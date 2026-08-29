<!-- mcp-name: io.github.premanand8800/cogsession -->

# 🌳 CogSession

**Session memory for AI coding agents.** Your agent forgets everything when the context
window fills. CogSession remembers the parts worth keeping, and tells you when they stop
being true.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-orange.svg)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-83%20passing-brightgreen.svg)](tests/)

---

## The problem

```
Session 1 (context fills) → Session 2 starts fresh → Session 3 starts fresh
    Everything lost.          Repeats the mistakes.   Starts blind again.
```

You explain the codebase again. The agent tries the approach that already failed. The
constraint you agreed on in session 1 is gone by session 3.

The usual answer is "write better notes", which fails for the same reason all documentation
fails: **it is true when written and nobody notices when it stops being true.**

## What CogSession does

Three things, and the third is the one that does not exist elsewhere.

**1. It records without being asked.** A session opens on its own. Decisions, dead ends,
assumptions and errors are written as they happen, each stamped with the local time and the
repo state it happened at (`main@a1b2c3d+2`, where `+2` is dirty files).

**2. It makes that searchable without loading it.** Every session keeps a `session.md`:
append-only, one block per event, every entry line self-describing. So one `grep` answers a
question without pulling a file into context.

```bash
grep -A4 "dead_end"      .cogsessions/*/session.md   # what already failed
grep "2026-08-27 01:"    .cogsessions/*/session.md   # what happened that hour
grep "main@a1b2c3d"      .cogsessions/*/session.md   # what happened at that commit
```

**3. It tells you when what you wrote stops being true.** Record a claim with the command
that *proves* it. When the files it watches change, the proof is re-run:

```
[CogSession] 1 claim(s) no longer hold:
  ✗ the composite key includes the tenant column
    expected '1', got '0'
    asserted in: PR description, line 26
    proof: grep -c 'UNIQUE (a, b, c)' migrations/007_schema.sql
```

No model judgement involved. It stores the command that proved something and re-runs it.
Silence means everything still holds.

## Why the third one matters

Every expensive failure in three weeks of daily use reduced to one sentence: *something was
true when it was written and stopped being true.* A pull request description explaining a
schema the code no longer had. A comment naming a constraint that moved. A test asserting a
shape the implementation had dropped. A docstring contradicting its own function.

An agent cannot notice that from a transcript. A human notices it in review, which is the
expensive place. A stored proof notices it for free.

---

## What a session looks like on disk

```
.cogsessions/
├── sess_001_discover/
│   ├── session.md          ← greppable timeline, every entry timestamped + git-stamped
│   ├── handoff.md          ← the brief the next session reads first
│   ├── claims.json         ← assertions with the commands that prove them
│   ├── dead_ends.md        ← what failed and why, so it is not retried
│   ├── assumptions.md      ← what was assumed but never verified
│   ├── tasks.json          ← done / remaining / blocked
│   ├── decisions.json      ← flagged when made under high context pressure
│   ├── environment.json    ← the commands that restore a working state
│   ├── architecture.mermaid← auto-generated dependency diagram
│   └── session_log.jsonl   ← append-only machine log
├── sess_002_auth/          (parent: sess_001)
└── sess_003_payments/      (parent: sess_001, sibling of sess_002)
```

Sessions form a tree, like branches, because work does. `session_tree` shows it; `session_log`
is a `git log --oneline` across all of them.

---

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for the install script
- An MCP-capable agent. Built against Claude Code; also usable from Codex (see below)
- `git` is optional. Without it the journal records `no-git` and stays useful

## Install

```bash
pip install cogsession        # or: uv tool install cogsession
cogsession-admin install
```

Two commands on purpose. The first installs the MCP server; the second wires the
**hooks**, which is what makes CogSession record without being asked. A package
cannot write to `~/.claude/settings.json` on its own, so without the second command
you get eleven tools you must call by hand and none of the recording.

