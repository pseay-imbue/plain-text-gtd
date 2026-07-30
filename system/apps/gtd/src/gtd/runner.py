"""GTD web app — server-rendered Jinja2 views over plain-text item files.

See ``system/apps/gtd/README.md`` for the design and usage notes.

Routes live behind a ``/service/gtd/`` prefix in the workspace UI; FastAPI
emits prefix-aware URLs via the ``ROOT_PATH`` env var.  Standalone ``uv run
gtd`` serves at ``/``.
"""

import calendar
import os
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

import markdown as md
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from markupsafe import escape as html_escape

from gtd.data_types import (
    PRIORITY_SORT,
    PROJECT_CHILD_SORT,
    GtdError,
    Item,
    ItemStatus,
    Priority,
)
from gtd.storage import (
    all_items,
    children_of,
    delete_item,
    new_item_id,
    seed_defaults,
    read_instructions,
    read_intentions,
    read_item,
    read_projects_and_goals,
    read_weekly_review_prompt,
    write_instructions,
    write_item,
    write_projects_and_goals,
    write_weekly_review_prompt,
)

ROOT_PATH = os.environ.get("ROOT_PATH", "")

_ASSETS = Path(__file__).parent / "assets"
_TEMPLATES_DIR = _ASSETS / "templates"
_STATIC_DIR = _ASSETS / "static"

app = FastAPI(title="gtd", root_path=ROOT_PATH)
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/static/{path:path}", name="static", include_in_schema=False)
def serve_static(path: str) -> FileResponse:
    # Plain route instead of app.mount("/static", StaticFiles(...)) because
    # mounts get the root_path prefix baked into their effective URL, so
    # they only respond at /service/gtd/static/* on the backend; the
    # system_interface reverse proxy strips that prefix and forwards
    # /static/*, which the mount no longer matches. Regular routes match
    # at both the bare and prefixed paths.
    static_root = _STATIC_DIR.resolve()
    resolved = (static_root / path).resolve()
    if not resolved.is_relative_to(static_root) or not resolved.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(resolved)


