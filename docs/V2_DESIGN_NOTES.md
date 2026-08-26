# CogSession v2 — design notes

Written 2026-08-27 after three weeks of daily use across three private repositories. Every item
below is either a bug read out of the v1 source or a failure observed in a real session, with
the file and line. Nothing here is speculative feature wishing.

v1 is ~2,560 lines across 13 modules, and the module split is good: `sensor` reads, `distiller`
condenses, `injector` decides what to say, `writer`/`loader` own the store, `hook_entry` and
`mcp/server` are the two entry points. **v2 is not a rewrite.** The architecture is sound. What
follows is four bugs, all now fixed, and one new capability that is not built yet.

The theme across all four: **every bug was silent.** The tool kept running, printed something
plausible, and did the wrong thing. That is the failure mode a session tool can least afford,
because the whole product is a claim to remember things accurately.

---

## 1. The bug that makes v1 look broken: two id namespaces

**This is the whole reason a healthy install reports that it is tracking nothing.**

Sessions on disk are keyed by CogSession slugs:

```
.cogsessions/sess_20260101_topic_ab12/handoff.md
```

But the `SessionStart` hook is handed the *host tool's* session id and passed it straight
through as a directory name:

```python
# session/loader.py:68 (v1)
def load_handoff(self, session_id: str) -> str:
    path = self.sessions_dir / session_id / "handoff.md"
    if not path.exists():
        return f"[CogSession] No handoff found for {session_id}"
```

So it looked for `.cogsessions/<uuid>/handoff.md`, which cannot exist. Observed output, twice in
one session:

```
[CogSession] Previous Memory Loaded (a6eaf734-...):
Summary: No summary
--- Handoff ---
[CogSession] No handoff found for a6eaf734-...
```

The store was healthy throughout: twelve sessions, `index.json` written that same evening, the
previous three all `completed` with proper close times. **v1 had the memory and could not find
it.**

The fallback that would have rescued it was already written, and was unreachable:

```python
# injector.py:23 (v1)
sid = session_id or self.loader.get_latest_session_id()
```

`session_id` is always truthy in the real hook path, so `get_latest_session_id()` never ran. A
dead branch that looked like a safety net.

### Fixed

`SessionLoader.resolve()` accepts any of the three things a caller might have — one of our
slugs, a host session id, or nothing — and returns a session that exists on disk or `None`.
Sessions now record `harness_session_id` so the mapping is data rather than a guess.

`load_handoff` returns `Optional[str]`. It used to return the not-found text *as* the handoff,
so a caller could not distinguish absence from content, and printed the message to the user as
if it were the brief. Related: `on_session_start` now returns `""` when there is nothing to
report. Saying nothing beats announcing that you found nothing.

---

## 2. Mandating an action the tool cannot perform

`injector.on_user_prompt_submit` escalated purely on context percentage:

```python
# injector.py:52 (v1)
if context_pct >= 80:
    "You MUST call `session_checkpoint` now to save session state before context truncation."
```

No check that a session existed. Observed: this fired at 95.6% and again at 99.9% in a session
where `session_status` returned **"No active session."** The tool told the model it MUST
checkpoint into something that did not exist, having itself just announced that it did not
exist.

### Fixed

The escalation ladder is gated on an open session. No target, no mandate.

The general rule, and the one worth keeping: **a hook that cannot act should not shout.** v1's
loudest message was its least actionable one.

Two refinements not yet built:

- **Escalate on what is at risk, not on a percentage.** A session with unsaved decisions at 70%
  deserves a nudge; one with nothing recorded at 99% deserves silence.
- **Read the host's settings.** Where the host compacts context automatically, "save state
  before truncation" is confident advice for a problem already solved elsewhere.

---

## 3. Writing into a git-tracked file, by default

```python
# mcp/server.py:417 (v1)
claude_md = _project_root / "CLAUDE.md"
```

with the default:

```python
# config.py:34 (v1)
"auto_handoff": True,   # Auto-write handoff to CLAUDE.md
```

`CLAUDE.md` is tracked, committed, shared with a team, and auto-loaded every session. So the
default behaviour was for a session tool to write session-local scratch state into a shared
source file, dirtying the working tree of the repository it is only supposed to be observing.

**And the flag governing it was never read.** `feature_enabled` was not imported in
`mcp/server.py` at all, so `auto_handoff: false` in a project config had no effect whatsoever.
Declared, documented, dead. This is why the behaviour survived being switched off, and it cost
three separate repositories a hand-written pre-commit guard to defend against.

The intent was right: auto-loading *is* the correct delivery mechanism. The mistake was choosing
a **tracked** file for it.

### Fixed

- Target `CLAUDE.local.md` (`config.HANDOFF_TARGET_NAME`). Auto-loaded the same way, not
  committed.