`cogsession-admin install` backs up your settings first, adds the six hooks
alongside anything already there, and **will not overwrite a status line you
already set**.

<details>
<summary>Installing from a clone instead (for working on CogSession itself)</summary>

```bash
git clone https://github.com/premanand8800/cogsession.git
cd cogsession
uv sync
uv run cogsession-admin install --repo .
```

`--repo` points the hooks at your checkout through `uv`, so edits take effect
without reinstalling.
</details>

The installer syncs dependencies with `uv`, registers the MCP server with Claude Code, and
writes the hooks that let it observe a session without being asked. It touches
`~/.claude/settings.json` and nothing inside your projects.

**It will not write to a file git tracks.** The handoff goes to `CLAUDE.local.md`, which is
auto-loaded the same way `CLAUDE.md` is but never committed. If that filename happens to be
tracked in your repo, CogSession refuses to write rather than dirtying your tree, and tells
you where the handoff is on disk instead. Add `.cogsessions/` to your `.gitignore`.

### The tools

Eleven MCP tools, in four groups:

| Group | Tools |
|---|---|
| Lifecycle | `session_init` · `session_checkpoint` · `session_load` · `session_status` |
| Recording | `session_update` |
| Searching | `session_search` · `session_log` · `session_tree` · `session_diagram` |
| Claims | `claim_record` · `claim_check` |