@app.middleware("http")
async def _no_store_html(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Prevent the browser back/forward cache for HTML pages.

    Without this, navigating Today → Projects → back-to-Today restores a
    snapshot of the previous Today page rather than re-fetching, so edits
    you made on other tabs/views don't show up until a hard refresh.
    Cache-Control: no-store on HTML disables bfcache.
    """
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

# Matches either a full http(s) URL or a bare domain (optionally followed
# by a path). The first alternative wins so URLs with scheme are preferred.
_URL_OR_DOMAIN_RE = re.compile(
    r"(?:"
    r"https?://[^\s<>\"]+"
    r"|"
    r"(?<![\w@./-])"  # not preceded by word char, @, /, dot, or hyphen
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
    r"(?:/[^\s<>\"]*)?"
    r")",
    re.IGNORECASE,
)

# TLDs we accept for bare-domain autolinking. Restricted to keep
# `instructions.md`, `path/to/file.py`, `version 4.6`, etc. from looking
# like URLs. Add more here as new ones come up.
_AUTOLINK_TLDS = frozenset({
    "com", "org", "net", "io", "co", "app", "dev", "ai", "gov", "edu",
    "us", "uk", "de", "jp", "fr", "it", "es", "nl", "au", "ca", "in", "br", "ru",
    "info", "me", "xyz", "tv", "news", "blog", "tech", "social", "today",
    "world", "fyi", "so", "gg", "house", "press", "studio", "cloud",
    "site", "online", "store", "live", "art", "design", "media", "page",
})


def _autolink(text: str) -> str:
    """Wrap http(s) URLs and bare domains in <a> tags for the body preview.

    Escapes surrounding text. URLs without a scheme get https:// prepended
    in the href. Bare domains are accepted only when their TLD is in
    ``_AUTOLINK_TLDS`` so file paths like ``instructions.md`` don't get
    linked.
    """
    def _wrap(match: re.Match[str]) -> str:
        raw = match.group(0)
        # Trim common trailing punctuation that's almost never part of the URL.
        trailing = ""
        while raw and raw[-1] in ".,;:!?)]'\"":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not raw:
            return html_escape(trailing)

        if raw.lower().startswith(("http://", "https://")):
            href = raw
        else:
            # Bare domain: filter on TLD so we don't link random `foo.bar`.
            domain_part = raw.split("/", 1)[0]
            tld = domain_part.rsplit(".", 1)[-1].lower()
            if tld not in _AUTOLINK_TLDS:
                return f"{html_escape(raw)}{html_escape(trailing)}"
            href = f"https://{raw}"

        safe_href = html_escape(href)
        safe_label = html_escape(raw)
        return (
            f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{safe_label}</a>'
            f"{html_escape(trailing)}"
        )

    parts = []
    last = 0
    for match in _URL_OR_DOMAIN_RE.finditer(text):
        parts.append(str(html_escape(text[last:match.start()])))
        parts.append(_wrap(match))
        last = match.end()
    parts.append(str(html_escape(text[last:])))
    return "".join(parts)


_templates.env.filters["autolink"] = _autolink


_WEEKDAY_SHORT = ["M", "T", "W", "T", "F", "S", "S"]
_WEEKDAY_LABEL = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _weekday_short(d: object) -> str:
    try:
        return _WEEKDAY_SHORT[int(d)]
    except (ValueError, IndexError, TypeError):
        return "?"


def _weekday_label(d: object) -> str:
    try:
        return _WEEKDAY_LABEL[int(d)]
    except (ValueError, IndexError, TypeError):
        return "?"


_templates.env.filters["weekday_short"] = _weekday_short
_templates.env.filters["weekday_label"] = _weekday_label


def _now() -> datetime:
    return datetime.now()


def _today() -> date:
    return date.today()


def _instructions_html() -> str:
    raw = read_instructions()
    if not raw:
        return "<p><em>Add triage rules to <code>data/.apps/gtd/instructions.md</code>.</em></p>"
    return md.markdown(raw, extensions=["extra", "sane_lists"])


def _body_html(text: str) -> str:
    if not text:
        return ""
    return md.markdown(text, extensions=["extra", "sane_lists"])


def _intentions_html() -> str:
    return _body_html(read_intentions())


def _status_counts(items: list[Item]) -> dict[str, int]:
    """Counts for the nav. status-based for inbox/next/waiting/someday/etc.,
    plus derived counts for the views that aren't direct status filters."""
    counts: dict[str, int] = {status.value.replace("-", "_"): 0 for status in ItemStatus}
    today = _today()
    project_count = 0
    tickler_count = 0
    for item in items:
        counts[item.status.value.replace("-", "_")] += 1
        if (
            item.is_project
            and item.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
        ):
            project_count += 1
        if (
            item.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
            and item.start_date is not None
            and item.start_date > today
        ):
            tickler_count += 1
    counts["project"] = project_count
    counts["tickler"] = tickler_count
    # Next-view count should exclude projects (they're not in Next).
    next_real = 0
    for item in items:
        if (
            item.status == ItemStatus.NEXT
            and not item.is_project
            # Exclude items deferred to the future — they're in Tickler.
            and (item.start_date is None or item.start_date <= today)
        ):
            next_real += 1
    counts["next"] = next_real
    return counts


def _parent_lookup(items: Iterable[Item]) -> dict[str, Item]:
    return {item.id: item for item in items if item.is_project}


def _project_child_sort_key(item: Item) -> tuple[int, int, int, int, date, datetime]:
    """Sort key for items shown grouped under a project.

    Same priority/quick/due_date/created ordering as the Next view, with a
    leading status bucket so active actions stay above waiting/deferred ones
    and done items sink to the bottom.
    """
    return (
        PROJECT_CHILD_SORT.get(item.status, 99),
        PRIORITY_SORT[item.priority],
        0 if item.quick else 1,
        0 if item.due_date else 1,
        item.due_date or date.max,
        item.created_at,
    )


def _render(
    request: Request,
    template: str,
    *,
    active: str | None = None,
    items_snapshot: list[Item] | None = None,
    **extra: object,
) -> HTMLResponse:
    snapshot = items_snapshot if items_snapshot is not None else list(all_items())
    counts = _status_counts(snapshot)
    parent_lookup = _parent_lookup(snapshot)
    done_items = sorted(
        (item for item in snapshot if item.status == ItemStatus.DONE),
        key=lambda item: item.updated_at,
        reverse=True,
    )
    # Only active projects are offered as "move to project" picker options;
    # done/trashed projects stay in parent_lookup (so a child can still render
    # a retired parent's name) but must not be assignable targets.
    all_projects = sorted(
        (
            p
            for p in parent_lookup.values()
            if p.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
        ),
        key=lambda p: p.title.lower(),
    )
    child_counts: dict[str, int] = {}
    for it in snapshot:
        if it.parent and it.status not in (ItemStatus.DONE, ItemStatus.TRASHED):
            child_counts[it.parent] = child_counts.get(it.parent, 0) + 1
    base_ctx: dict[str, object] = {
        "active": active,
        "counts": counts,
        "all_count": len(snapshot),
        # True on a brand-new system (no items on file at all) so views can
        # show first-run onboarding instead of terse empty states.
        "is_fresh": len(snapshot) == 0,
        "today": _today(),
        "instructions_html": _instructions_html(),
        "parent_lookup": parent_lookup,
        "all_projects": all_projects,
        "all_statuses": list(ItemStatus),
        "all_priorities": list(Priority),
        "child_counts": child_counts,
        # Shared option lists for the row pickers. Emitted once per page (see
        # base.html) and used to build each dropdown panel lazily on open,
        # instead of inlining every option in every row (which bloated the DOM).
        "picker_data": {
            "status": [s.value for s in ItemStatus],
            "priority": [p.value for p in Priority],
            "projects": [{"id": p.id, "title": p.title} for p in all_projects],
        },
        # Include ROOT_PATH so back-links (?back=<current_path>) survive the
        # round-trip through the system_interface reverse proxy. Without the
        # prefix, the browser resolves the redirect against the host root,
        # which lands outside the gtd service — the desktop client then
        # treats the unknown route as a fallback to its agent view.
        "current_path": f"{ROOT_PATH}{request.url.path}",
        "done_items": done_items,
        "url": _url_builder(request),
        "static_url": _static_url_builder(request),
    }
    base_ctx.update(extra)
    return _templates.TemplateResponse(request=request, name=template, context=base_ctx)


def _url_builder(request: Request) -> Callable[..., str]:
    """Emit path-only URLs so the browser resolves against whatever origin
    it's on (workspace proxy, Cloudflare tunnel, or direct). Absolute URLs
    embedded localhost:8082 which broke every link and form action when
    the page was loaded through the workspace_server proxy."""

    def build(name: str, **params: object) -> str:
        return request.url_for(name, **params).path

    return build


def _static_url_builder(request: Request) -> Callable[[str], str]:
    def build(path: str) -> str:
        version = int((_STATIC_DIR / path).stat().st_mtime)
        return f"{request.url_for('static', path=path).path}?v={version}"

    return build


def _split_capture_block(block: str) -> list[str]:
    titles: list[str] = []
    for raw_line in block.splitlines():
        cleaned = _BULLET_RE.sub("", raw_line).strip()
        if cleaned:
            titles.append(cleaned)
    return titles


def _parse_optional_date(value: str) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _list_view(
    request: Request,
    *,
    status: ItemStatus,
    active: str,
    heading_html: str,
    heading_text: str,
    count_label: str,
    blurb: str,
    empty_text: str,
    sort_key: Callable[[Item], object] | None = None,
) -> HTMLResponse:
    snapshot = list(all_items())
    items = [item for item in snapshot if item.status == status]
    if sort_key is not None:
        items.sort(key=sort_key)
    else:
        items.sort(key=lambda item: (PRIORITY_SORT[item.priority], -item.created_at.timestamp()))
    return _render(
        request,
        "list_view.html",
        active=active,
        items_snapshot=snapshot,
        items=items,
        heading_html=heading_html,
        heading_text=heading_text,
        count_label=count_label,
        blurb=blurb,
        empty_text=empty_text,
    )


def _render_not_found(request: Request, item_id: str) -> HTMLResponse:
    snapshot = list(all_items())
    return _render(
        request,
        "list_view.html",
        active=None,
        items_snapshot=snapshot,
        items=[],
        heading_html="Not found",
        heading_text="Not found",
        count_label="",
        blurb=f"No item with id {item_id!r}.",
        empty_text="Try the Inbox or Projects view.",
    )


@app.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    # Path-only Location (with root_path prefix) so the browser resolves
    # against its own host, not the backend's localhost:8082 which would be
    # unreachable from a remote client behind the system_interface proxy.
    return RedirectResponse(url=request.url_for("today").path)


@app.get("/inbox", name="inbox", response_class=HTMLResponse)
def view_inbox(request: Request) -> HTMLResponse:
    snapshot = list(all_items())
    items = sorted(
        (it for it in snapshot if it.status == ItemStatus.INBOX),
        key=lambda item: (PRIORITY_SORT[item.priority], -item.created_at.timestamp()),
    )
    return _render(
        request,
        "inbox.html",
        active="inbox",
        items_snapshot=snapshot,
        items=items,
    )


@app.get("/next", name="next", response_class=HTMLResponse)
def view_next(request: Request) -> HTMLResponse:
    def sort_key(item: Item) -> tuple[int, int, int, date, datetime]:
        return (
            PRIORITY_SORT[item.priority],
            0 if item.quick else 1,
            0 if item.due_date else 1,
            item.due_date or date.max,
            item.created_at,
        )

    snapshot = list(all_items())
    today = _today()
    next_tasks = [
        item
        for item in snapshot
        if item.status == ItemStatus.NEXT
        and not item.is_project
        # Future-start items live in Tickler, not Next.
        and (item.start_date is None or item.start_date <= today)
    ]
    next_tasks.sort(key=sort_key)

    project_by_id: dict[str, Item] = {it.id: it for it in snapshot if it.is_project}
    grouped_by_parent: dict[str, list[Item]] = {}
    standalone: list[Item] = []
    for task in next_tasks:
        if task.parent and task.parent in project_by_id:
            grouped_by_parent.setdefault(task.parent, []).append(task)
        else:
            standalone.append(task)

    project_groups: list[tuple[Item, list[Item]]] = []
    for parent_id, tasks in grouped_by_parent.items():
        parent = project_by_id[parent_id]
        project_groups.append((parent, tasks))
    project_groups.sort(
        key=lambda pg: (PRIORITY_SORT[pg[0].priority], pg[0].title.lower())
    )

    return _render(
        request,
        "next.html",
        active="next",
        items_snapshot=snapshot,
        standalone=standalone,
        project_groups=project_groups,
    )


@app.get("/waiting", name="waiting", response_class=HTMLResponse)
def view_waiting(request: Request) -> HTMLResponse:
    return _list_view(
        request,
        status=ItemStatus.WAITING,
        active="waiting",
        heading_html="<em>Waiting</em> For",
        heading_text="Waiting For",
        count_label="open",
        blurb="Things blocked on someone or something else. Nudge as needed.",
        empty_text="Nothing on hold.",
    )


@app.get("/tickler", name="tickler", response_class=HTMLResponse)
def view_tickler(request: Request) -> HTMLResponse:
    """Items whose start_date is in the future — deferred until then."""
    snapshot = list(all_items())
    today = _today()
    hidden = (ItemStatus.DONE, ItemStatus.TRASHED)
    items = sorted(
        (
            it
            for it in snapshot
            if it.status not in hidden
            and it.start_date is not None
            and it.start_date > today
        ),
        key=lambda it: (it.start_date or today, PRIORITY_SORT[it.priority], it.title.lower()),
    )
    return _render(
        request,
        "list_view.html",
        active="tickler",
        items_snapshot=snapshot,
        items=items,
        heading_html="<em>Tickler</em>",
        heading_text="Tickler",
        count_label="deferred",
        blurb="Items with a start date in the future. They live here until their day arrives.",
        empty_text="Nothing deferred.",
    )


@app.get("/someday", name="someday", response_class=HTMLResponse)
def view_someday(request: Request) -> HTMLResponse:
    snapshot = list(all_items())
    someday_projects = sorted(
        (it for it in snapshot if it.is_project and it.status == ItemStatus.SOMEDAY),
        key=lambda it: (PRIORITY_SORT[it.priority], it.title.lower()),
    )
    project_groups: list[tuple[Item, list[Item]]] = []
    for project in someday_projects:
        children = sorted(
            (
                it
                for it in snapshot
                if it.parent == project.id
                and it.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
            ),
            key=_project_child_sort_key,
        )
        project_groups.append((project, children))
    standalone_tasks = sorted(
        (
            it
            for it in snapshot
            if it.status == ItemStatus.SOMEDAY
            and not it.is_project
            and not it.parent
        ),
        key=lambda it: (PRIORITY_SORT[it.priority], -it.created_at.timestamp()),
    )
    return _render(
        request,
        "someday.html",
        active="someday",
        items_snapshot=snapshot,
        project_groups=project_groups,
        standalone_tasks=standalone_tasks,
    )


@app.get("/read", name="read", response_class=HTMLResponse)
def view_read(request: Request) -> HTMLResponse:
    return _list_view(
        request,
        status=ItemStatus.READ_REVIEW,
        active="read",
        heading_html="Read &amp; <em>Review</em>",
        heading_text="Read & Review",
        count_label="to consume",
        blurb="Articles, videos, papers, books to work through. Send to Readwise when ready.",
        empty_text="Reading queue is empty.",
    )


@app.get("/reference", name="reference", response_class=HTMLResponse)
def view_reference(request: Request) -> HTMLResponse:
    return _list_view(
        request,
        status=ItemStatus.REFERENCE,
        active="reference",
        heading_html="<em>Reference</em>",
        heading_text="Reference",
        count_label="filed",
        blurb="Information to keep handy. Not actionable, just useful.",
        empty_text="Nothing filed.",
    )


@app.get("/projects", name="projects", response_class=HTMLResponse)
def view_projects(request: Request) -> HTMLResponse:
    snapshot = list(all_items())

    def children_for(project_id: str) -> list[Item]:
        return sorted(
            (
                it
                for it in snapshot
                if it.parent == project_id
                and it.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
            ),
            key=_project_child_sort_key,
        )

    active_projects = [
        it
        for it in snapshot
        if it.is_project
        and it.status not in (ItemStatus.DONE, ItemStatus.TRASHED, ItemStatus.SOMEDAY)
    ]
    active_projects.sort(key=lambda p: (PRIORITY_SORT[p.priority], p.title.lower()))
    someday_projects = sorted(
        (it for it in snapshot if it.is_project and it.status == ItemStatus.SOMEDAY),
        key=lambda p: (PRIORITY_SORT[p.priority], p.title.lower()),
    )
    next_project_groups: list[tuple[Item, list[Item]]] = [
        (p, children_for(p.id)) for p in active_projects
    ]
    someday_project_groups: list[tuple[Item, list[Item]]] = [
        (p, children_for(p.id)) for p in someday_projects
    ]
    return _render(
        request,
        "projects.html",
        active="projects",
        items_snapshot=snapshot,
        next_project_groups=next_project_groups,
        someday_project_groups=someday_project_groups,
    )


@app.post("/projects/{item_id}/add-child", name="add_child")
def add_child_route(
    request: Request,
    item_id: str,
    title: str = Form(...),
) -> RedirectResponse:
    cleaned = title.strip()
    if cleaned:
        now = _now()
        write_item(
            Item(
                id=new_item_id(),
                title=cleaned[:300],
                status=ItemStatus.NEXT,
                created_at=now,
                updated_at=now,
                parent=item_id,
            )
        )
    return RedirectResponse(
        url=str(request.url_for("project_detail", item_id=item_id).path),
        status_code=303,
    )


@app.get("/projects/{item_id}", name="project_detail", response_class=HTMLResponse)
def view_project_detail(request: Request, item_id: str) -> HTMLResponse:
    snapshot = list(all_items())
    project = next((it for it in snapshot if it.id == item_id), None)
    if project is None or not project.is_project:
        return _render_not_found(request, item_id)
    children = sorted(
        (it for it in snapshot if it.parent == project.id),
        key=_project_child_sort_key,
    )
    return _render(
        request,
        "project_detail.html",
        active="projects",
        items_snapshot=snapshot,
        project=project,
        children=children,
        body_html=_body_html(project.body),
    )


@app.get("/all", name="all_items", response_class=HTMLResponse)
def view_all(
    request: Request,
    q: str = "",
    status: str = "",
    priority: str = "",
    kind: str = "",
) -> HTMLResponse:
    snapshot = list(all_items())
    needle = q.strip().lower()
    results = []
    for item in snapshot:
        if item.status == ItemStatus.TRASHED:
            continue
        if status and item.status.value != status:
            continue
        if priority and item.priority.value != priority:
            continue
        if kind == "project" and not item.is_project:
            continue
        if kind == "task" and item.is_project:
            continue
        if needle and needle not in item.title.lower() and needle not in item.body.lower():
            continue
        results.append(item)
    results.sort(
        key=lambda it: (
            PRIORITY_SORT[it.priority],
            0 if it.status != ItemStatus.DONE else 1,
            -it.created_at.timestamp(),
        )
    )
    # Group results into projects-with-children plus standalone tasks.
    result_ids = {it.id for it in results}
    matched_children_by_parent: dict[str, list[Item]] = {}
    for it in results:
        if it.parent and not it.is_project:
            matched_children_by_parent.setdefault(it.parent, []).append(it)
    project_ids_to_show: set[str] = set()
    for it in results:
        if it.is_project:
            project_ids_to_show.add(it.id)
        if it.parent and it.parent not in result_ids:
            project_ids_to_show.add(it.parent)
    project_groups: list[tuple[Item, list[Item]]] = []
    shown_child_ids: set[str] = set()
    for project in sorted(
        (it for it in snapshot if it.id in project_ids_to_show and it.is_project),
        key=lambda p: (PRIORITY_SORT[p.priority], p.title.lower()),
    ):
        children = sorted(
            matched_children_by_parent.get(project.id, []),
            key=_project_child_sort_key,
        )
        project_groups.append((project, children))
        shown_child_ids.update(child.id for child in children)
    # Loose list = every matched non-project item not already shown under a
    # project group. This deliberately includes orphans — items whose parent
    # project was deleted (or whose parent isn't a project) — so a match is
    # never silently dropped from the page just because its group is gone.
    standalone = [
        it for it in results if not it.is_project and it.id not in shown_child_ids
    ]
    return _render(
        request,
        "all.html",
        active="all",
        items_snapshot=snapshot,
        results=results,
        project_groups=project_groups,
        standalone=standalone,
        filter_q=q,
        filter_status=status,
        filter_priority=priority,
        filter_kind=kind,
    )


@app.get("/today", name="today", response_class=HTMLResponse)
def view_today(request: Request) -> HTMLResponse:
    snapshot = list(all_items())
    today = _today()
    hidden = (ItemStatus.DONE, ItemStatus.TRASHED)
    due_today = sorted(
        (it for it in snapshot if it.status not in hidden and it.due_date == today),
        key=lambda it: (PRIORITY_SORT[it.priority], it.title.lower()),
    )
    starting_today = sorted(
        (
            it
            for it in snapshot
            if it.status not in hidden
            and it.start_date == today
            and it.due_date != today
        ),
        key=lambda it: (PRIORITY_SORT[it.priority], it.title.lower()),
    )
    # In progress: started in the past, not due today, due hasn't passed
    # yet (or no due date at all). These are items you've already committed
    # to working on but haven't finished — they should stay visible on
    # Today until they're done or actually overdue.
    in_progress = sorted(
        (
            it
            for it in snapshot
            if it.status not in hidden
            and it.start_date is not None
            and it.start_date < today
            and it.due_date != today
            and (it.due_date is None or it.due_date > today)
        ),
        key=lambda it: (PRIORITY_SORT[it.priority], it.start_date or today, it.title.lower()),
    )
    # Past-due: anything genuinely overdue (due_date in the past).
    past_due = sorted(
        (
            it
            for it in snapshot
            if it.status not in hidden
            and it.due_date is not None
            and it.due_date < today
        ),
        key=lambda it: (
            it.due_date or today,
            PRIORITY_SORT[it.priority],
            it.title.lower(),
        ),
    )
    return _render(
        request,
        "today.html",
        active="today",
        items_snapshot=snapshot,
        intentions_html=_intentions_html(),
        due_today=due_today,
        starting_today=starting_today,
        in_progress=in_progress,
        past_due=past_due,
    )


@app.get("/calendar", name="calendar", response_class=HTMLResponse)
def view_calendar_today(request: Request) -> HTMLResponse:
    today = _today()
    return _render_calendar(request, today.year, today.month)


@app.get("/calendar/{year}/{month}", name="calendar_for", response_class=HTMLResponse)
def view_calendar_for(request: Request, year: int, month: int) -> HTMLResponse:
    if not (1 <= month <= 12):
        raise GtdError(f"Bad month: {month}")
    return _render_calendar(request, year, month)


def _render_calendar(request: Request, year: int, month: int) -> HTMLResponse:
    snapshot = list(all_items())
    visible = [
        it
        for it in snapshot
        if it.status not in (ItemStatus.DONE, ItemStatus.TRASHED)
    ]

    cal = calendar.Calendar(firstweekday=0)
    today = _today()
    url = _url_builder(request)
    cells: list[dict[str, object]] = []
    for cell_date in cal.itermonthdates(year, month):
        chips = _chips_for_day(cell_date, visible, url)
        cells.append(
            {
                "day": cell_date.day,
                "in_month": cell_date.month == month,
                "is_today": cell_date == today,
                "chips": chips,
            }
        )

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return _render(
        request,
        "calendar.html",
        active="calendar",
        items_snapshot=snapshot,
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        day_names=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        cells=cells,
        prev_year=prev_year,
        prev_month=prev_month,
        prev_name=calendar.month_abbr[prev_month],
        next_year=next_year,
        next_month=next_month,
        next_name=calendar.month_abbr[next_month],
    )


def _chips_for_day(
    cell_date: date,
    items: list[Item],
    url: Callable[..., str],
) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    for item in items:
        kind: str | None = None
        if item.due_date == cell_date:
            kind = "due"
        elif item.start_date == cell_date:
            kind = "span"
        if kind is None:
            continue
        if item.is_project:
            href = url("project_detail", item_id=item.id)
        else:
            href = url("edit_item", item_id=item.id)
        chips.append({"kind": kind, "title": item.title, "href": href})
    return chips


@app.post("/items", name="create_items")
def create_items(request: Request, block: str = Form(...)) -> RedirectResponse:
    now = _now()
    for title in _split_capture_block(block):
        item = Item(
            id=new_item_id(),
            title=title[:300],
            status=ItemStatus.INBOX,
            created_at=now,
            updated_at=now,
        )
        write_item(item)
    return RedirectResponse(url=request.url_for("inbox").path, status_code=303)


@app.get("/items/{item_id}/edit", name="edit_item", response_class=HTMLResponse)
def edit_item_route(request: Request, item_id: str, back: str = "") -> HTMLResponse:
    snapshot = list(all_items())
    item = next((it for it in snapshot if it.id == item_id), None)
    if item is None:
        return _render_not_found(request, item_id)
    projects = sorted(
        (it for it in snapshot if it.is_project and it.id != item.id),
        key=lambda p: p.title.lower(),
    )
    back_url = back or request.headers.get("referer") or request.url_for("inbox").path
    return _render(
        request,
        "edit.html",
        active=None,
        items_snapshot=snapshot,
        item=item,
        projects=projects,
        statuses=list(ItemStatus),
        back_url=back_url,
    )


@app.post("/items/{item_id}", name="save_item")
def save_item(
    request: Request,
    item_id: str,
    title: str = Form(...),
    status: str = Form(...),
    due_date: str = Form(""),
    start_date: str = Form(""),
    waiting_for: str = Form(""),
    parent: str = Form(""),
    tags: str = Form(""),
    quick: str = Form(""),
    is_project: str = Form(""),
    priority: str = Form("normal"),
    recur_weekdays: list[str] = Form(default_factory=list),
    body: str = Form(""),
    back: str = Form(""),
) -> RedirectResponse:
    existing = read_item(item_id)
    tag_list = tuple(t.strip() for t in tags.split(",") if t.strip())
    recur = tuple(sorted({int(d) for d in recur_weekdays if d.isdigit() and 0 <= int(d) <= 6}))
    new_status = ItemStatus(status)
    updated = Item(
        id=existing.id,
        title=title.strip() or existing.title,
        status=new_status,
        created_at=existing.created_at,
        updated_at=_now(),
        body=body,
        due_date=_parse_optional_date(due_date),
        start_date=_parse_optional_date(start_date),
        waiting_for=waiting_for.strip() or None,
        parent=parent.strip() or None,
        contexts=existing.contexts,
        tags=tag_list,
        quick=bool(quick),
        is_project=bool(is_project),
        priority=Priority(priority),
        recur_weekdays=recur,
    )
    write_item(updated)
    _cascade_project_status(existing, new_status)
    return RedirectResponse(url=back or request.url_for("inbox").path, status_code=303)


@app.post("/items/{item_id}/delete", name="delete_item")
def delete_item_route(
    request: Request,
    item_id: str,
    back: str = Form(""),
) -> RedirectResponse:
    delete_item(item_id)
    target = back or request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=target, status_code=303)


