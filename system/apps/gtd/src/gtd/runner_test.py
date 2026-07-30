"""Integration tests for the GTD web app."""

import importlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gtd import runner, storage
from gtd.data_types import Item, ItemStatus, Priority


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GTD_ROOT", str(tmp_path))
    (tmp_path / "items").mkdir(parents=True, exist_ok=True)
    (tmp_path / "instructions.md").write_text("# rules\n", encoding="utf-8")
    (tmp_path / "weekly_review_prompt.md").write_text("Walk me through it.\n", encoding="utf-8")
    importlib.reload(runner)
    with TestClient(runner.app) as test_client:
        yield test_client


def _make_item(
    *,
    item_id: str = "gtd-aaaa11",
    status: ItemStatus = ItemStatus.INBOX,
    title: str = "Sample",
    due_date: date | None = None,
    parent: str | None = None,
    body: str = "",
) -> Item:
    now = datetime(2026, 5, 12, 18, 30, 0)
    return Item(
        id=item_id,
        title=title,
        status=status,
        created_at=now,
        updated_at=now,
        body=body,
        due_date=due_date,
        parent=parent,
    )


def test_root_redirects_to_today(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/today")


def test_inbox_shows_captured_items(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="Hello world"))
    response = client.get("/inbox")
    assert response.status_code == 200
    assert "Hello world" in response.text


def test_inbox_shows_first_run_onboarding_only_on_fresh_system(client: TestClient) -> None:
    # A brand-new system guides the mass-dump + triage-all flow.
    fresh = client.get("/inbox")
    assert fresh.status_code == 200
    assert "Getting started" in fresh.text
    assert "Dump your whole list" in fresh.text
    # Once any item exists, the onboarding gives way to the normal view.
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="something"))
    populated = client.get("/inbox")
    assert "Getting started" not in populated.text


def test_list_view_renders_full_body_for_inline_expand(client: TestClient) -> None:
    # The body preview is now rendered in full (clamping + more/less toggle are
    # client-side), so text past the old truncation point must still be present,
    # along with the marker app.js hooks the toggle onto.
    tail = "this sentence sits well past the old preview cutoff and must still render"
    long_body = "Start of the note. " + ("filler word " * 40) + tail
    storage.write_item(
        _make_item(
            item_id="gtd-bbbb22",
            status=ItemStatus.NEXT,
            title="Long note",
            body=long_body,
        )
    )
    response = client.get("/next")
    assert response.status_code == 200
    assert tail in response.text
    assert "data-body-preview" in response.text


def test_bulk_capture_strips_bullets_and_files_to_inbox(client: TestClient) -> None:
    block = "Buy milk\n- Email Alex\n* Walk the dog\n1. Think about the company"
    response = client.post("/items", data={"block": block}, follow_redirects=False)
    assert response.status_code == 303
    items = sorted(storage.all_items(), key=lambda i: i.title)
    titles = [item.title for item in items]
    assert titles == [
        "Buy milk",
        "Email Alex",
        "Think about the company",
        "Walk the dog",
    ]
    assert all(item.status == ItemStatus.INBOX for item in items)


def test_triage_endpoint_returns_snippet(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="Triage me"))
    response = client.get("/api/triage/gtd-aaaa11")
    assert response.status_code == 200
    text = response.text
    assert "Triage gtd-aaaa11" in text
    assert "Triage me" in text
    assert "instructions.md" in text


def test_handoff_endpoint_includes_metadata_and_notes(client: TestClient) -> None:
    storage.write_item(
        _make_item(
            item_id="gtd-aaaa11",
            status=ItemStatus.NEXT,
            title="Ship the demo",
            due_date=date(2026, 6, 1),
            body="Things to remember:\n- mic check",
        )
    )
    response = client.get("/api/handoff/gtd-aaaa11")
    assert response.status_code == 200
    text = response.text
    assert "# GTD item gtd-aaaa11: Ship the demo" in text
    assert "- Status: next" in text
    assert "- Due: 2026-06-01" in text
    assert "Things to remember" in text


