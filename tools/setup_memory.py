#!/usr/bin/env python3
"""Create/update the persistent Codex Jarvis memory vault.

The vault lives outside the git repositories so upgrades never overwrite user
memory. This script is conservative: create missing structure, never replace a
user's existing notes.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: setup_memory.py /path/to/agent-home")

home = Path(sys.argv[1]).expanduser().resolve()
vault = home / "Memory"

dirs = [
    vault / "00 - Inbox",
    vault / "01 - Daily Notes",
    vault / "02 - Projects",
    vault / "03 - Routines",
    vault / "04 - People",
    vault / "05 - Resources" / "Jobs",
    vault / "99 - Archive",
]
for d in dirs:
    d.mkdir(parents=True, exist_ok=True)

def create(path: Path, content: str):
    if path.exists():
        return False
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    print(f"[codex-jarvis] memory: created {path.relative_to(vault)}")
    return True

def ensure_block(path: Path, marker: str, content: str) -> bool:
    """Append a durable migration block without replacing user-authored text."""
    if not path.exists():
        return False
    current = path.read_text(encoding="utf-8")
    if marker in current:
        return False
    updated = current.rstrip() + "\n\n" + content.rstrip() + "\n"
    path.write_text(updated, encoding="utf-8")
    print(f"[codex-jarvis] memory: updated {path.relative_to(vault)}")
    return True

create(vault / "VAULT-INDEX.md", """---
status: active
project: meta
type: index
---
# Jarvis Memory Vault

This is Jarvis's persistent memory. It is the durable source of truth across
fresh Codex threads and restarts. Keep only information that helps future work.

## How to use this vault

At session start Jarvis loads this file, [[Active Priorities]], and the newest
daily note. For everything else, retrieve only the notes relevant to the task.

When the user says "remember this", persist it immediately in the most relevant
note. Project decisions belong with that project. Repeated working patterns
belong in Routines. Stable preferences belong here. People belong in People.
Use the Inbox only when the correct home is genuinely unclear.

Do not store passwords, API keys, access tokens, recovery codes, or secrets.
Record the name/location of the secure store instead.

## Working preferences

- Address the user as Boss.
- Be direct, capable, witty, and concise in voice conversations.
- Preserve important decisions, proven fixes, recurring workflows, and project
  state so future sessions do not pay the same discovery cost again.
- Do not turn the vault into a transcript. Save durable context, not chatter.
- Before claiming a project state is current, verify it from the real system
  when practical.

## Vault map

- [[Active Priorities]] - current cross-project work.
- [[Projects]] - project index and current state.
- [[Routines]] - recurring workflows and habits.
- [[People]] - people and roles useful to ongoing work.
- [[Jobs]] - repeatable tasks and the notes required to perform them.
- 01 - Daily Notes - concise dated session checkpoints.
- 99 - Archive - completed/retired material.

## Profile

Build this section gradually from information the user explicitly shares with
Jarvis and wants retained. Keep it useful and concise.
""")

create(vault / "Active Priorities.md", """---
status: active
project: meta
type: plan
---
# Active Priorities

Keep only currently open work here. Link each item to its project note.

- [ ] Keep this list current as projects and routines evolve.
""")

create(vault / "02 - Projects" / "Projects.md", """---
status: active
project: meta
type: index
---
# Projects

One durable note per active project. Each project note should record purpose,
current status, architecture/stack, important decisions, next actions, and
proven operational methods.
""")

create(vault / "02 - Projects" / "Codex Jarvis.md", """---
status: active
project: codex-jarvis
type: reference
---
# Codex Jarvis

## Purpose

A persistent desktop AI assistant using OpenAI Codex as the brain, local speech
recognition, local low-latency speech output, Jared Rhodenizer's Backtalk /
AI Visualizer foundation, and this memory vault for continuity.

## Current architecture

Push-to-talk -> faster-whisper -> persistent Codex app-server -> streamed voice
chunks -> Piper local TTS -> speakers + AI Visualizer signal bus.

## Current decisions

- Push-to-talk on Ubuntu Wayland uses the visualizer-focused V key bridge.
- Piper is the default local voice because it removed the multi-second
  sentence gaps seen with Kokoro on this laptop.
