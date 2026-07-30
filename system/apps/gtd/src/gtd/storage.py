"""Filesystem storage for GTD items, instructions, and the weekly-review prompt.

Items live as one markdown file per item under ``<root>/items/<id>.md``.  Each
file has YAML frontmatter (parsed by python-frontmatter) for metadata and a
free-form markdown body for notes. ``<root>`` defaults to ``data/.apps/gtd`` so
files live with the rest of the workspace data and are captured by the host
backup and survive container loss.
"""

import os
import re
import secrets
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import frontmatter

from gtd.data_types import GtdValueError, Item, ItemStatus, Priority

DEFAULT_ROOT = Path("data/.apps/gtd")
GTD_ROOT_ENV_VAR = "GTD_ROOT"
_ID_PATTERN = re.compile(r"^gtd-[a-z0-9]+$")

# Starter files shipped with the app, seeded into a fresh root on first boot.
_DEFAULTS_DIR = Path(__file__).parent / "assets" / "defaults"


def _resolved_root(root: Path | None) -> Path:
    if root is not None:
        return root
    override = os.environ.get(GTD_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return DEFAULT_ROOT


def items_dir(root: Path | None = None) -> Path:
    return _resolved_root(root) / "items"


def instructions_path(root: Path | None = None) -> Path:
    return _resolved_root(root) / "instructions.md"


def weekly_review_path(root: Path | None = None) -> Path:
    return _resolved_root(root) / "weekly_review_prompt.md"


def projects_and_goals_path(root: Path | None = None) -> Path:
    return _resolved_root(root) / "projects_and_goals.md"


def intentions_path(root: Path | None = None) -> Path:
    return _resolved_root(root) / "intentions.md"


def _seed_file(target: Path, default_name: str) -> None:
    """Write a packaged default into ``target`` only if it doesn't exist yet."""
    if target.exists():
        return
    default = _DEFAULTS_DIR / default_name
    if default.exists():
        target.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")


def seed_defaults(root: Path | None = None) -> None:
    """Create the GTD root and seed starter files that don't exist yet.

    Seeds ``instructions.md`` and ``weekly_review_prompt.md`` from the packaged
    defaults so a fresh install has a working triage workflow and weekly review
    on first boot instead of a blank slate. Only writes files that are missing,
    so once the user has their own versions this is a no-op and never clobbers
    their edits.
    """
    resolved = _resolved_root(root)
    (resolved / "items").mkdir(parents=True, exist_ok=True)
    _seed_file(instructions_path(root), "instructions.md")
    _seed_file(weekly_review_path(root), "weekly_review_prompt.md")


def new_item_id() -> str:
    return f"gtd-{secrets.token_hex(3)}"


def path_for_id(item_id: str, root: Path | None = None) -> Path:
    if not _ID_PATTERN.match(item_id):
        raise GtdValueError(f"Invalid item id: {item_id!r}")
    return items_dir(root) / f"{item_id}.md"


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if value is None:
        return datetime.now()
    raise GtdValueError(f"Unrecognized datetime value: {value!r}")


def _coerce_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise GtdValueError(f"Unrecognized date value: {value!r}")


def _item_from_post(item_id: str, post: frontmatter.Post) -> Item:
    metadata = dict(post.metadata)
    created_at = _coerce_datetime(metadata.get("created_at"))
    # Legacy: items with status "project" had no is_project flag. Treat them
    # as projects with status "next" by default; on next write the legacy
    # status disappears.
    raw_status = metadata.get("status", ItemStatus.INBOX.value)
    is_project = bool(metadata.get("is_project", False))
    if raw_status == "project":
        is_project = True
        raw_status = ItemStatus.NEXT.value
    # Legacy: tickler_date was a separate field. Fold it into start_date if
    # start_date isn't already set, and migrate status: tickler -> next.
    legacy_tickler = _coerce_date(metadata.get("tickler_date"))
    start_date_value = _coerce_date(metadata.get("start_date"))
    if legacy_tickler is not None and start_date_value is None:
        start_date_value = legacy_tickler
    if raw_status == "tickler":
        raw_status = ItemStatus.NEXT.value
    return Item(
        id=metadata.get("id", item_id),
        title=metadata.get("title", "(untitled)"),
        status=ItemStatus(raw_status),
        created_at=created_at,
        updated_at=_coerce_datetime(metadata.get("updated_at", created_at)),
        body=post.content,
        due_date=_coerce_date(metadata.get("due_date")),
        start_date=start_date_value,
        parent=metadata.get("parent"),
        waiting_for=metadata.get("waiting_for"),
        contexts=tuple(metadata.get("contexts") or ()),
        tags=tuple(metadata.get("tags") or ()),
        quick=bool(metadata.get("quick", False)),
        is_project=is_project,
        priority=Priority(metadata.get("priority", Priority.NORMAL.value)),
        prev_status=ItemStatus(metadata["prev_status"]) if metadata.get("prev_status") else None,
        recur_weekdays=tuple(int(d) for d in metadata.get("recur_weekdays") or ()),
    )


def read_item(item_id: str, root: Path | None = None) -> Item:
    path = path_for_id(item_id, root)
    with path.open("r", encoding="utf-8") as handle:
        post = frontmatter.load(handle)
    return _item_from_post(item_id, post)


def write_item(item: Item, root: Path | None = None) -> None:
    items_dir(root).mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "id": item.id,
        "title": item.title,
        "status": item.status.value,
        "created_at": item.created_at.isoformat(timespec="seconds"),
        "updated_at": item.updated_at.isoformat(timespec="seconds"),
    }
    if item.due_date is not None:
        metadata["due_date"] = item.due_date.isoformat()
    if item.start_date is not None:
        metadata["start_date"] = item.start_date.isoformat()
    if item.parent is not None:
        metadata["parent"] = item.parent
    if item.waiting_for is not None:
        metadata["waiting_for"] = item.waiting_for
    if item.contexts:
        metadata["contexts"] = list(item.contexts)
    if item.tags:
        metadata["tags"] = list(item.tags)
    if item.quick:
        metadata["quick"] = True
    if item.is_project:
        metadata["is_project"] = True
    if item.priority != Priority.NORMAL:
        metadata["priority"] = item.priority.value
    if item.prev_status is not None:
        metadata["prev_status"] = item.prev_status.value
    if item.recur_weekdays:
        metadata["recur_weekdays"] = list(item.recur_weekdays)

    # Trailing newlines on the body are normalized away by python-frontmatter
    # when dumping, so strip them up front to keep round-trips clean.
    post = frontmatter.Post(item.body.rstrip("\n"), **metadata)
    path = path_for_id(item.id, root)
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def delete_item(item_id: str, root: Path | None = None) -> None:
    path = path_for_id(item_id, root)
    path.unlink(missing_ok=True)


