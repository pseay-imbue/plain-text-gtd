"""Unit tests for storage round-trips."""

from datetime import date, datetime
from pathlib import Path

import pytest

from gtd.data_types import GtdValueError, Item, ItemStatus, Priority
from gtd.storage import (
    all_items,
    children_of,
    delete_item,
    items_by_status,
    new_item_id,
    read_instructions,
    read_item,
    read_weekly_review_prompt,
    seed_defaults,
    write_item,
)


def _make_item(
    *,
    item_id: str = "gtd-aaaa11",
    status: ItemStatus = ItemStatus.INBOX,
    title: str = "Buy milk",
    body: str = "",
    parent: str | None = None,
    due_date: date | None = None,
    start_date: date | None = None,
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
        start_date=start_date,
        parent=parent,
    )


def test_new_item_id_is_well_formed() -> None:
    item_id = new_item_id()
    assert item_id.startswith("gtd-")
    assert len(item_id) == len("gtd-") + 6


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    original = _make_item(
        title="Plan launch",
        body="Some triage notes",
        due_date=date(2026, 6, 1),
    )
    write_item(original, root=tmp_path)
    restored = read_item(original.id, root=tmp_path)
    assert restored == original


def test_read_item_preserves_body_with_yaml_lookalike(tmp_path: Path) -> None:
    item = _make_item(body="Body text\n---\nnot frontmatter")
    write_item(item, root=tmp_path)
    restored = read_item(item.id, root=tmp_path)
    assert restored.body == item.body


def test_all_items_returns_every_written_item(tmp_path: Path) -> None:
    first = _make_item(item_id="gtd-aaaa11")
    second = _make_item(item_id="gtd-bbbb22", status=ItemStatus.NEXT)
    write_item(first, root=tmp_path)
    write_item(second, root=tmp_path)
    ids = {item.id for item in all_items(root=tmp_path)}
    assert ids == {first.id, second.id}


def test_items_by_status_filters(tmp_path: Path) -> None:
    write_item(_make_item(item_id="gtd-aaaa11", status=ItemStatus.INBOX), root=tmp_path)
    write_item(_make_item(item_id="gtd-bbbb22", status=ItemStatus.NEXT), root=tmp_path)
    write_item(_make_item(item_id="gtd-cccc33", status=ItemStatus.NEXT), root=tmp_path)
    next_items = items_by_status(ItemStatus.NEXT, root=tmp_path)
    assert {item.id for item in next_items} == {"gtd-bbbb22", "gtd-cccc33"}


def test_children_of_returns_only_children(tmp_path: Path) -> None:
    parent = _make_item(item_id="gtd-pppp11", status=ItemStatus.NEXT)
    child = _make_item(item_id="gtd-cccc22", status=ItemStatus.NEXT, parent=parent.id)
    unrelated = _make_item(item_id="gtd-uuuu33", status=ItemStatus.NEXT)
    write_item(parent, root=tmp_path)
    write_item(child, root=tmp_path)
    write_item(unrelated, root=tmp_path)
    assert [item.id for item in children_of(parent.id, root=tmp_path)] == [child.id]


def test_delete_item_removes_file(tmp_path: Path) -> None:
    item = _make_item()
    write_item(item, root=tmp_path)
    delete_item(item.id, root=tmp_path)
    assert list(all_items(root=tmp_path)) == []


def test_invalid_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(GtdValueError):
        read_item("not-a-valid-id", root=tmp_path)


def test_priority_round_trips_when_set(tmp_path: Path) -> None:
    """The Priority field survives a write/read cycle when set to a non-default value."""
    now = datetime(2026, 5, 12, 18, 30, 0)
    high_item = Item(
        id="gtd-aaaa11",
        title="urgent",
        status=ItemStatus.NEXT,
        created_at=now,
        updated_at=now,
        priority=Priority.HIGH,
    )
    write_item(high_item, root=tmp_path)
    assert read_item("gtd-aaaa11", root=tmp_path).priority == Priority.HIGH

    # And the absence of priority in the file deserializes to NORMAL (the default).
    raw_path = tmp_path / "items" / "gtd-bbbb22.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "---\n"
        "id: gtd-bbbb22\n"
        "title: defaults to normal\n"
        "status: next\n"
        "created_at: '2026-05-12T18:30:00'\n"
        "updated_at: '2026-05-12T18:30:00'\n"
        "---\n",
        encoding="utf-8",
    )
    assert read_item("gtd-bbbb22", root=tmp_path).priority == Priority.NORMAL


def test_is_project_and_quick_flags_round_trip(tmp_path: Path) -> None:
    """is_project and quick flags persist correctly when True; omitted when False."""
    now = datetime(2026, 5, 12, 18, 30, 0)
    item = Item(
        id="gtd-aaaa11",
        title="project",
        status=ItemStatus.NEXT,
        created_at=now,
        updated_at=now,
        is_project=True,
        quick=True,
    )
    write_item(item, root=tmp_path)
    restored = read_item("gtd-aaaa11", root=tmp_path)
    assert restored.is_project is True
    assert restored.quick is True


def test_seed_defaults_seeds_starter_files_on_fresh_root(tmp_path: Path) -> None:
    root = tmp_path / "gtd"
    assert read_instructions(root=root) == ""
    assert read_weekly_review_prompt(root=root) == ""
    seed_defaults(root=root)
    assert "Triage instructions" in read_instructions(root=root)
    assert "Weekly Review" in read_weekly_review_prompt(root=root)
    assert (root / "items").is_dir()


def test_seed_defaults_never_overwrites_existing_instructions(tmp_path: Path) -> None:
    root = tmp_path / "gtd"
    seed_defaults(root=root)
    (root / "instructions.md").write_text("my own rules", encoding="utf-8")
    seed_defaults(root=root)
    assert read_instructions(root=root) == "my own rules"


def test_legacy_project_status_is_migrated_on_read(tmp_path: Path) -> None:
    """An item written with the legacy `status: project` reads back as is_project=True, status=next."""
    raw_path = tmp_path / "items" / "gtd-legacy00.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "---\n"
        "id: gtd-legacy00\n"
        "title: Legacy project\n"
        "status: project\n"
        "created_at: '2026-05-01T10:00:00'\n"
        "updated_at: '2026-05-01T10:00:00'\n"
        "---\n",
        encoding="utf-8",
    )
    restored = read_item("gtd-legacy00", root=tmp_path)
    assert restored.is_project is True
    assert restored.status == ItemStatus.NEXT
