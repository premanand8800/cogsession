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

**Codex CLI:**
```bash
codex mcp add cogsession -- uv --directory /path/to/cogsession run cogsession
```

Example, if this repo is at `/home/prem/Desktop/cogsession`:
```bash
codex mcp add cogsession -- uv --directory /home/prem/Desktop/cogsession run cogsession
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

**Cursor** (`.cursor/mcp.json`):
```json
{"mcpServers": {"cogsession": {"command": "uv", "args": ["run", "cogsession"]}}}
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
Use cogsession to record a decision: "Use Resend for password reset email" because "It is already configured for production email delivery".
```

```text
Use cogsession to record a dead end: "Next build failed" because "next-env.d.ts is owned by nobody:nogroup" and use instead "Fix file ownership before rerunning npm run build".
```

Before stopping, checkpoint the session:

```text
Use cogsession to checkpoint this session with context 64% and summary "Forgot-password email flow is implemented; build is blocked by next-env.d.ts permissions."
```

### Codex Example

```text
prem@Inspiron-15-3511:~/Desktop/resturent-bot/QUICKSERVE$ codex
╭───────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.130.0)                    │
│                                               │
│ model:     gpt-5.5   /model to change         │
│ directory: ~/Desktop/resturent-bot/QUICKSERVE │
╰───────────────────────────────────────────────╯

› load the session and handoff from cogsession

• Called
  └ cogsession.session_load({
      "project_root": "/home/prem/Desktop/resturent-bot/QUICKSERVE",
      "load_level": "handoff"
    })

[CogSession] Handoff from sess_20260513_231619_initialize_session_t_4cdf:

# Handoff: sess_20260513_231619_initialize_session_t_4cdf

**Focus:** Initialize session tracking for QUICKSERVE
**Closed:** 64% context | 2026-05-13 23:33
**Parent:** root

## Summary
Forgot-password fix branch is ready with Resend email support, normalized
lookup input, docs, and passing lint/TypeScript.
```

After loading the handoff, Codex can continue with the saved context:

```text
I fixed the forgot-password flow by adding real email delivery support through
Resend:

- Added src/lib/email.ts for password reset emails.
- Updated src/app/api/auth/forgot-password/route.ts to send the reset link.
- Normalized restaurant slug and email in src/lib/validations.ts.
- Documented required production vars in .env.example:
  RESEND_API_KEY=
  EMAIL_FROM="QuickServe <noreply@yourdomain.com>"

Verification:
npm run lint       passed
npx tsc --noEmit   passed
git diff --check   passed

npm run build compiled successfully after network approval, but final build
verification is blocked because next-env.d.ts is owned by nobody:nogroup and
Next cannot write it.
```

### Project-Local vs Global

CogSession does not use one global memory store by default. Each project gets
its own `.cogsessions/` directory.

For this project:

```text
/home/prem/Desktop/resturent-bot/QUICKSERVE/.cogsessions/
```

For another copy of QuickServe:

```text
/home/prem/Downloads/QUICKSERVE-QuickServe/.cogsessions/
```

Those are separate histories. To inspect a specific project, ask Codex:

```text
Use cogsession to show the session tree for /home/prem/Desktop/resturent-bot/QUICKSERVE.
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
Use cogsession to search this project for "forgot-password".
```

```text
Use cogsession to checkpoint this session with context 70% and summary "Implemented auth changes and recorded build blocker."
```