def test_plan_endpoint_bundles_project_with_children(client: TestClient) -> None:
    storage.write_item(
        Item(
            id="gtd-pppp11",
            title="Launch the thing",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 12, 18, 30, 0),
            updated_at=datetime(2026, 5, 12, 18, 30, 0),
            body="Outcome: thing is launched",
            is_project=True,
        )
    )
    storage.write_item(
        _make_item(
            item_id="gtd-cccc22",
            status=ItemStatus.NEXT,
            title="Write the announcement",
            parent="gtd-pppp11",
        )
    )
    storage.write_item(
        _make_item(item_id="gtd-uuuu33", status=ItemStatus.NEXT, title="Unrelated thing"),
    )
    response = client.get("/api/plan/gtd-pppp11")
    assert response.status_code == 200
    text = response.text
    assert "Plan project gtd-pppp11" in text
    assert "Outcome: thing is launched" in text
    assert "Write the announcement" in text
    assert "Unrelated thing" not in text


def test_weekly_review_endpoint_returns_prompt(client: TestClient) -> None:
    response = client.get("/api/weekly-review")
    assert response.status_code == 200
    assert "Walk me through it" in response.text


def test_save_item_updates_status_and_due_date(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="Move me"))
    response = client.post(
        "/items/gtd-aaaa11",
        data={
            "title": "Move me",
            "status": "next",
            "due_date": "2026-06-15",
            "start_date": "",
            "waiting_for": "",
            "parent": "",
            "tags": "urgent, demo",
            "body": "Updated body",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    updated = storage.read_item("gtd-aaaa11")
    assert updated.status == ItemStatus.NEXT
    assert updated.due_date == date(2026, 6, 15)
    assert updated.tags == ("urgent", "demo")
    assert updated.body == "Updated body"


def test_delete_item_removes_file(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="Doomed"))
    response = client.post("/items/gtd-aaaa11/delete", follow_redirects=False)
    assert response.status_code == 303
    assert list(storage.all_items()) == []


def test_calendar_lists_items_on_their_due_date(client: TestClient) -> None:
    storage.write_item(
        _make_item(
            item_id="gtd-aaaa11",
            status=ItemStatus.NEXT,
            title="Deadline thing",
            due_date=date(2026, 5, 20),
        )
    )
    response = client.get("/calendar/2026/5")
    assert response.status_code == 200
    assert "Deadline thing" in response.text


def test_check_item_marks_done_and_uncheck_restores_inbox(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="To finish"))
    checked = client.post("/items/gtd-aaaa11/check", data={"checked": "on"}, follow_redirects=False)
    assert checked.status_code == 303
    assert storage.read_item("gtd-aaaa11").status == ItemStatus.DONE
    unchecked = client.post("/items/gtd-aaaa11/check", data={}, follow_redirects=False)
    assert unchecked.status_code == 303
    assert storage.read_item("gtd-aaaa11").status == ItemStatus.INBOX


def test_done_section_renders_on_every_page(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.DONE, title="Already finished"))
    for path in ("/inbox", "/next", "/calendar", "/reference"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "done-section" in response.text, path
        assert "Already finished" in response.text, path


def test_batch_triage_endpoint_lists_inbox_items(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="One"))
    storage.write_item(_make_item(item_id="gtd-bbbb22", title="Two"))
    storage.write_item(_make_item(item_id="gtd-cccc33", status=ItemStatus.NEXT, title="Not in inbox"))
    response = client.get("/api/triage-inbox")
    assert response.status_code == 200
    text = response.text
    assert "first-pass triage" in text
    assert "gtd-aaaa11" in text
    assert "gtd-bbbb22" in text
    assert "gtd-cccc33" not in text