Plus six hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PreCompact`, `SessionEnd`) that do the recording you never have to ask for.

### What it does *not* do

Worth saying plainly, because it is the first thing people assume:

**CogSession does not store your conversations.** It reads the transcript only to measure how
full the context window is. What it keeps is conclusions — decisions, dead ends, assumptions,
claims — plus the mechanical events from the hooks. That is deliberate: a memory made of every
word said is a memory nobody re-reads. But it does mean the quality of a session's memory
depends on things being recorded as they are decided.

---

## Usage

**Start of every session: nothing.** The `SessionStart` hook opens a session and
loads the previous handoff on its own. Tracking that depends on someone
remembering to turn it on is tracking that silently does not happen.

Name the session when you know what it is about — the focus line is the only
part a tool cannot infer:
```
session_update(type="focus", content="auth module")
```

`session_init` still exists for a session you want to start deliberately, or to
attach to a parent:
```
session_init(project_root="/your/project", focus="auth module")
session_load(project_root="/your/project")   # load a previous handoff by hand
```

**Throughout the session:**
```
session_update(type="decision", content="Use python-jose for JWT", reasoning="Handles RS256 edge cases")
session_update(type="dead_end", content="Using httpx for auth", why_failed="Breaks streaming in /login", use_instead="Use requests library")
session_update(type="assumption", content="users table has hashed_password column", risk_level="HIGH", how_to_verify="Run \\d users in psql")
session_update(type="task_complete", content="Build /register endpoint")
session_update(type="task_add", content="Build /refresh endpoint")
session_update(type="danger_zone", content="Don't touch middleware.py — custom CORS order line 45")
session_status(context_pct=67)
```

**At 70-80% context:**
```
session_checkpoint(context_pct=78, one_liner="Built JWT auth. Refresh token next.")
```

**Look something up without loading anything.** Every session keeps a
`session.md`: append-only, one block per event, each header carrying its own
local timestamp, event type, and the repo state it happened at
(`branch@commit+dirty`). So one `grep` answers a question:

```
grep -n -A4 "dead_end"  .cogsessions/<session>/session.md   # what already failed
grep -n "2026-08-27 01:" .cogsessions/<session>/session.md   # what happened that hour
grep -n "main@a1b2c3d"   .cogsessions/<session>/session.md   # what happened at that commit
```

Every entry line is self-describing, which is what makes a bare `grep` useful:
a match tells you when, what kind, and against which state of the code, with no
need to scroll for context. Tool calls are deliberately left out — hundreds per
session would bury the decisions someone is actually searching for; they stay in
`session_log.jsonl`.

**Scan history like `git log`:**
```
session_log()                        # newest first, all sessions
session_log(type_filter="dead_end")  # what has already failed here
```

```
when                       what         repo                   session
2026-08-27 01:43:57 +0545  error        master@3d4d1fd+2       sess_20260827_...
2026-08-27 01:43:57 +0545  dead_end     master@3d4d1fd+1       sess_20260827_...
```

Scan, then grep the journal for the entry that matters. The commit id is the
join back to real `git log`, so a decision can be lined up with the state of
the code that produced it.

**Claims — for anything you write down that could go stale:**
```
claim_record(
  claim="the composite key includes the tenant column",
  verified_by="grep -c 'UNIQUE (a, b, c)' migrations/007_schema.sql",
  expect="1",
  watches=["migrations/007_schema.sql"],
  asserted_in="PR description, line 26",
)
claim_check()          # re-runs the proofs whose files moved
```

A claim stores the *command that proved it*, not a note about how to check it.
When the file changes, the next session is told which statements stopped being
true, where they were asserted, and how they were checked. Silence means
everything still holds.

This exists because the most expensive failure is not a wrong decision. It is a
right one that quietly stopped being true — a description of a schema the code
no longer has, a comment naming a constraint that moved, a test asserting a
shape the implementation dropped.

**Explore history:**
```
session_tree(project_root="/your/project")
session_search(project_root="/your/project", query="httpx", type_filter="dead_end")
session_diagram(project_root="/your/project")
```

---

## How It Works: Inverted Control (Observer-First)

CogSession operates automatically via agent hooks and transcript inspection. **You don't need to manually report token percentages or call tools.**

1. **Automatic Context Measurement**: Context load is read directly from Claude Code session transcripts (`input_tokens + cache_creation + cache_read + output_tokens`).
2. **Automatic Injection**:
   - `SessionStart`: Injects L0 manifest & L1 handoff unprompted.
   - `UserPromptSubmit`: Nudges at 65%–74%, recommends at 75%–79%, and mandates checkpoints at $\ge$80%. Surfaces prompt-relevant dead ends and danger zones.
   - `PreToolUse`: Blocks file edits targeting recorded danger zones.
3. **Deterministic Distillation**: Tracks file edits, git operations, commands, and test failures without relying on LLM guesses.

---

## What Makes It Different

| Feature | Other Systems | CogSession |
|---|---|---|
| Dead ends tracking | ❌ | ✅ Automatic extraction of failed approaches & reasons |
| Context load measurement | ❌ Guesswork | ✅ Ground truth token measurement from transcript |
| Decision quality flags | ❌ | ✅ Automatically flagged if made at >75% context |
| Tree structure | ❌ linear | ✅ Branches like git |
| Token-aware warnings | ❌ | ✅ Automatic: 65% nudge, 75% alert, 80% mandate |
| Auto CLAUDE.md handoff | ❌ | ✅ Handoff written automatically at checkpoint |
| Architecture diagram | ❌ | ✅ Auto-generated Mermaid |
| Environment snapshot | ❌ | ✅ Exact start commands, ports, env vars |


---

## Connect

CogSession is an MCP stdio server. If `cogsession` is installed in the
environment where your agent runs, register it with `uv run cogsession`.

**Codex CLI:**
```bash
codex mcp add cogsession -- uv run cogsession
```

If you are running CogSession directly from a local source checkout, point `uv`
at that checkout:

```bash
codex mcp add cogsession -- uv --directory /path/to/cogsession run cogsession
```

Verify the server is registered:
```bash
codex mcp list
codex mcp get cogsession
```

Restart Codex after adding the MCP server. Codex loads MCP tools when a new
Codex session starts.

**Claude Code:**
```bash
claude mcp add cogsession -- uv run cogsession
```

For a local source checkout:

```bash
claude mcp add cogsession -- uv --directory /path/to/cogsession run cogsession
```

**Cursor** (`.cursor/mcp.json`):
```json
{"mcpServers": {"cogsession": {"command": "uv", "args": ["run", "cogsession"]}}}
```

For a local source checkout:

```json
{
  "mcpServers": {
    "cogsession": {
      "command": "uv",
      "args": ["--directory", "/path/to/cogsession", "run", "cogsession"]
    }
  }
}
```

**Disable for a project:**
```bash
echo '{"enabled": false}' > .cogsession.json
```

---

## Using CogSession with Codex

CogSession is project-local. It stores session data inside the target project:

```text
/your/project/.cogsessions/
```

Start Codex from the project you want to remember:

```bash
cd /your/project
codex
```

Then ask Codex to use CogSession in plain language:

```text
load the session and handoff from cogsession
```

or be explicit:

```text
Use cogsession to load the latest handoff for this project.
```

If this is the first session for the project:

```text
Use cogsession to initialize a session for this project with focus "initial setup".
```

During work, record important facts:

```text
Use cogsession to record a decision: "Use the existing service layer for account updates" because "It keeps validation and audit logging in one place".
```

```text
Use cogsession to record a dead end: "Calling the external API directly from the route handler" because "It bypassed retries and request tracing" and use instead "Call the existing API client wrapper".
```

Before stopping, checkpoint the session:

```text
Use cogsession to checkpoint this session with context 64% and summary "Implemented account update flow; remaining work is integration tests."
```

### Codex Example

```text
developer@workstation:~/projects/example-app$ codex
╭───────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.130.0)                    │
│                                               │
│ model:     gpt-5.5   /model to change         │
│ directory: ~/projects/example-app             │
╰───────────────────────────────────────────────╯