@app.post("/items/{item_id}/title", name="rename_item")
def rename_item_route(item_id: str, title: str = Form(...)) -> Response:
    existing = read_item(item_id)
    cleaned = title.strip() or existing.title
    updated = existing.model_copy(update={"title": cleaned, "updated_at": _now()})
    write_item(updated)
    return Response(status_code=204)


@app.post("/items/{item_id}/body", name="save_body", response_class=HTMLResponse)
def save_body_route(item_id: str, body: str = Form("")) -> HTMLResponse:
    """Save a new body for an item and return its rendered HTML.

    Used by the click-to-edit body widget on the project detail page; the
    response payload is dropped straight into the body view's innerHTML.
    """
    existing = read_item(item_id)
    updated = existing.model_copy(update={"body": body, "updated_at": _now()})
    write_item(updated)
    return HTMLResponse(_body_html(updated.body))


@app.post("/items/{item_id}/status", name="set_status")
def set_status_route(
    request: Request,
    item_id: str,
    status: str = Form(...),
) -> RedirectResponse:
    existing = read_item(item_id)
    new_status = ItemStatus(status)
    updated = existing.model_copy(update={"status": new_status, "updated_at": _now()})
    write_item(updated)
    _cascade_project_status(existing, new_status)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


def _cascade_project_status(project: Item, new_status: ItemStatus) -> None:
    """If a project moves to/from Someday, cascade the status to its children.

    On the way to Someday, each affected child saves its current status into
    ``prev_status`` so the round-trip back to Next restores the exact prior
    bucket (e.g. a child that was ``waiting`` returns to ``waiting``, not
    blindly to ``next``).
    """
    if not project.is_project:
        return
    old_status = project.status
    if old_status == new_status:
        return
    children = children_of(project.id)
    if new_status == ItemStatus.SOMEDAY and old_status != ItemStatus.SOMEDAY:
        for child in children:
            if child.status in (ItemStatus.DONE, ItemStatus.TRASHED, ItemStatus.SOMEDAY):
                continue
            updated = child.model_copy(
                update={
                    "status": ItemStatus.SOMEDAY,
                    "prev_status": child.status,
                    "updated_at": _now(),
                }
            )
            write_item(updated)
    elif old_status == ItemStatus.SOMEDAY and new_status != ItemStatus.SOMEDAY:
        # Reactivating the project pulls every someday child along; if the
        # child has a saved prev_status (it was cascaded down by us), restore
        # that exact bucket — otherwise default to Next.
        for child in children:
            if child.status != ItemStatus.SOMEDAY:
                continue
            restore_to = child.prev_status or ItemStatus.NEXT
            updated = child.model_copy(
                update={
                    "status": restore_to,
                    "prev_status": None,
                    "updated_at": _now(),
                }
            )
            write_item(updated)