- Kokoro remains installed as an alternate/fallback voice.
- Barehands is intentionally out of scope.
- Persistent memory is a core requirement, not an optional feature.
- The assistant addresses the user as Boss and uses a sharp, sarcastic,
  sysadmin-friend personality.

## Definition of useful

Jarvis should launch in one action, remember durable project/routine context
across fresh Codex sessions, retrieve deeper notes on demand, and save new
decisions without needing the user to repeat them.
""")

create(vault / "02 - Projects" / "Freelance Profile.md", """---
status: active
project: freelance-agent
type: profile
---
# Freelance Profile

This file is the source of truth for claims Jarvis may make in freelance
proposal drafts. Keep it factual and update it when experience changes.

## Verified strengths

- Add only skills and platforms the user can genuinely deliver with.

## Verified experience

- Add concise, truthful experience that can be used in client proposals.

## Portfolio / proof

- Add links or descriptions only for real work the user is allowed to share.

## Certifications

- List only completed certifications. Put studying/in-progress items in the
  section below, never present them as completed.

## In progress

- Add technologies/certifications currently being learned.

## Proposal rules

- Never invent years of experience.
- Never invent client names, outcomes, certifications, or portfolio projects.
- If a job needs a capability not verified here, call that out instead of
  pretending.
""")

create(vault / "02 - Projects" / "Freelance Business.md", """---
status: active
project: freelance-agent
type: reference
---
# Freelance Business

## Goal

Use Jarvis as a freelance co-pilot: find suitable opportunities, analyze fit,
prepare tailored proposals, organize client work, and help deliver projects
efficiently while keeping account submissions and commercial commitments under
human control.

## Target work

Prioritize opportunities around demonstrated technical areas such as DevOps,
Linux/system administration, cloud infrastructure, automation, containers,
Kubernetes, Microsoft/Windows infrastructure, and related operational work.

Before claiming any specific experience, certification, tool depth, or past
result in a proposal, verify it from the user's real profile/history. Never
inflate credentials.

## Operating model

1. Capture opportunities from read-only browser discovery or job-alert email.
2. Analyze requirements, risk, effort, and fit.
3. Shortlist credible work.
4. Draft a job-specific proposal.
5. User performs platform submission when eligible.
6. If accepted, create a delivery workspace.
7. Jarvis and the user build, test, document, and review the deliverable.
8. User approves final delivery.

## Guardrails

- No automated proposal/bid submission.
- No automated Connect spending.
- No automated client messaging or contract acceptance.
- No bypassing account age, identity, verification, or eligibility rules.
- No invented skills, portfolio work, experience, or outcomes.
""")

create(vault / "05 - Resources" / "Jobs" / "Freelance Agent.md", """---
status: active
project: freelance-agent
type: job
---
# Freelance Agent

## Trigger

Run daily from job-alert email, or on demand while the authenticated freelance
job feed is open in the Jarvis browser.

## Workflow

- Scan and deduplicate opportunities.
- Inspect promising jobs in detail.
- Evaluate technical fit, missing requirements, delivery risk, and effort.
- Save fit analysis and shortlist credible work.
- Draft a concise tailored proposal.
- Keep platform submission manual.
- Track interview, active project, delivery, and outcome state.
- For accepted work, create a local project workspace and record scope and
  acceptance criteria before implementation.

## Commands

- jarvis-freelance scan-browser
- jarvis-freelance scan-email
- jarvis-freelance list
- jarvis-freelance show ID
- jarvis-freelance analyze ID
- jarvis-freelance proposal-save ID
- jarvis-freelance status-set ID STATUS
- jarvis-freelance workspace ID
- jarvis-freelance digest

## Source of truth

Structured state is local in Jarvis's freelance SQLite database. A readable
dashboard is generated at [[Freelance Opportunities]].
""")

create(vault / "03 - Routines" / "Routines.md", """---
status: active
project: personal
type: index
---
# Routines

Record recurring workflows, schedules, checklists, and working habits here as
Jarvis learns them. Prefer one maintained source of truth per routine.
""")

create(vault / "04 - People" / "People.md", """---
status: active
project: personal
type: index
---
# People

