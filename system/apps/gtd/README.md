# gtd

A personal GTD (Getting Things Done) inbox, organizer, and triage workflow,
backed by plain-text markdown files and served as a calm, ruled-paper web
view at `/service/gtd/`. Designed to be edited by both the user (in the UI
or directly in `data/.apps/gtd/`) and an agent (in chat), without surprise.

This README is the orientation doc — read this first if you are picking the
system up for the first time.


## What the system is

Everything lives in `data/.apps/gtd/` on disk:

```
data/.apps/gtd/
  instructions.md            # editable triage rules; the source of truth
  weekly_review_prompt.md    # editable weekly review prompt
  items/<id>.md              # one markdown file per item; frontmatter + body
```

`runtime/` is gitignored on `main` but the runtime-backup service mirrors it
to a separate orphan branch, so your items survive container loss. To wipe
the GTD state, delete (or move) `data/.apps/gtd/`.

The web app is a FastAPI + Jinja2 service in `system/apps/gtd/`. The
`bootstrap` service manager keeps it running; the workspace UI exposes it as
a tab at `/service/gtd/`.

There is no database. There is no JSON API in front of the files. Items
*are* the files. You can edit them with `vim`, `cat` them, grep them.


## Item file format

```markdown
---
id: gtd-abc123                 # required; gtd-<6 hex chars>
title: Buy a new bike          # required
status: inbox                  # required; see Status values below
created_at: '2026-05-12T18:30:00'
updated_at: '2026-05-12T18:30:00'

# optional dates
due_date: 2026-06-01           # hard deadline; shows on Calendar in red
start_date: 2026-05-15         # day to start; if in the future, the item waits in Tickler until then

# optional structure
parent: gtd-xyz789             # link to a parent project (any item with is_project: true)
waiting_for: alice             # free-text label for who/what you're blocked on

# optional flags
is_project: true               # marks this item as a project (can have children); orthogonal to status
quick: true                    # 2-minute action; rises to top of Next
priority: high                 # one of: high, normal (default), low
prev_status: waiting           # bookkeeping: prior status from a Someday cascade (auto-managed)

# optional labels
tags: [urgent, demo]
contexts: ['@phone', '@home']
---

Free-form markdown body.

## Triage 2026-05-12

Dated notes appended during chat triage live here. Never overwrite earlier
notes — always add a new section.
```

Anything omitted defaults sensibly (status → `inbox`; priority → `normal`;
flags → `false`). Trailing newlines on the body are normalized away on
write so round-trips are clean.

### Status values

One of: `inbox`, `next`, `waiting`, `someday`, `read-review`,
`reference`, `done`, `trashed`. **Note:** `project` was previously a status
but is now an orthogonal `is_project: true` flag — a project can sit in any
status (including `someday`). There is also no `tickler` status: deferral is
a `start_date` in the future on an otherwise-active item (see Tickler below).

| status        | meaning                                                          | primary view     |
|---------------|------------------------------------------------------------------|------------------|
| `inbox`       | Captured but not yet processed.                                  | Inbox            |
| `next`        | A concrete next physical action you can do without thinking.     | Next Actions     |
| `waiting`     | Blocked on someone or something. `waiting_for:` says who/what.   | Waiting For      |
| `someday`     | Might-do later. No commitment.                                   | Someday / Maybe  |
| `read-review` | Articles, papers, videos to consume.                             | Read & Review    |
| `reference`   | Information to keep handy. Not actionable.                       | Reference        |
| `done`        | Closed. Hidden from active views; surfaces in the Done section.  | Done (bottom)    |
| `trashed`     | Soft-deleted. Hidden everywhere. Delete the file to remove.      | (none)           |

To **defer** an item to a future day, leave it in an active status (usually
`next`) and set `start_date` to that day. While `start_date` is in the future
the item is held out of Next and listed in the **Tickler** view; on its
start day it rejoins Next and shows up in Today's "Starting today". Old files
that still carry `status: tickler` or a separate `tickler_date` field are
auto-migrated on read (`tickler` → `next`, `tickler_date` folded into
`start_date`); nothing new should be written in that form.