@app.post("/items/{item_id}/parent", name="set_parent")
def set_parent_route(
    request: Request,
    item_id: str,
    parent: str = Form(""),
) -> RedirectResponse:
    existing = read_item(item_id)
    updated = existing.model_copy(
        update={"parent": parent.strip() or None, "updated_at": _now()}
    )
    write_item(updated)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


@app.post("/items/{item_id}/due-date", name="set_due_date")
def set_due_date_route(
    request: Request,
    item_id: str,
    due_date: str = Form(""),
) -> RedirectResponse:
    existing = read_item(item_id)
    updated = existing.model_copy(
        update={"due_date": _parse_optional_date(due_date), "updated_at": _now()}
    )
    write_item(updated)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


@app.post("/items/{item_id}/start-date", name="set_start_date")
def set_start_date_route(
    request: Request,
    item_id: str,
    start_date: str = Form(""),
) -> RedirectResponse:
    existing = read_item(item_id)
    updated = existing.model_copy(
        update={"start_date": _parse_optional_date(start_date), "updated_at": _now()}
    )
    write_item(updated)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


@app.post("/items/{item_id}/priority", name="set_priority")
def set_priority_route(
    request: Request,
    item_id: str,
    priority: str = Form(...),
) -> RedirectResponse:
    existing = read_item(item_id)
    updated = existing.model_copy(
        update={"priority": Priority(priority), "updated_at": _now()}
    )
    write_item(updated)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