› load the session and handoff from cogsession

• Called
  └ cogsession.session_load({
      "project_root": "/home/developer/projects/example-app",
      "load_level": "handoff"
    })

[CogSession] Handoff from sess_20260513_101500_account_updates_a1b2:

# Handoff: sess_20260513_101500_account_updates_a1b2

**Focus:** Account update flow
**Closed:** 64% context | 2026-05-13 23:33
**Parent:** root

## Summary
Account update flow is implemented, validation is wired through the existing
service layer, and lint/type checks are passing. Remaining work is integration
test coverage for error cases.
```

After loading the handoff, Codex can continue with the saved context:

```text
I implemented the account update flow using the existing service layer:

- Added src/services/account_service.py for update orchestration.
- Updated src/api/account_routes.py to call the service layer.
- Added validation in src/schemas/account.py.
- Documented required environment variables in .env.example.

Verification:
pytest             passed
ruff check .       passed
mypy src           passed

Remaining work:
- Add integration tests for validation failures.
- Confirm production environment variables before release.
```

### Project-Local vs Global

CogSession does not use one global memory store by default. Each project gets
its own `.cogsessions/` directory.

For this project:

```text
/home/developer/projects/example-app/.cogsessions/
```

For another project:

```text
/home/developer/projects/admin-tool/.cogsessions/
```

Those are separate histories. To inspect a specific project, ask Codex:

```text
Use cogsession to show the session tree for /home/developer/projects/example-app.
```

Codex may show an approval prompt before running a CogSession tool:

```text
Allow the cogsession MCP server to run tool "session_tree"?

1. Allow
2. Allow for this session
3. Always allow
4. Cancel
```

Choose `Allow for this session` or `Always allow` if you want fewer prompts.

### Useful Codex Prompts

```text
Use cogsession to load the latest handoff for this project.
```

```text
Use cogsession to initialize a new session for this project with focus "auth fixes".
```

```text
Use cogsession to show the session tree for this project.
```

```text
Use cogsession to search this project for "account update".
```

```text
Use cogsession to checkpoint this session with context 70% and summary "Implemented auth changes and recorded build blocker."
```