- `_is_git_tracked()` runs `git ls-files --error-unmatch` before writing, and **refuses** if the
  target is tracked, reporting where the handoff is on disk instead. There is no flag to
  override it. A tool that observes a repository must not modify it.
- `auto_handoff` is actually consulted now.

This is the fix I would ship first in any ordering. It is the only one that caused work in other
repositories.

---

## 4. Danger zones matched a bare filename against prose

```python
# injector.py:120 (v1)
target_name = file_path.name
if target_name in dz_path.read_text():
    return True, ...
```

Two problems, and this hook can **block a write** (exit 2), so a false positive stops real work:

- **Basename, not path.** Marking `src/api/config.py` dangerous also blocked
  `tests/fixtures/config.py` and every other `config.py` in the tree.
- **Substring against the whole file.** `danger_zones.md` is prose. A line like "we fixed the
  bug in `config.py` last week" armed a block. The note explaining why something *had been*
  dangerous became the thing that blocked it.

### Fixed

A zone must now be **declared**, not **mentioned**. `_zone_pattern` reads one declaration per
line, pattern first, and returns nothing for prose or comments:

```
- src/core/ports.py            exact, repo-relative
- src/core/*.py                glob
- path: src/core/ports.py      explicit key
- `src/core/ports.py` - why    backticked, prose after it
- ports.py (why)               bare name: matches any directory
```

Matching is on the normalised repo-relative path via `fnmatch`. A **bare name keeps its old
permissive reach**, because that is how existing files are written and breaking them would be a
pointless cost — but only when it is the first token on the line. A line whose first token is a
word rather than a filename declares nothing.

A write outside the project has no repo-relative path, so it falls back to its bare name: a
declared bare name still protects it, and a declared path cannot match it by accident.

Not yet built, and worth doing since this one blocks: **warn on first hit, block on the second
within a session**, and let zones carry an `expires` date so one nobody renews stops blocking. A
guard that can be wrong should escalate rather than shoot.

---

## 5. The thing to build next: claims, not just decisions

v1 records decisions, dead ends and assumptions. All of it is **what we believed at a moment**,
and nothing ever re-checks whether it is still true.

Three weeks of real failures, and every one reduces to the same sentence: *something was true
when it was written and stopped being true.*

| What went stale | Cost |
|---|---|
| A pull request description describing a schema — four separate times on one PR | Four review rounds. One version described a security hole as though it were the fix |
| A migration header comment naming which numbers were taken | Caught by review automation, twice |
| A planning document, one day old | Reported stale state to the user as current |
| A test asserting the wrong shape | Passed green over an open isolation hole for a day |
| A docstring contradicting its own function | Shipped; caught by someone reading the library source |

A session tool already knows what was asserted and when. So let assertions be **checkable**:

```json
{
  "claim": "the composite key includes the tenant column",
  "verified_by": "grep -c 'UNIQUE (a, b, c)' migrations/007_schema.sql",
  "expect": "1",
  "at_commit": "c632681",
  "asserted_in": "PR description, line 26"
}
```

Then:

- On `SessionStart`, re-run the checks whose files changed since `at_commit`.
- Report only what moved: **"3 claims still hold, 1 no longer does."**
- The message a model needs is not "you decided X". It is **"what you wrote about X is now
  false."**

No model judgement involved. It stores the command that proved something and re-runs it. Roughly
one module beside `distiller`, and it targets the single most expensive recurring failure across
three weeks of work.

It also fits what CogSession already is: the component that remembers. The gap is that
remembering a claim is not the same as still believing it.

---

## What not to change

- **The module split.** `sensor` / `distiller` / `injector` / `writer` / `loader` is a clean
  separation, and all four fixes above landed inside one existing module each.
- **Progressive loading.** L0 manifest, L1 handoff, L2 tasks is the right shape. The bug was
  resolution, not the tiering.
- **The context-quality warning on handoffs.** *"This handoff was written at 96% context;
  decisions in the last 20% may be lower quality"* is the best idea in v1. It is honest about
  its own reliability, which almost no tool is. Keep it and extend it — that instinct is what
  §5 generalises.

## Testing note

`tests/test_v2_foundations.py` covers all four fixes, and the assertions care as much about what
is **not** said as about what is, because every one of these bugs was silent.

Two pre-existing tests had to change, and both were asserting the bug rather than the intent:

- `test_injector_pressure_messages` constructed an injector with **no sessions at all** and
  asserted the mandate fired. It passed precisely because the escalation was unconditional. It
  now seeds an active session, and a new test asserts silence when there is none.
- `test_pre_tool_use_danger_zone_block` relied on bare-name substring matching, which is why the
  bare-name path is retained rather than dropped.

A test that passes over a bug is worse than no test, because it certifies the bug. Worth
checking, when a change breaks a test, which of the two is wrong.