@app.post("/items/{item_id}/recur", name="set_recur")
def set_recur_route(
    request: Request,
    item_id: str,
    recur_weekdays: list[str] = Form(default_factory=list),
) -> Response:
    """Update only the recur_weekdays field — used by the inline picker."""
    existing = read_item(item_id)
    recur = tuple(sorted({int(d) for d in recur_weekdays if d.isdigit() and 0 <= int(d) <= 6}))
    updated = existing.model_copy(update={"recur_weekdays": recur, "updated_at": _now()})
    write_item(updated)
    return Response(status_code=204)


@app.post("/items/{item_id}/check", name="check_item")
def check_item_route(
    request: Request,
    item_id: str,
    checked: str = Form(""),
) -> RedirectResponse:
    existing = read_item(item_id)
    if checked:
        # Recurring items don't go done — they bounce to their next weekday
        # and stay in their existing status (typically next). If today is
        # already past the recurrence window, just defer to the next match
        # ("skip the miss, don't catch up").
        if existing.recur_weekdays:
            next_date = _next_recurrence_after(_today(), tuple(existing.recur_weekdays))
            if next_date is not None:
                updated = existing.model_copy(
                    update={"start_date": next_date, "updated_at": _now()}
                )
                write_item(updated)
                referer = request.headers.get("referer") or request.url_for("inbox").path
                return RedirectResponse(url=referer, status_code=303)
        new_status = ItemStatus.DONE
    else:
        # Box unchecked -> restore to inbox so the user can re-classify.
        new_status = ItemStatus.INBOX
    updated = existing.model_copy(update={"status": new_status, "updated_at": _now()})
    write_item(updated)
    referer = request.headers.get("referer") or request.url_for("inbox").path
    return RedirectResponse(url=referer, status_code=303)