def test_weekly_review_page_renders_and_saves_edits(client: TestClient) -> None:
    response = client.get("/weekly-review")
    assert response.status_code == 200
    assert "Walk me through it" in response.text
    save = client.post(
        "/weekly-review",
        data={"body": "Brand new prompt."},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert storage.read_weekly_review_prompt().strip() == "Brand new prompt."


def test_rename_endpoint_updates_only_the_title(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", title="Old name", status=ItemStatus.NEXT))
    response = client.post(
        "/items/gtd-aaaa11/title",
        data={"title": "New name"},
        follow_redirects=False,
    )
    assert response.status_code == 204
    restored = storage.read_item("gtd-aaaa11")
    assert restored.title == "New name"
    assert restored.status == ItemStatus.NEXT


def test_set_status_endpoint_changes_status(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.INBOX))
    response = client.post(
        "/items/gtd-aaaa11/status",
        data={"status": "next"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert storage.read_item("gtd-aaaa11").status == ItemStatus.NEXT


def test_set_priority_endpoint_changes_priority(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.NEXT))
    response = client.post(
        "/items/gtd-aaaa11/priority",
        data={"priority": "high"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert storage.read_item("gtd-aaaa11").priority == Priority.HIGH


def test_set_due_date_endpoint_sets_and_clears(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.NEXT))
    set_resp = client.post(
        "/items/gtd-aaaa11/due-date",
        data={"due_date": "2026-06-15"},
        follow_redirects=False,
    )
    assert set_resp.status_code == 303
    assert storage.read_item("gtd-aaaa11").due_date == date(2026, 6, 15)

    clear_resp = client.post(
        "/items/gtd-aaaa11/due-date",
        data={"due_date": ""},
        follow_redirects=False,
    )
    assert clear_resp.status_code == 303
    assert storage.read_item("gtd-aaaa11").due_date is None


def test_set_start_date_endpoint_sets_value(client: TestClient) -> None:
    storage.write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.NEXT))
    response = client.post(
        "/items/gtd-aaaa11/start-date",
        data={"start_date": "2026-06-01"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert storage.read_item("gtd-aaaa11").start_date == date(2026, 6, 1)


def test_next_view_sorts_high_priority_first(client: TestClient) -> None:
    """High-priority items rise to the top of Next Actions."""
    storage.write_item(
        Item(
            id="gtd-aaaa11",
            title="Normal first by date",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 10, 9, 0),
            updated_at=datetime(2026, 5, 10, 9, 0),
        )
    )
    storage.write_item(
        Item(
            id="gtd-bbbb22",
            title="High urgent task",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 12, 9, 0),
            updated_at=datetime(2026, 5, 12, 9, 0),
            priority=Priority.HIGH,
        )
    )
    response = client.get("/next")
    assert response.status_code == 200
    body = response.text
    high_pos = body.find("High urgent task")
    normal_pos = body.find("Normal first by date")
    assert high_pos != -1 and normal_pos != -1
    assert high_pos < normal_pos, "high-priority item must appear before normal-priority"


def test_today_view_shows_items_due_today_and_starting_today(client: TestClient) -> None:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    storage.write_item(
        Item(
            id="gtd-due0011",
            title="Due today",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            due_date=today,
        )
    )
    storage.write_item(
        Item(
            id="gtd-start011",
            title="Starting today",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            start_date=today,
        )
    )
    storage.write_item(
        Item(
            id="gtd-later011",
            title="Due tomorrow",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            due_date=tomorrow,
        )
    )
    response = client.get("/today")
    assert response.status_code == 200
    body = response.text
    assert "Due today" in body
    assert "Starting today" in body
    assert "Due tomorrow" not in body


def test_deferred_items_only_appear_on_calendar_on_their_start_date(client: TestClient) -> None:
    storage.write_item(
        Item(
            id="gtd-tttt11",
            title="Resurface me",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            start_date=date(2026, 5, 25),
        )
    )
    response = client.get("/calendar/2026/5")
    assert response.status_code == 200
    assert "Resurface me" in response.text

    earlier_month = client.get("/calendar/2026/4")
    assert earlier_month.status_code == 200
    assert "Resurface me" not in earlier_month.text


def test_recurring_check_off_bounces_start_date_to_next_weekday(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recurring item, when checked off, advances start_date instead of going done."""
    fixed_today = date(2026, 5, 18)  # Monday
    monkeypatch.setattr("gtd.runner._today", lambda: fixed_today)
    storage.write_item(
        Item(
            id="gtd-recur01",
            title="Write for 2 hours on Say No essay",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            start_date=fixed_today,
            recur_weekdays=(0, 1, 2, 3, 4),  # Mon-Fri
        )
    )
    response = client.post(
        "/items/gtd-recur01/check", data={"checked": "on"}, follow_redirects=False
    )
    assert response.status_code == 303
    after = storage.read_item("gtd-recur01")
    assert after.status == ItemStatus.NEXT  # still next, not done
    assert after.start_date == date(2026, 5, 19)  # Tue


def test_recurring_check_off_skips_missed_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If you don't check off until later in the week, the next occurrence
    is the first matching weekday after today, not a backlog of missed days."""
    fixed_today = date(2026, 5, 21)  # Thursday
    monkeypatch.setattr("gtd.runner._today", lambda: fixed_today)
    storage.write_item(
        Item(
            id="gtd-recur02",
            title="Mon/Wed recur",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            start_date=date(2026, 5, 18),  # last Monday (already past)
            recur_weekdays=(0, 2),  # Mon and Wed
        )
    )
    client.post("/items/gtd-recur02/check", data={"checked": "on"}, follow_redirects=False)
    after = storage.read_item("gtd-recur02")
    # First Monday after today (Thursday May 21) is Monday May 25.
    assert after.start_date == date(2026, 5, 25)


def test_project_detail_renders_body_as_html(client: TestClient) -> None:
    """The project detail page renders the project's markdown body as HTML.

    Regression: the route was passing `project=...` to the template, but the
    template referenced `project.body_html` which doesn't exist on the Item
    model — so the body never displayed.
    """
    storage.write_item(
        Item(
            id="gtd-pbody11",
            title="Daily practice project",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 18),
            updated_at=datetime(2026, 5, 18),
            is_project=True,
            body="## Practice menu\n\n- **Free-writing.** Daily, unfiltered.\n",
        )
    )
    response = client.get("/projects/gtd-pbody11")
    assert response.status_code == 200
    text = response.text
    assert "Practice menu" in text
    assert "Free-writing" in text
    # Markdown was converted to HTML (the rendered view, not just the raw
    # markdown in the edit textarea).
    assert "<h2>Practice menu</h2>" in text
    assert "<strong>Free-writing.</strong>" in text


def test_project_detail_includes_editable_body_wrap(client: TestClient) -> None:
    """The project detail page exposes the body inside the click-to-edit wrap.

    The wrap holds both the rendered view and a hidden textarea with the
    raw markdown, so the front-end JS can swap between them.
    """
    storage.write_item(
        Item(
            id="gtd-pedit11",
            title="Editable body project",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 18),
            updated_at=datetime(2026, 5, 18),
            is_project=True,
            body="## Notes\n\nFirst line.\nSecond *line*.\n",
        )
    )
    response = client.get("/projects/gtd-pedit11")
    assert response.status_code == 200
    text = response.text
    assert 'data-body-edit' in text
    assert 'data-body-view' in text
    assert 'data-body-raw' in text
    assert "/items/gtd-pedit11/body" in text  # save endpoint url
    assert "First line." in text  # raw markdown in the textarea
    assert "<h2>Notes</h2>" in text  # rendered view


