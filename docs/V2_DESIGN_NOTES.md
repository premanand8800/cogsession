# CogSession v2 — design notes

Written 2026-08-27 after three weeks of daily use across three private repositories. Every item
below is either a bug read out of the v1 source or a failure observed in a real session, with
the file and line. Nothing here is speculative feature wishing.

v1 was ~2,560 lines across 13 modules, and the module split is good: `sensor` reads, `distiller`
condenses, `injector` decides what to say, `writer`/`loader` own the store, `hook_entry` and
`mcp/server` are the two entry points. **v2 is not a rewrite.** The architecture is sound. What
follows is seven bugs, all fixed, and two new capabilities.

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

## 5. Claims, not just decisions — built

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

No model judgement involved. It stores the command that proved something and re-runs it.

### Built as

`cogsession/claims.py`, plus two MCP tools (`claim_record`, `claim_check`) and a re-check on
`SessionStart`. `Claim` carries the statement, the command, the expected output, the paths it
watches, where it was asserted, and the commit it held at. `expect=None` means "must exit 0",
which suits `test -f` or a test invocation where the exit code is the whole signal.

Four decisions worth naming:

- **Silence when everything holds.** A report that appears every session becomes furniture, and
  furniture is not read. Only breakages are reported.
- **Verify on the way in.** A claim recorded already-broken is a typo in the command, and
  discovering that weeks later defeats the point.
- **A broken proof is not a false claim.** `error` and `broken` are distinct statuses;
  conflating them would cry wolf.
- **Fail towards checking.** No commit, no watch list, or git unable to answer all mean "check
  anyway". A missed re-check is a stale claim believed; a spurious one costs a `grep`.

On the trust boundary: verification runs shell commands from `claims.json`. That is the same
boundary as `.git/hooks` — local, developer-owned, not shared — and two guards make it explicit
rather than assumed. A claims file **tracked by git** is never executed, because a tracked file
can arrive from someone else. And every command runs under a timeout with its output compared,
never interpreted.

It fits what CogSession already is: the component that remembers. The gap was that remembering a
claim is not the same as still believing it.

---

## 6. Tracking that depended on someone remembering

The bug behind "it is not tracking anything", once §1 was fixed.

A session existed only if someone called `session_init` by hand. Nothing in the hook path
created one. So the ordinary case was: hooks firing on every event, `get_active_session_id()`
returning `None`, and every downstream handler returning early.

```
_handle_post_tool_use:  active_id = get_active_session_id(); if not active_id: return
```

Tool activity, compaction boundaries, session ends — none of it recorded, for anyone who had not
run an MCP call by hand. And §2's escalation firing into that vacuum.

### Fixed

`SessionStart` now opens a session when none is active, writing a manifest and an index entry
and nothing else. Deliberately cheap and quiet. The focus line stays empty until someone who
knows what the session is about fills it in, because that is the one thing a tool cannot infer.

It also binds `harness_session_id` to an already-open session the first time a hook sees it, so
later lookups resolve directly instead of falling back.

---

## 7. Two handlers that reported saves they never made

```python
# hook_entry.py (v1)
def _handle_pre_compact(...):
    print(f"[CogSession] ⚡ Pre-compact checkpoint saved at {context_pct:.1f}% context")

def _handle_session_end(...):
    print("[CogSession] Session ended — final snapshot saved")
```

Both printed a save and wrote nothing whatsoever. For a tool whose entire product is a claim to
remember accurately, reporting a save it never performed is the worst failure available: it is
not a missing feature, it is a false statement to the user at the exact moment they are relying
on it.

The `session_end` case had a second effect. A session left `active` forever is not inert —
`get_active_session_id()` returns the first active session it finds, so a stale one captures
every later lookup and new work is logged into last week's folder.

### Fixed

- `PreCompact` appends a real `compaction` entry to the append-only log, and says only what it
  did: a boundary recorded, not a checkpoint saved. A full checkpoint needs the in-memory
  session that lives in the MCP server, so a marker is the honest thing here.
- `SessionEnd` appends an entry and closes the session in the index with `closed_at`,
  `token_pct_at_close` and a `session_end` trigger, freeing the active slot.
- Both stay silent when there is no session. Nothing to report is not an error.