def _next_recurrence_after(today: date, weekdays: tuple[int, ...]) -> date | None:
    """Return the first date strictly after ``today`` that falls on one of
    the given weekdays (Mon=0…Sun=6). Returns None if no weekdays given."""
    if not weekdays:
        return None
    for offset in range(1, 8):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate
    return None  # unreachable for any non-empty subset of 0..6


@app.get("/api/triage/{item_id}", name="api_triage", response_class=PlainTextResponse)
def api_triage(item_id: str) -> str:
    item = read_item(item_id)
    return (
        f"Triage {item.id}: {item.title}\n\n"
        f"(Read data/.apps/gtd/items/{item.id}.md for full context, then walk me "
        f"through it per data/.apps/gtd/instructions.md.)\n"
    )


@app.get("/api/handoff/{item_id}", name="api_handoff", response_class=PlainTextResponse)
def api_handoff(item_id: str) -> str:
    item = read_item(item_id)
    return _handoff_block(item)


def _handoff_block(item: Item) -> str:
    lines: list[str] = [f"# GTD item {item.id}: {item.title}", ""]
    lines.append(f"- Status: {item.status.value}")
    if item.due_date:
        lines.append(f"- Due: {item.due_date.isoformat()}")
    if item.start_date:
        lines.append(f"- Start: {item.start_date.isoformat()}")
    if item.waiting_for:
        lines.append(f"- Waiting on: {item.waiting_for}")
    if item.parent:
        lines.append(f"- Parent project: {item.parent}")
    if item.tags:
        lines.append(f"- Tags: {', '.join(item.tags)}")
    if item.contexts:
        lines.append(f"- Contexts: {', '.join(item.contexts)}")
    if item.body:
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        lines.append(item.body)
    lines.append("")
    return "\n".join(lines)