Items with `is_project: true` appear in the **Projects** view regardless of
their status (active projects first, then Someday-parked projects).


## Views

Nav clusters by mental mode:

- **Active** — Inbox, Next, Projects, Waiting, Tickler
- **Holding** — Someday/Maybe, Read & Review, Reference
- **Time** — Today, Calendar, Weekly Review
- **All** — Search & filter across every non-trashed item

What each view shows:

| view              | filter                                                                       | layout                                            |
|-------------------|------------------------------------------------------------------------------|---------------------------------------------------|
| **Today**         | items with `due_date` or `start_date` of today                               | two sections: "Due today" / "Starting today"      |
| **Inbox**         | `status == inbox`                                                            | flat list; capture textarea + Triage-all button   |
| **Next Actions**  | `status == next` and NOT `is_project`                                        | "Loose actions" + "Inside a project" groups       |
| **Projects**      | `is_project == true` (excluding done/trashed)                                | project rows; high priority first, someday at bottom |
| **Project detail**| children of a project                                                        | sorted by status (next → inbox → someday → waiting → read-review → reference → done) |
| **Waiting For**   | `status == waiting`                                                          | flat list, muted styling                          |
| **Tickler**       | non-done/trashed items whose `start_date` is in the future                   | sorted by `start_date` asc                        |
| **Someday/Maybe** | `status == someday`                                                          | two sections: "Projects" (with children indented) + "Loose tasks" |
| **Read & Review** | `status == read-review`                                                      | flat list                                         |
| **Reference**     | `status == reference`                                                        | flat list                                         |
| **Calendar**      | items with `due_date` or `start_date` in the month                           | month grid; chip per day                          |
| **All**           | every non-trashed item                                                       | search box + filters; projects with children nested|
| **Weekly Review** | (none — editable page)                                                       | textarea editing `weekly_review_prompt.md`        |

Every list view also renders a collapsed **Done** section at the bottom
showing site-wide done items.

### How the calendar handles dates

Month grid. An item appears on a day's cell when:

- `due_date == day` → red chip (hard deadline)
- `start_date == day` → umber chip (starting today; this is also the day a
  deferred Tickler item resurfaces)

Done and trashed items never appear.


## Capture

Three paths land items in the Inbox:

1. **Inbox UI textarea.** Paste any block of text; each non-empty line
   becomes one inbox item. Leading bullet markers (`- `, `* `, `1. `) are
   stripped automatically.
2. **Chat.** Tell the agent "file these:" and paste a list. The agent
   uses the `gtd-triage` skill, splits on lines, and writes one file per
   item under `data/.apps/gtd/items/`. Nothing gets triaged on capture.
3. **Direct file creation.** Drop a hand-written `gtd-<6 hex>.md` file
   into `data/.apps/gtd/items/`. The web app picks it up on the next page
   load.


## Inline editing

Every row in every list view has the same affordances:

