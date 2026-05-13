# 🌳 CogSession

**Session memory for AI coding agents — tree-structured, handoff-driven, context-aware.**

When Claude Code hits its context limit, you lose everything. CogSession fixes that with a developer-first session handoff system — organized like git branches, designed for real handoffs between sessions.

---

## The Problem

```
Session 1 (hits 80% context) → Session 2 starts fresh → Session 3 starts fresh
    Everything lost.              Repeat the mistakes.    Start blind again.
```

## The Solution

```
.cogsessions/
├── sess_001_discover/
│   ├── handoff.md          ← New session reads THIS first
│   ├── dead_ends.md        ← What failed and why (don't repeat it)
│   ├── assumptions.md      ← What was assumed but not verified
│   ├── tasks.json          ← Done / remaining / blocked
│   ├── decisions.json      ← Decisions flagged by context quality
│   ├── environment.json    ← Exact commands to restore working state
│   ├── architecture.mermaid← Auto-generated codebase diagram
│   └── session_log.jsonl   ← Timestamped append-only event stream
├── sess_002_auth/          (parent: sess_001)
└── sess_003_payments/      (parent: sess_001, sibling of sess_002)
```

---

## Install

```bash
bash scripts/install.sh
```

---

## Usage

**Start of every session:**
```
session_init(project_root="/your/project", focus="auth module")
session_load(project_root="/your/project")   # load previous handoff
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

**Explore history:**
```
session_tree(project_root="/your/project")
session_search(project_root="/your/project", query="httpx", type_filter="dead_end")
session_diagram(project_root="/your/project")
```

---

## What Makes It Different

| Feature | Other Systems | CogSession |
|---|---|---|
| Dead ends tracking | ❌ | ✅ What failed and why |
| Assumption risk levels | ❌ | ✅ HIGH/MEDIUM/LOW + how to verify |
| Decision quality flags | ❌ | ✅ Flagged if made at >75% context |
| Tree structure | ❌ linear | ✅ Branches like git |
| Token-aware warnings | ❌ | ✅ Warn at 65%, alert 75%, checkpoint 80% |
| Auto CLAUDE.md handoff | ❌ | ✅ Handoff written at checkpoint |
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