Record people only when their role/context is useful for future work. Keep this
practical and avoid unnecessary personal detail.
""")

create(vault / "05 - Resources" / "Jobs" / "Jobs.md", """---
status: active
project: meta
type: index
---
# Jobs

A Job is a repeatable task. Each Job note should say what triggers the task,
what context to read first, the workflow, expected output, and lessons learned.
""")

create(vault / "01 - Daily Notes" / "Daily Note Template.md", """---
status: active
project: personal
type: log
created: {{date}}
---
# {{date}}

## Session

### What changed
-

### Decisions
-

### Still open
-

### Memory updated
-
""")

today = datetime.now().astimezone()
month = vault / "01 - Daily Notes" / today.strftime("%m - %B %Y")
month.mkdir(parents=True, exist_ok=True)
daily = month / f"{today:%Y-%m-%d}.md"
create(daily, f"""---
status: active
project: codex-jarvis
type: log
created: {today:%Y-%m-%d}
---
# {today:%A, %B %d, %Y}

## Session 1 - {today:%H:%M}: Persistent memory

### What changed
- Initialized the Jarvis Memory vault outside the git repositories.
- Wired the design around project memory, routines, active priorities, and
  concise daily checkpoints.

### Decisions
- Persistent memory is a core Jarvis feature.
- Barehands is not part of the target build.
- Voice + face + memory + one-action launch define the current personal MVP.

### Still open
- Populate project/routine notes naturally as the user works with Jarvis.

### Memory updated
- [[Codex Jarvis]]
- [[Active Priorities]]
""")

ensure_block(
    vault / "02 - Projects" / "Codex Jarvis.md",
    "<!-- JARVIS-PERSONAL-AGENT-REQUIREMENTS -->",
    """<!-- JARVIS-PERSONAL-AGENT-REQUIREMENTS -->
## Personal-agent requirements

- Credentials must use OAuth or the OS credential vault; never store secret
  values in Memory or source control.
- Gmail and Calendar should support automatic read-only daily briefings.
- Consequential external actions require one-time scoped Boss approval unless
  that exact action class was explicitly pre-authorized.
- The long-term mobile client is an iPhone interface to the same Jarvis Core and
  the same Memory vault, not a separate assistant with separate memory.
- Website automation should prefer authenticated browser sessions over handing
  raw passwords to the model.
""",
)

ensure_block(
    vault / "02 - Projects" / "Codex Jarvis.md",
    "<!-- JARVIS-BROWSER-UBUNTU-VISUAL -->",
    """<!-- JARVIS-BROWSER-UBUNTU-VISUAL -->
## Browser, Ubuntu, and visual-answer requirements

- Jarvis uses a dedicated persistent Chrome profile for browser automation.
- The user signs into Google/browser accounts manually once when possible;
  Jarvis reuses authenticated browser sessions rather than storing Google
  passwords.
- Website passwords explicitly entrusted to Jarvis live only in Linux Secret
  Service. API keys use the same secret vault.
- Read-only browsing/searching may run automatically. Login submissions,
  consequential clicks, and Ubuntu state-changing commands use one-time scoped
  approval.
- Jarvis may manage the Ubuntu user environment, but must not disable Codex
  sandboxing, configure passwordless sudo, or store the sudo password.
- When the user asks to show a product/place/object and visuals help, Jarvis may
  open a desktop information card with an image, concise description, and
  source link after gathering current information.
""",
)

ensure_block(
    vault / "02 - Projects" / "Codex Jarvis.md",
    "<!-- JARVIS-FREELANCE-AGENT -->",
    """<!-- JARVIS-FREELANCE-AGENT -->
## Freelance co-pilot

- Jarvis maintains a local freelance opportunity pipeline and delivery
  workspaces.
- Daily recurring discovery should prefer job-alert email over aggressive
  browser automation.
- Jarvis may analyze jobs and prepare proposals, but platform submission,
  Connect spending, client messaging, contract acceptance, and account changes
  remain manual human actions.
- Accepted client work can be moved into a local workspace so Jarvis and the
  user can build, test, document, and prepare delivery together.
""",
)

print(f"[codex-jarvis] memory vault: {vault}")