- **Checkbox** (left): mark/unmark done. Done items get struck through and
  fade out of the current view (they reappear in the Done section). The
  confirmation toast carries an **Undo** button that restores the item to the
  bucket it was in before (the row's prior status), for a few seconds after
  you check it. (Recurring items don't go "done" — checking them bounces them
  to their next occurrence — so they get no Undo.)
- **Title** (center, click): edit the title in place. Enter saves, Escape
  reverts.
- **Priority dropdown** (under the title): low / normal / high. Changing
  priority re-sorts the row within its list.
- **Status dropdown**: change which bucket the item is in. If the new
  status no longer matches the current view, the row fades out (~1s).
- **Project dropdown**: assign or change the parent project.
- **Date inputs**: due date and start date, native calendar pickers.
- **Action icons** (right, on hover): copy a Triage-in-chat snippet, copy
  a handoff block, open the full edit form, delete.

All inline edits POST to the server asynchronously via `fetch` and show a
toast like `{title} → status: waiting`. No page reload, no scroll jump.

For projects (`is_project: true`), rows render differently: project-style
display with **Plan / Open / Edit / 🗑** buttons always visible. Clicking
the title goes to the project detail page.


## Triage in chat

Triage happens in chat, not in the UI. The principle: clicking buttons is
faster for trivial filing, but the value of GTD is in the *thinking*, and
thinking happens in conversation.

The flow:

1. From the UI, click **Triage in chat** on any item (or **Plan in chat**
   on a project). A snippet is copied to your clipboard.
2. Paste the snippet into this chat.
3. The `gtd-triage` skill kicks in automatically. It:
   - Reads `data/.apps/gtd/instructions.md` to load your current rules.
   - Reads the item file for the full notes context.
   - Walks through the decision tree with you.
   - Appends a dated note to the item body capturing what you decided.
   - Updates the item's status and frontmatter.

You can also say "triage my inbox" with no clipboard snippet — the skill
walks through every `status: inbox` item one at a time.

### Vague items

Items like "figure out the company" should not be triaged automatically.
The skill leaves these in `inbox` (or as a project shell), asks open
questions, and appends notes capturing what you say — it never forces a
decision. Move them forward only when you are ready to extract concrete
actions.

### "Blocked on my own output" pattern

If an item is blocked on *you* finishing some other piece of work (e.g.
"reach out to Jihad after the vision essay ships"), the rule is:

- `status: waiting`
- `waiting_for: <free text naming the blocker>` (e.g. `vision essay to ship`)

The Waiting For view doubles as your "follow-ups unlocked when X ships"
list. The agent applies this pattern automatically.


## The triage rules file

`data/.apps/gtd/instructions.md` is the source of truth for how the agent
processes items. It is editable from the UI (`/instructions`) or directly
on disk. The skill reads it at the start of every triage conversation, so
changes take effect on the next paste.

The web UI renders this file inside a collapsed `<details>` block at the
top of every page, with an **Edit these instructions** link inside.


## Project → Someday cascade

When you move a project's status to **Someday**, every active child
(items where `parent == project.id` and status is not done/trashed/
someday already) automatically follows: their `status` is set to
`someday`, and their previous status is saved in a `prev_status` field
on each child.

When the project moves back **out of Someday** (e.g. to Next), every
someday child is reactivated:

- If the child has `prev_status` set, its prior bucket is restored
  (e.g. `waiting` items return to `waiting`, preserving the
  `waiting_for` blocker).
- If not, the child defaults to `next`.

This means round-tripping a project through Someday is non-destructive
for items the cascade carried with it. Children that were
*independently* `someday` before the cascade are also reactivated (they
were skipped on the way down but get pulled back along with the
project).

To shelve a single sub-action without affecting siblings, change just
that item's status — the cascade only fires on project status changes.


## Handoff to another agent

If you want to work on an item with a different agent (e.g. open a new
mngr agent for a specific project), use **Copy for handoff** on the item
or project. It produces a markdown block containing the item's id,
title, all metadata, and the accumulated body notes — paste it into the
other chat to bring that agent up to speed instantly.


## Weekly Review

Click **Weekly Review** in the Time group of the nav. It opens
`/weekly-review`, an editable page with the contents of
`data/.apps/gtd/weekly_review_prompt.md`. Edit the prompt if you want to
change how the review runs, then **Copy to clipboard** and paste into
chat — the agent walks you through the structured review defined in
that file.


## Read/Review and Readwise

The Read & Review view is a flat checklist of things to consume. Today
there is no automation — when you've decided what to send to Readwise,
do it manually, then mark the items `done` or `reference`.


## Where things live in the code

```
system/apps/gtd/
  src/gtd/
    data_types.py            # Item, ItemStatus, Priority (frozen pydantic models)
    storage.py               # read/write/list items; instructions/review file IO
    runner.py                # FastAPI app: routes, view helpers, cascade logic
    storage_test.py          # unit tests for storage round-trips + new fields
    runner_test.py           # integration tests for routes + inline endpoints
    assets/
      templates/             # Jinja2 templates, one per view
        _item_macros.html    # item_row + project_row + picker macros
        base.html            # masthead, nav, instructions block, Done section
        inbox.html / next.html / projects.html / project_detail.html / ...
      static/
        style.css            # commonplace-book aesthetic; cdrop picker styles
        app.js               # async inline-submit, custom dropdown, toast, fade-out
  test_gtd_ratchets.py       # standard ratchets at zero
.agents/skills/gtd-triage/SKILL.md   # the chat-side triage protocol
data/.apps/gtd/                 # the user's actual data
```


## URL prefix + storage root

The app reads two env vars at startup:

- **`ROOT_PATH`** — URL prefix; set to `/service/gtd` when run behind the
  workspace_server proxy, empty when running standalone (`uv run gtd`).
  FastAPI uses it to emit prefix-aware redirects and `url_for` links.
- **`GTD_ROOT`** — storage root directory. Defaults to `data/.apps/gtd`.
  Tests set this to a tmp dir so they don't pollute real data.


## Custom dropdown widget (`cdrop`)

The three inline pickers (priority, status, project) are not native
`<select>` elements. Native popups can't be styled consistently across
browsers, so a `<details>`-based widget renders the closed state as a
text-style summary and the open state as a `position: absolute` panel of
buttons. Each button is a form `<button type="submit" name="..." value="..."`>
that posts via `app.js` and updates the row in place.

This pattern is in `_item_macros.html` as the `picker_enum` and
`picker_parent` macros. Clicking an option fires a `submit` event, which
the global handler in `app.js` intercepts → fetch POST → success toast.

The native `<select>` popup is gone. Don't add new ones; use the picker
macros instead.


## Tradeoffs we accepted on purpose

- **Plain text files over SQLite.** Slower at scale, but: human-readable,
  diffable, hand-editable, agent-editable, and git-friendly. At personal
  scale (thousands of items) the perf cost is invisible.
- **Server-rendered HTML over a SPA.** Lower JS surface, easier to read,
  faster page loads. The JS in `app.js` only handles inline POSTs and the
  custom dropdown.
- **Chat-driven triage over button-driven triage.** The UI has Edit forms
  for when you know what you want; for the actual *deciding* it punts you
  back to chat with the right context preloaded.
- **Cascade preserves prior status via `prev_status`.** Costs one
  frontmatter field; gains round-trip safety on project Someday moves.
- **Inline edits don't auto-refresh the page.** The row stays visible
  with its new dropdown selection. If the new status no longer fits the
  current view, the row fades out smoothly (~1s). This is intentional —
  full page reloads were jarring.


## How to extend the system

- **Add a new view filter.** Add a route in `runner.py` that calls
  `_list_view(...)` with a different `status`, or write a custom view
  function for grouped layouts (see `view_next`, `view_someday`).
- **Add a new field to items.** Add it to `Item` in `data_types.py`,
  then to the serializer in `storage.py:write_item` and the parser in
  `storage.py:_item_from_post`, then surface it in the edit form
  (`templates/edit.html`) and the item-row macro
  (`templates/_item_macros.html`). Use a defensive default so existing
  files continue to round-trip.
- **Change the triage rules.** Edit `data/.apps/gtd/instructions.md`. No
  code changes required — the agent re-reads it on every triage
  conversation.
- **Change the weekly review.** Edit `data/.apps/gtd/weekly_review_prompt.md`.
- **Add a new inline endpoint.** Mirror `set_status_route` /
  `set_priority_route` in `runner.py` — they all read the existing item,
  update one field, and write it back. Wire it up via a `picker_*`
  macro or a date input in `_item_macros.html`.


## Tests

```bash
cd system/apps/gtd && uv run pytest --no-cov --cov-fail-under=0
```

48+ tests covering: storage round-trips (including priority, is_project,
quick, legacy `status: project` migration), every inline endpoint
(rename, set_status, set_priority, set_due_date, set_start_date), the
Today view, the priority sort in Next, the calendar's start/due-date
filtering, and the 15 standard ratchets at zero.

Tests not yet covered (because the design is still in flux): the
project→children cascade, the Someday/All grouped layouts, the All
view's filter semantics, the project-detail child sort order. Add tests
here when those stabilize.