def test_project_detail_with_empty_body_still_renders_editable_wrap(
    client: TestClient,
) -> None:
    """Projects without a body still get the wrap so the user can click to add notes."""
    storage.write_item(
        Item(
            id="gtd-pempty11",
            title="No-body project",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 18),
            updated_at=datetime(2026, 5, 18),
            is_project=True,
            body="",
        )
    )
    response = client.get("/projects/gtd-pempty11")
    assert response.status_code == 200
    text = response.text
    assert 'data-body-edit' in text
    assert "project-shell__body--empty" in text
    assert "Click to add project notes" in text


def test_save_body_endpoint_persists_and_returns_rendered_html(
    client: TestClient,
) -> None:
    """POST /items/<id>/body saves the new body and returns the rendered HTML."""
    storage.write_item(
        Item(
            id="gtd-pbsave11",
            title="Body save target",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 18),
            updated_at=datetime(2026, 5, 18),
            is_project=True,
            body="old text",
        )
    )
    response = client.post(
        "/items/gtd-pbsave11/body",
        data={"body": "## New heading\n\nSome **bold** text.\n"},
    )
    assert response.status_code == 200
    html = response.text
    assert "<h2>New heading</h2>" in html
    assert "<strong>bold</strong>" in html
    persisted = storage.read_item("gtd-pbsave11")
    assert "New heading" in persisted.body
    assert persisted.body.startswith("## New heading")


