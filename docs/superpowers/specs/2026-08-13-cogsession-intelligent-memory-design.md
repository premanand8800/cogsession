# CogSession: Intelligent Memory Management

**Design specification — 2026-08-13**

## 1. Purpose

CogSession should give a developer working with an AI coding agent a memory
that manages itself. The developer installs it once and never thinks about
memory again: context is measured, work is recorded, and the relevant past is
handed back to the agent at the moment it matters.

Today CogSession does not do this. It exposes eight MCP tools that the agent
must remember to call, with a context percentage the agent must guess. In
practice the agent forgets, the percentage is always zero, and the features
built on top of it are inert.

This document specifies the redesign. It is forward-only: the on-disk store,
the data model, and the tool signatures are preserved.

## 2. The problem, precisely

Four independent causes produce the reported symptoms — no token tracking and
no forced checkpoint at 80% context.

| Symptom | Cause |
|---|---|
| Nothing is tracked at all | Hooks were never installed. `~/.claude/cogsession/` does not exist; `~/.claude/settings.json` has no `statusLine` and an empty `hooks` array. `install.sh` prints "manually merge" when `settings.json` already exists, and does nothing. |
| Hooks would still not run | All five hooks are pure `jq` under `set -euo pipefail`. `jq` is not installed on the target machine, so each hook dies at its first line of real work, silently. |
| The 80% trigger fires into nothing | `statusline.sh` writes `/tmp/cogsession_checkpoint_$SESSION_ID` with the comment "MCP server polls this". A repository-wide search for `cogsession_checkpoint` returns exactly one hit: the writer. There is no reader. |
| Even installed and working, it could not force the agent | `statusLine` output is display-only. Its stdout never enters model context. The auto-checkpoint mechanism was wired to the one hook type that is structurally incapable of influencing the model. |

The consequence chain: real context percentage is read only by the statusline
and then discarded; the logging hooks hardcode `"context_pct":0`; the MCP
server accepts `context_pct` as an agent-supplied argument. Therefore the
decision-confidence flags in `models.py` and `token_pct_at_close` in
`loader.py` are permanently zero. The README advertises "Warn at 65%, alert
75%, checkpoint 80%" and "Decision quality flags — flagged if made at >75%
context". Both are dead.

## 3. Principle: invert control

**The system observes; the agent never reports.**

Every conclusion below follows from this one line.

### 3.1 Ground truth moves to the transcript

Claude Code passes `transcript_path` to *every* hook. That JSONL file records
one entry per turn, and each assistant entry carries a `message.usage` block.
Live context load is:

```
input_tokens
  + cache_creation_input_tokens
  + cache_read_input_tokens
  + output_tokens
```

taken from the **last** assistant message. This was verified against a live
transcript during design: the computation returned 65,025 tokens for the
session in progress, matching observed reality.

So any hook can know the truth. The statusline is demoted from plumbing to
decoration — plus one narrow job described in §5.1.

### 3.2 Writing becomes extraction, not reporting

Files touched, tests run and their pass/fail status, commands executed, git
operations, and error text are all present in the hook payloads and the
transcript. None of it needs to be volunteered by the agent. Dead ends —
edit, test fails, different approach, test passes — are visible to a reader of
the transcript and invisible to a tool-call API that depends on the agent
choosing to describe its own failure.

### 3.3 Delivery becomes injection, not invitation

Prior memory is injected at `SessionStart` before the developer types.
Pressure is injected at `UserPromptSubmit` and escalates. At the threshold the
injected text is a mandate, not a suggestion. `PreCompact` captures state
before the loss event.

The eight MCP tools survive as the read API and the escape hatch. They stop
being the write path.

## 4. Approach

Three approaches were considered.

**A. Observer-first (chosen).** Hooks measure and inject; the agent is a
subject, not a participant. Requires rewriting the hook layer.

**B. Prompt-contract.** Keep the current architecture, add strong instructions
telling the agent to call the tools and pass the percentage. Rejected: this is
the current design with more words, and it still depends on model compliance
for correctness.

**C. Transcript post-processor.** Read the transcript after the fact and
derive everything offline. Rejected as the whole answer: no live forcing, no
danger-zone blocking, and nothing survives a hard kill. Its central insight —
the transcript is the ground truth — is absorbed into A.

## 5. Components

Three units, one entrypoint, testable in isolation.

### 5.1 Sensor — `cogsession-hook <event>`

A single Python console script under `uv`, replacing all five bash hooks. It
dispatches on event name, reads the hook JSON from stdin, and is bound by one
contract: **it can never break the developer's session.** Every failure path
exits 0 silently. This is the inverse of the current `set -euo pipefail` plus
`jq` arrangement, which is a session-killing landmine on any machine without
`jq`.