Also cleaned up on the way through: `datetime.utcnow()` (deprecated in 3.12) was emitting a
`DeprecationWarning` from inside a hook. Diagnostics from a hook are noise in the transcript,
which is what the earlier stdout fix was about, so all four call sites are now timezone-aware.

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

## 8. Two shapes of memory, and only one was built

The store was structured entirely for a program: `session_log.jsonl`,
`decisions.json`, `manifest.json`. Good for loading, useless for looking something up, because
finding one fact meant parsing a file and holding all of it. So the only way to answer "has this
already failed?" was to load the session and read it.

### Built as

`cogsession/journal.py` — `session.md`, append-only, one block per event. Three constraints, each
load-bearing:

- **Append-only.** Never rewritten, so a `grep -n` line number stays valid, and two processes
  appending cannot corrupt each other's entries. This is also why a late `focus` is appended as
  a timeline entry rather than edited into the header.
- **Every entry line self-describing.** Local timestamp, type, and repo state on the header
  line itself. That is precisely what makes a bare `grep` useful: a match is informative without
  `-A/-B`, so the reader does not have to scroll for context.
- **Repo state on every entry**, as `branch@commit+dirty`. A decision is not a free-floating
  fact; it was made against a specific state of the code, and "why did we think that?" is
  usually answered by what the tree looked like at the time. The commit id is also the join back
  to real `git log`.

Wired at one choke point: `Session.append_log` already carried every recording path in the
server, so the journal cannot develop holes. That mattered more than it looks — a journal
missing entries is worse than no journal, because a `grep` that finds nothing reads as proof
that nothing happened.

**One deliberate omission:** tool calls go to the JSONL and not the journal. Hundreds per
session would bury the decisions and dead ends someone is searching for, and the value here is
entirely in the signal-to-noise ratio.

`session_log` is the scan step — a `git log --oneline` across sessions, filterable by type, one
line each. Scan, then grep for the entry that matters.

One bug found while building it: the dirty count included `.cogsessions/`, so the tool reported
the developer's tree as dirty **because it had just written to its own store**, and the number
climbed with every entry. Excluded in code as well as by gitignore, so the figure means the same
thing in a project that has not got round to ignoring it.

## What is still open

Small, and none of it blocks daily use:

- **Escalate on what is at risk, not on a percentage.** A session with unsaved decisions at 70%
  deserves a nudge; one with nothing recorded at 99% deserves silence. Currently gated only on a
  session existing.
- **Read the host's settings.** Where the host compacts context automatically, "save state
  before truncation" is advice for a problem already solved elsewhere.
- **Danger zones: warn on first hit, block on the second**, and let a zone carry an `expires`
  date so one nobody renews stops blocking. A guard that can be wrong should escalate rather
  than shoot.
- **Rebuild a session from its log.** The append-only log holds everything that happened, so a
  crashed session could be reconstructed rather than lost. Today a checkpoint needs the
  in-memory session.

## Testing note

Four files, 83 tests, up from 13:

| File | Covers |
|---|---|
| `test_v2_foundations.py` | id resolution, the gated mandate, tracked-file refusal, danger zones |
| `test_session_lifecycle.py` | auto-init and the two handlers, driven through the real hook as a subprocess |
| `test_claims.py` | claims: breakage detection, silence when holding, the trust boundary |
| `test_journal.py` | `session.md`: grep behaviour, append-only, live git context, the log view |

The assertions care as much about what is **not** said as about what is, because every bug here
was silent. `test_session_lifecycle.py` goes through `hook_entry` as a subprocess rather than
calling functions, because these bugs lived in the wiring rather than in any one function — and
it asserts a clean exit everywhere, since the hook contract is that it must never break the
developer's session.

Two pre-existing tests had to change, and both were asserting the bug rather than the intent:

- `test_injector_pressure_messages` constructed an injector with **no sessions at all** and
  asserted the mandate fired. It passed precisely because the escalation was unconditional. It
  now seeds an active session, and a new test asserts silence when there is none.
- `test_pre_tool_use_danger_zone_block` relied on bare-name substring matching, which is why the
  bare-name path is retained rather than dropped.

A test that passes over a bug is worse than no test, because it certifies the bug. Worth
checking, when a change breaks a test, which of the two is wrong.
