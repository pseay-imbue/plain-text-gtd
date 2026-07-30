"""Frozen domain types for the GTD app."""

from datetime import date, datetime
from enum import StrEnum

from imbue.imbue_common.frozen_model import FrozenModel
from pydantic import Field


class GtdError(Exception):
    """Base exception for GTD app errors."""


class GtdValueError(GtdError, ValueError):
    """Invalid value passed to a GTD operation."""


class ItemStatus(StrEnum):
    """Which view bucket an item lives in. Project-ness is orthogonal — see Item.is_project."""

    INBOX = "inbox"
    NEXT = "next"
    WAITING = "waiting"
    SOMEDAY = "someday"
    READ_REVIEW = "read-review"
    REFERENCE = "reference"
    DONE = "done"
    TRASHED = "trashed"


class Priority(StrEnum):
    """Manual priority for sorting within a view."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


PRIORITY_SORT: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.NORMAL: 1,
    Priority.LOW: 2,
}


# How status buckets rank inside a project detail view. Active actions
# come first, deferred ones in the middle, blocked-on-someone after,
# completed and trashed last.
PROJECT_CHILD_SORT: dict[ItemStatus, int] = {
    ItemStatus.NEXT: 0,
    ItemStatus.INBOX: 1,
    ItemStatus.SOMEDAY: 2,
    ItemStatus.WAITING: 3,
    ItemStatus.READ_REVIEW: 4,
    ItemStatus.REFERENCE: 5,
    ItemStatus.DONE: 6,
    ItemStatus.TRASHED: 7,
}


ACTIVE_STATUSES: tuple[ItemStatus, ...] = (
    ItemStatus.INBOX,
    ItemStatus.NEXT,
    ItemStatus.WAITING,
)
HOLDING_STATUSES: tuple[ItemStatus, ...] = (
    ItemStatus.SOMEDAY,
    ItemStatus.READ_REVIEW,
    ItemStatus.REFERENCE,
)


class Item(FrozenModel):
    """A single GTD item stored as one markdown file with YAML frontmatter."""

    id: str = Field(description="Stable identifier of the form gtd-<6 hex chars>.")
    title: str = Field(description="Short title shown in lists and on the calendar.")
    status: ItemStatus = Field(description="Which view bucket this item lives in.")
    created_at: datetime = Field(description="When the item was first captured.")
    updated_at: datetime = Field(description="When the item was last modified.")
    body: str = Field(default="", description="Free-form markdown notes appended during triage.")

    due_date: date | None = Field(default=None, description="Hard deadline for the item.")
    start_date: date | None = Field(
        default=None,
        description="The day to start working on this. If in the future, the item lives in Tickler until that day.",
    )

    parent: str | None = Field(default=None, description="Id of the parent project, if any.")
    waiting_for: str | None = Field(default=None, description="Who or what this item is blocked on.")
    contexts: tuple[str, ...] = Field(default=(), description="GTD contexts like @phone, @home.")
    tags: tuple[str, ...] = Field(default=(), description="Free-form labels.")
    quick: bool = Field(default=False, description="True if this is a sub-2-minute action; surfaces at top of Next.")
    recur_weekdays: tuple[int, ...] = Field(
        default=(),
        description="Weekdays this item recurs on (Mon=0..Sun=6). Empty = not recurring. Checking off bounces start_date to the next listed weekday.",
    )
    is_project: bool = Field(default=False, description="True if this item is a project (has children). Orthogonal to status.")
    priority: Priority = Field(default=Priority.NORMAL, description="Manual priority; high items rise to the top of every list.")
    prev_status: ItemStatus | None = Field(
        default=None,
        description="Saved status from before a project→Someday cascade, so cascade-back can restore the exact prior bucket (e.g. waiting).",
    )