Responsibilities:

- Compute live context load from `transcript_path` per §3.1.
- Resolve the context window size, in this precedence order:
  1. explicit `context_window` in `.cogsession.json`;
  2. observed high-water mark — a load above 200,000 tokens proves a larger
     window;
  3. a fresh value written by the statusline calibrator (below);
  4. default 200,000.
- Append events to `session_log.jsonl` with the true percentage.
- Maintain the tool-call counter and autosave cadence.

**Window resolution is load-bearing.** The transcript records
`"model": "claude-opus-5"` and does *not* disclose whether the session is the
1M-context variant. 65,025 tokens is 6.5% of 1M but 32.5% of 200k. Assuming
200k would over-report by roughly 5× on a large-window session and force
checkpoints absurdly early. The window must never be assumed from the model
id alone.

The statusline is retained in a reduced role: it *does* receive an
authoritative `context_window.used_percentage`, so it writes that value to a
small state file which the Sensor prefers when the file is fresh. It calibrates
and it displays. It never triggers.

### 5.2 Distiller — observation into memory

Two tiers, deliberately separated by cost and reliability.

**Deterministic tier** — runs on every tool call, no LLM. Files touched with
edit counts, commands run, test invocations and exit status, git operations,
error text from failed tools. Cheap, always correct, cannot hallucinate. This
tier alone eliminates the `context_pct: 0` class of defect.

**Inferential tier** — runs at checkpoint and pre-compact only, one LLM call.
Reads the transcript slice since the last checkpoint and produces the prose
artefacts the data model already defines: `handoff.md`, decisions with
reasoning, dead ends with why-they-failed, unverified assumptions. This is
where intelligence lives. Dead-end detection in particular is exactly what a
reader of the transcript sees immediately and what no deterministic rule
extracts reliably.

The Distiller is a pure function: transcript slice in, structured memory out,
no side effects. `session/models.py` is already the correct output type.

### 5.3 Injector — delivery without asking

| Hook | Injected |
|---|---|
| `SessionStart` | L0 manifest + L1 handoff, unprompted. The agent begins with memory. |
| `UserPromptSubmit` | Below 65%: nothing. 65–74%: a one-line nudge. 75–79%: a strong recommendation. 80% and above: a mandate naming the checkpoint tool. |
| `PreCompact` | Nothing injected; distil and write before the loss event. Non-negotiable — this is the moment the product exists for. |
| `PreToolUse` | On a write into a recorded danger zone: exit 2 with the remembered reason. The only blocking path in the system. |

Note that the current `claude_settings_template.json` registers no injecting
hook at all — no `SessionStart`, no `UserPromptSubmit`. This is why the system
cannot force anything, and fixing it is the core behavioural change.

### 5.4 Intelligent recall

Injection is relevance-filtered, not a dump. When the developer's prompt
concerns authentication, the Injector surfaces the dead ends and danger zones
matching authentication — not all forty memories. The retrieval primitive
already exists in `session_search`; the change is to aim it at the incoming
prompt instead of at a human typing a query.

A memory system that returns everything is merely a larger context problem.
Relevance filtering is the substantive difference between intelligent memory
and dead memory.

## 6. Data flow

```
SessionStart ──► Sensor reads index.json ──► Injector emits L0+L1 ──► agent has memory
                                                                          │
every tool ────► Sensor appends event to session_log.jsonl                │
                 (real context_pct, from transcript)                      ▼
                                                               developer works
UserPromptSubmit ─► Sensor computes pct ─► <65% silent | 65-79% nudge | >=80% mandate
                                        └─► relevance query vs the prompt ─► targeted recall

PreToolUse(Write) ─► danger-zone match? ─► exit 2 with the remembered reason
PreCompact ───────► Distiller over transcript slice ─► writer.write_all(real pct) ─► artefacts
SessionEnd ───────► same, plus close the session in index.json
```

`session_log.jsonl` ceases to be a parallel half-truth. It becomes a derived
index over the transcript: cheap to append, cheap to query, and consistent
because the same Sensor writes both the event and its true percentage.

## 7. Compatibility and migration

**The eight MCP tools keep their names and signatures.** Two changes:

- `context_pct` becomes optional everywhere. Omitted, the server measures it.
  Supplied, it is ignored — the agent's guess is strictly worse than the
  measurement. No caller breaks.
