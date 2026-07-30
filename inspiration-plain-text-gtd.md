---
title: Plain-Text GTD
description: A calm, plain-text GTD (Getting Things Done) task manager -- inbox, next actions, projects, calendar, and weekly review -- backed by markdown files and served as a ruled-paper web app.
thumbnail: inspiration-plain-text-gtd.svg
version: v1
format: v1
---

# Plain-Text GTD

This file is the manifest for the **Plain-Text GTD** inspiration (slug:
`plain-text-gtd`). It is the one document a future agent reads to understand,
present, and adapt this inspiration. If you are an agent in a mind that was
created from this inspiration, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A calm, plain-text GTD (Getting Things Done) task manager -- inbox, next actions, projects, calendar, and weekly review -- backed by markdown files and served as a ruled-paper web app.

It solves the problem of a scattered, stressful todo list by giving you a
single trusted place to capture everything and a calm workflow for deciding
what to actually do. There is no database and no cloud service: every task is
an ordinary markdown file with a little YAML header, living under
`data/.apps/gtd/items/<id>.md`, and the whole thing is served as a warm,
ruled-paper "commonplace book" web page you open as a workspace tab. A
left-hand nav clusters by mental mode -- Active (Inbox, Next Actions, Projects,
Waiting, Tickler), Holding (Someday/Maybe, Read & Review, Reference), and Time
(Today, Calendar, Weekly Review) -- each rendering the matching tasks as
editable rows. You capture by pasting a block of lines into the Inbox (one task
per line), and you process by moving items into buckets, setting due/start
dates, grouping actions under projects, and running a weekly review. The
distinctive move: the actual thinking happens in chat with an agent -- each row
has "Triage in chat", "Plan", and "Handoff" buttons that copy a ready-made
prompt to paste into your agent, and a fresh install shows a Getting Started
panel.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/gtd`
- `system/supervisord.conf`
- `pyproject.toml`
- `uv.lock`

- `system/apps/gtd` is the entire application: a FastAPI + Jinja2 web service
  (no database) whose storage layer reads and writes one markdown file per task.
  It ships the routes, templates, static CSS/JS, and the seed defaults
  (`instructions.md`, `weekly_review_prompt.md`) that populate a fresh install.
- `system/supervisord.conf` carries the `[program:gtd]` block that boots it.
- `pyproject.toml` registers `gtd` as a workspace dependency and source so the
  monorepo resolves and installs it.
- `uv.lock` locks that resolution, including `gtd` and its dependencies.

At runtime the `[program:gtd]` supervisord program runs
`python3 system/scripts/forward_port.py --url http://localhost:8082 --name gtd`
to register the app with the workspace's reverse proxy, then launches it with
`ROOT_PATH=/service/gtd uv run gtd`. The app binds uvicorn to
`127.0.0.1:8082`; `ROOT_PATH=/service/gtd` makes FastAPI emit prefix-aware
links so it serves correctly behind the system_interface reverse proxy at the
`/service/gtd/` tab. Three environment variables tune it: `ROOT_PATH` (the URL
prefix; empty when run standalone via `uv run gtd`), `GTD_ROOT` (the storage
directory, default `data/.apps/gtd`), and `GTD_PORT` (the listen port, default
8082). All task and context files live under `GTD_ROOT` -- items at
`data/.apps/gtd/items/<id>.md`, plus the editable context files
`data/.apps/gtd/instructions.md`, `data/.apps/gtd/weekly_review_prompt.md`,
`data/.apps/gtd/projects_and_goals.md`, and `data/.apps/gtd/intentions.md`.

## Recipe

This inspiration is version `v1` (front-matter `version:`).
It is not a fork of the workspace it came from -- it is DERIVED from it by the
recipe below: include these paths, leave these out, apply these
published-version rules. An update re-runs the recipe against the current
workspace and publishes the result as the next version, so anything excluded
here stays excluded even though it still exists in the source workspace. This
block is the durable home of that recipe -- a later update reads it back from
here.

```yaml
version: v1
include:
  - system/apps/gtd
  - system/supervisord.conf
  - pyproject.toml
  - uv.lock
data_include: []
exclude:
  []
modification_rules:
  []
```

## Prerequisites

Activation requirements: what the adopting agent must SET UP -- and must
INITIATE ITSELF during setup, before asking how to adapt -- for this
inspiration to run against the new user's own accounts/data. One line per
requirement, in this machine-readable form (greppable by `requires_`):

No prerequisites -- runs with no external permissions or secrets.

The app is entirely filesystem-backed, reaches no external service, has no
accounts/API keys/tokens, and calls no LLM directly (the "Triage in chat" /
"Plan" / "Handoff" buttons copy prompts the user pastes into their own chat
agent).

## How to adapt it

Instructions for the NEXT agent -- the one adapting this inspiration into a
new mind. This is the `use-inspiration` skill's template path; in short:

1. Read this entire file first, especially "Prerequisites" and "Holes"
   below -- Prerequisites are your SETUP agenda, Holes are your ADAPTATION
   agenda.
2. Present the inspiration to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the Prerequisites).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each hole interactively, one at a time. Translate each into
   plain language, ask for a decision only when you genuinely need one, and
   resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Holes

- The seeded triage rules (`data/.apps/gtd/instructions.md`) and weekly-review
  prompt (`data/.apps/gtd/weekly_review_prompt.md`) are generic starters; the
  adopter should rewrite both to fit their own contexts/projects/priorities
  (editable from the UI at `/instructions` and `/weekly-review`, or on disk).
- It is built for a human + AI-agent loop. The triage/plan/handoff buttons copy
  prompts meant to be pasted into the adopter's chat agent, which decides and
  writes results back to the item files. The snapshot preserves the button
  prompts but not the original `gtd-triage` skill; reconnecting those prompts to
  the adopter's agent (re-creating/adapting a triage skill) is the main
  adaptation. Without an agent it is still a fully functional manual GTD tool.
- Two optional context features start empty: Projects & Goals
  (`data/.apps/gtd/projects_and_goals.md`) and Daily Intentions
  (`data/.apps/gtd/intentions.md`) are blank until the adopter writes them.
- Single-user, no authentication -- binds to localhost, served at `/service/gtd/`
  through the template proxy; add auth before exposing it anywhere else.
- The port is hardcoded-by-default (8082, overridable via `GTD_PORT`, referenced
  in the `[program:gtd]` block); data lives in gitignored `data/.apps/gtd/`,
  captured by the host backup in this template.

## Publication history

This inspiration's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-07-30) -- First publish of Plain-Text GTD migrated onto the current workspace template (system/apps layout, data/.apps storage).

## Adaptation history

Each mind that adapts this inspiration appends one dated entry below. Earlier
entries are never rewritten.