@app.get("/api/plan/{item_id}", name="api_plan", response_class=PlainTextResponse)
def api_plan(item_id: str) -> str:
    project = read_item(item_id)
    children = sorted(children_of(project.id), key=lambda c: c.created_at)
    lines: list[str] = [f"# Plan project {project.id}: {project.title}", ""]
    if project.due_date:
        lines.append(f"- Due: {project.due_date.isoformat()}")
    if project.tags:
        lines.append(f"- Tags: {', '.join(project.tags)}")
    if project.body:
        lines.append("")
        lines.append("## Project notes")
        lines.append("")
        lines.append(project.body)
    lines.append("")
    lines.append(f"## Sub-actions ({len(children)})")
    lines.append("")
    if children:
        for child in children:
            mark = "x" if child.status == ItemStatus.DONE else " "
            extras: list[str] = [f"`{child.id}`", f"status: {child.status.value}"]
            if child.due_date:
                extras.append(f"due {child.due_date.isoformat()}")
            if child.waiting_for:
                extras.append(f"waiting on {child.waiting_for}")
            lines.append(f"- [{mark}] {child.title}  ({', '.join(extras)})")
            if child.body:
                indented = "\n".join("    " + line for line in child.body.splitlines())
                lines.append(indented)
    else:
        lines.append("_No sub-actions yet._")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Help me plan this project. What's the next physical action? What's "
        "the desired outcome? What am I forgetting?"
    )
    lines.append("")
    return "\n".join(lines)