- `session_init` becomes idempotent and largely unnecessary, since
  `SessionStart` auto-initialises. It remains for explicit branching and for
  MCP clients without hooks (Codex, Windsurf, Cline), which must keep the
  manual path. That fallback is a feature: it keeps CogSession
  MCP-generic rather than Claude-Code-only.

**Existing `.cogsessions/` stores need no migration.** Same directory layout,
same schema, same `index.json`. Sessions recorded before this change carry
`token_pct_at_close: 0`; they must render as "unknown" rather than "0%". That
is a display fix in `loader.py`, not a data migration. Forward-only: no
version bump, no upgrade script.

## 8. Packaging and install

`uv` throughout, per project convention and the existing README.

- `pyproject.toml` gains a second console script beside `cogsession`: the hook
  entrypoint. Both ship in one package, so there is one thing to install and
  one version to keep in step. The hook cannot drift from the server.
- Hooks are registered as
  `uv --directory <repo> run cogsession-hook <event>`.
  **No files are copied to `~/.claude/cogsession/`.** That directory is
  retired, and with it every "installed hooks are stale relative to the repo"
  failure.
- `install.sh` must genuinely merge into an existing `~/.claude/settings.json`
  instead of printing "manually merge" — the first cause in §2, and the reason
  none of this was running. The merge is performed in Python, which is already
  a dependency; `jq` is not available and must not be assumed. It preserves
  unknown keys, appends to hook arrays rather than replacing them, writes a
  backup first, and is idempotent on re-run.
- Publishable so that adoption is one command with no clone:
  `uvx --from cogsession cogsession`.

## 9. Error handling

One rule, one exception, applied at the boundary rather than scattered through
the code.

**The rule.** The hook entrypoint wraps its whole dispatch in a catch-all that
writes the traceback to `.cogsessions/.debug/hook-errors.log` and exits 0 with
empty stdout. Nothing inside the Sensor, Distiller, or Injector carries
defensive `try/except`; they raise freely and stay testable as pure logic.

**The exception.** `PreToolUse` may exit 2, and only on a positive danger-zone
match. Any error while evaluating the match falls through to allow.

Two specific failure modes, named because they will occur:

- **Transcript unreadable, or no assistant message yet** (the first turn of a
  session). Context load is *unknown*, not zero. The Injector emits nothing.
  An unknown percentage must never round down into "you are fine."
- **Inferential distillation fails at `PreCompact`.** This is the worst
  possible moment to lose data, so the deterministic tier writes its artefacts
  first and independently; the inferential tier is a best-effort enrichment on
  top. A failed distillation degrades memory quality. It never loses the
  session.

A memory system that breaks sessions is uninstalled on the first day. The
silent-failure contract is a product requirement, not defensive habit.

## 10. Testing

The current code is untestable because its behaviour lives in bash reading
live stdin. That is why five broken hooks shipped undetected. The testing
strategy exists to make that specific outcome impossible.

- **Transcript fixtures.** A small set of real, scrubbed `.jsonl` transcripts:
  a fresh session, a mid-session one, one past 80%, one on a 1M window, one
  truncated mid-write. Context computation and window resolution become pure
  functions over these fixtures, table-tested. The 1M-versus-200k ambiguity
  gets a fixture per case, so the 5× over-reporting defect cannot reappear
  silently.
- **Hook contract tests.** Feed each event's real stdin JSON to the entrypoint
  as a subprocess; assert on exit code and stdout shape. This is the test that
  catches all four causes in §2: missing `jq`, dying under `set -euo
  pipefail`, writing a trigger file nobody reads, and injecting nothing at
  80%.
- **Distiller golden tests.** Transcript slice in, memory artefacts out, LLM
  tier stubbed for determinism, with one opt-in live test.
- **Install merge test.** Run the merger against a fixture `settings.json`
  holding pre-existing unrelated hooks; assert nothing was clobbered and that a
  second run changes nothing.

Deliberately not tested: statusline rendering, which is cosmetic; and
end-to-end "does the agent actually checkpoint", which needs a live session and
is verified manually once and documented as such rather than faked.

## 11. Documentation

The README currently over-promises. The token-aware warnings and
decision-quality flags in its comparison table describe behaviour that does
not exist. Once the redesign lands they become true, and the README must be
updated to describe automatic operation as the default path, with the manual
tool calls presented as the fallback for hook-less clients.

## 12. Out of scope

- File-content caching to deduplicate reads. The current hook logs a
  `cache_hit` and notes that real caching "needs SDK". That remains true and
  is not part of this work.
- Any change to the on-disk schema or the progressive-loading tiers.
- Cross-project global memory. The store stays project-local by default, as
  documented.