def test_save_body_endpoint_accepts_empty_body(client: TestClient) -> None:
    """Clearing the body is allowed; the response is empty HTML."""
    storage.write_item(
        Item(
            id="gtd-pbempty1",
            title="Body clear target",
            status=ItemStatus.NEXT,
            created_at=datetime(2026, 5, 18),
            updated_at=datetime(2026, 5, 18),
            is_project=True,
            body="something to remove",
        )
    )
    response = client.post("/items/gtd-pbempty1/body", data={"body": ""})
    assert response.status_code == 200
    assert response.text == ""
    persisted = storage.read_item("gtd-pbempty1")
    assert persisted.body == ""


def test_legacy_tickler_date_migrates_to_start_date(tmp_path: Path) -> None:
    """An item written with the legacy tickler_date + status: tickler is
    read back with start_date populated and status migrated to next."""
    raw_path = tmp_path / "items" / "gtd-legacy11.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "---\n"
        "id: gtd-legacy11\n"
        "title: Legacy tickler\n"
        "status: tickler\n"
        "created_at: '2026-05-01T10:00:00'\n"
        "updated_at: '2026-05-01T10:00:00'\n"
        "tickler_date: 2026-05-25\n"
        "---\n",
        encoding="utf-8",
    )
    restored = storage.read_item("gtd-legacy11", root=tmp_path)
    assert restored.status == ItemStatus.NEXT
    assert restored.start_date == date(2026, 5, 25)


def test_project_picker_excludes_trashed_and_done_projects(client: TestClient) -> None:
    """The "move to project" picker only offers active projects. A project
    that has been trashed (e.g. after merging it into another) or completed
    must not appear as a selectable parent, even though it stays in the
    parent lookup so existing children can still render its name."""
    active = _make_item(
        item_id="gtd-pppp11", status=ItemStatus.NEXT, title="Active Project"
    ).model_copy(update={"is_project": True})
    trashed = _make_item(
        item_id="gtd-pppp22", status=ItemStatus.TRASHED, title="Retired Project"
    ).model_copy(update={"is_project": True})
    done = _make_item(
        item_id="gtd-pppp33", status=ItemStatus.DONE, title="Finished Project"
    ).model_copy(update={"is_project": True})
    for project in (active, trashed, done):
        storage.write_item(project)
    storage.write_item(_make_item(item_id="gtd-tttt11", status=ItemStatus.NEXT, title="A task"))

    # The parent-picker options are built client-side from the shared
    # #gtd-picker-data blob, so assert on that: only the active project is an
    # offered parent. (A done project still renders as a row elsewhere, so a
    # plain title substring check would false-positive — hence the id check.)
    html = client.get("/next").text
    blob = re.search(
        r'<script type="application/json" id="gtd-picker-data">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert blob is not None
    project_ids = {p["id"] for p in json.loads(blob.group(1))["projects"]}
    assert "gtd-pppp11" in project_ids
    assert "gtd-pppp22" not in project_ids
    assert "gtd-pppp33" not in project_ids


def test_all_view_shows_orphaned_match_whose_parent_was_deleted(client: TestClient) -> None:
    """A matched item whose parent project no longer exists must still appear
    in the All/Search results (in the loose list), not be silently dropped
    because its project group can't be rendered."""
    # Title kept apostrophe-free so the substring check isn't tripped by HTML
    # entity escaping of "'".
    storage.write_item(
        _make_item(
            item_id="gtd-orph11",
            status=ItemStatus.NEXT,
            title="Read the Pope Leo encyclical",
            parent="gtd-gone99",  # parent project was deleted
        )
    )
    response = client.get("/all", params={"q": "pope"})
    assert response.status_code == 200
    assert "Read the Pope Leo encyclical" in response.text
    # The header match count should include the orphan.
    assert "1 match" in response.text