def all_items(root: Path | None = None) -> Iterable[Item]:
    directory = items_dir(root)
    if not directory.exists():
        return
    for path in sorted(directory.glob("gtd-*.md")):
        with path.open("r", encoding="utf-8") as handle:
            post = frontmatter.load(handle)
        yield _item_from_post(path.stem, post)


def items_by_status(status: ItemStatus, root: Path | None = None) -> list[Item]:
    return [item for item in all_items(root) if item.status == status]


def children_of(parent_id: str, root: Path | None = None) -> list[Item]:
    return [item for item in all_items(root) if item.parent == parent_id]


def read_instructions(root: Path | None = None) -> str:
    path = instructions_path(root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_instructions(text: str, root: Path | None = None) -> None:
    path = instructions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_weekly_review_prompt(root: Path | None = None) -> str:
    path = weekly_review_path(root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_weekly_review_prompt(text: str, root: Path | None = None) -> None:
    path = weekly_review_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_projects_and_goals(root: Path | None = None) -> str:
    path = projects_and_goals_path(root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_projects_and_goals(text: str, root: Path | None = None) -> None:
    path = projects_and_goals_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_intentions(root: Path | None = None) -> str:
    path = intentions_path(root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_intentions(text: str, root: Path | None = None) -> None:
    path = intentions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