@app.get("/instructions", name="instructions_page", response_class=HTMLResponse)
def instructions_page(request: Request) -> HTMLResponse:
    return _render(
        request,
        "instructions.html",
        active="instructions",
        prompt_text=read_instructions(),
    )


@app.post("/instructions", name="save_instructions")
def save_instructions_route(request: Request, body: str = Form(...)) -> RedirectResponse:
    write_instructions(body)
    return RedirectResponse(url=request.url_for("instructions_page").path, status_code=303)


@app.get("/weekly-review", name="weekly_review", response_class=HTMLResponse)
def weekly_review_page(request: Request) -> HTMLResponse:
    return _render(
        request,
        "weekly_review.html",
        active="weekly_review",
        prompt_text=read_weekly_review_prompt(),
    )


@app.post("/weekly-review", name="save_weekly_review")
def save_weekly_review(request: Request, body: str = Form(...)) -> RedirectResponse:
    write_weekly_review_prompt(body)
    return RedirectResponse(url=request.url_for("weekly_review").path, status_code=303)


@app.get("/projects-and-goals", name="projects_and_goals_page", response_class=HTMLResponse)
def projects_and_goals_page(request: Request) -> HTMLResponse:
    prompt_text = read_projects_and_goals()
    return _render(
        request,
        "projects_and_goals.html",
        active="projects_and_goals",
        prompt_text=prompt_text,
        prompt_html=_body_html(prompt_text),
    )


@app.get("/projects-and-goals/editor", name="projects_and_goals_editor", response_class=HTMLResponse)
def projects_and_goals_editor(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        "_projects_and_goals_edit.html",
        {"prompt_text": read_projects_and_goals(), "url": _url_builder(request)},
    )


@app.post("/projects-and-goals", name="save_projects_and_goals")
def save_projects_and_goals(request: Request, body: str = Form(...)) -> Response:
    write_projects_and_goals(body)
    if request.headers.get("HX-Request") == "true":
        return _templates.TemplateResponse(
            request,
            "_projects_and_goals_view.html",
            {
                "prompt_text": body,
                "prompt_html": _body_html(body),
                "url": _url_builder(request),
            },
        )
    return RedirectResponse(url=request.url_for("projects_and_goals_page").path, status_code=303)


@app.get("/api/weekly-review", name="api_weekly_review", response_class=PlainTextResponse)
def api_weekly_review() -> str:
    prompt = read_weekly_review_prompt()
    if not prompt:
        return "Weekly review prompt not configured. Edit data/.apps/gtd/weekly_review_prompt.md.\n"
    return prompt


@app.get("/api/triage-inbox", name="api_triage_inbox", response_class=PlainTextResponse)
def api_triage_inbox() -> str:
    inbox = [item for item in all_items() if item.status == ItemStatus.INBOX]
    lines: list[str] = [
        "Help me do a first-pass triage of my GTD inbox.",
        "",
        "Read data/.apps/gtd/instructions.md first so you have the latest rules.",
        "",
        "For each item:",
        "",
        "1. Read its file under data/.apps/gtd/items/<id>.md for full context.",
        "2. Propose a classification (next, waiting, someday, read-review, "
        "reference, or trashed; mark is_project: true if it has children).",
        "3. Suggest a clearer, more executable phrasing of the title if the "
        "current one is vague.",
        "4. Ask me anything you need to decide. For vague items (like "
        "\"figure out the company\") do not classify without my answers — "
        "flag them and we'll think through them together.",
        "",
        "After we agree on each item, update its file in place: set the new "
        "status, rename the title if we improved it, and append a dated note "
        "to the body capturing what we decided.",
        "",
        f"## Inbox right now ({len(inbox)} items)",
        "",
    ]
    if not inbox:
        lines.append("_Empty._")
    else:
        for item in inbox:
            lines.append(f"- `{item.id}` — {item.title}")
    lines.append("")
    lines.append("Do all items together — give me your proposals for all of them in one message.")
    lines.append("")
    return "\n".join(lines)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


PORT = int(os.environ.get("GTD_PORT", "8082"))


def main() -> None:
    seed_defaults()
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
