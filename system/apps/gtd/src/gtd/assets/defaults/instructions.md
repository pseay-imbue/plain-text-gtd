# Triage instructions

These are the rules for processing items out of the Inbox. The web UI shows
this file (collapsed) at the top of the Inbox tab, and a chat-driven triage
agent can read it at the start of a triage session — so editing this file
changes how triage works. This is a starting point: adapt it to how you
actually work.

## The decision tree

For each Inbox item, ask in order:

1. **Is this actionable?**
   - No: route to **Reference** (info to keep), **Read/Review** (something to
     read or watch), **Someday/Maybe** (might-do later, no commitment), or
     **Trashed**. Done.
   - Yes: continue.

2. **What is the desired outcome?** State it in one sentence. If it takes more
   than one physical step, it is a **Project** — set `is_project: true` on the
   item (status can stay `next` or whatever active bucket fits) and create at
   least one child action (`parent: <project-id>`) for the very next physical
   step. Project-ness is a flag, not a status, so a project can also live in
   `someday`.

3. **What is the next physical action?** Concrete verb + object (e.g. "draft
   email to Alex"). This becomes a Next Action or a child of a Project.

4. **The 2-minute rule.** If the next action will take less than two minutes,
   just do it now. Don't file it.

5. **Otherwise, where does it go?**
   - **Delegate** → `waiting`, with `waiting_for: <person>` set.
   - **Defer to a specific day** → set `start_date` to that day; the item waits
     in Tickler until then, then rejoins Next.
   - **Hard deadline** → set `due_date`. It shows on the Calendar that day.
   - **Otherwise** → status `next` (Next Actions).

## Blocked on your own output

If an item is blocked on *you* finishing some other piece of work (e.g. "email
the client once the proposal is done"), classify it as `waiting` with
`waiting_for` naming the blocker explicitly — e.g. `waiting_for: proposal to
ship`. The Waiting For view then doubles as your "follow-ups unlocked when X
ships" list.

## Vague items

If an item is vague or large ("figure out the business plan", "decide what to
do about X"), don't force it into a bucket. Leave it in Inbox or move it to a
project shell, and add a note to think it through later.

## Read/Review

Items in `read-review` are things to read or watch. They live as a flat
checklist; tag them with `tags: [video|article|book|paper]` if the distinction
is useful, otherwise leave tags empty.

## Priority

Use `priority: high` sparingly — it pulls items to the top of every list. The
default is `normal`. `low` means "this exists, but don't surface it prominently
right now."

## Quick (2-min) tasks

If an item really will take under two minutes, mark `quick: true` (or toggle it
from the edit page). It surfaces at the top of Next Actions with a small
"2 min" marker so it's easy to knock out during a gap.

## How the Someday cascade works

When you move a project to `someday`, every active sub-action moves to `someday`
too (each carried child saves its prior status in a `prev_status` field). When
you move the project back out of `someday`, its someday sub-actions reactivate —
children with a saved `prev_status` restore to exactly that bucket; everything
else defaults to `next`. To shelve just one sub-action, change only that
sub-action's status.
