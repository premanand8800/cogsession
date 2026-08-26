"""
cogsession/journal.py

`session.md` — one flat, greppable, timestamped record of a session.

Everything else in the store is structured for a program: `session_log.jsonl`,
`decisions.json`, `manifest.json`. Good for loading, useless for looking
something up, because finding one fact means parsing a file and holding it all.

This is the other half. One markdown file, appended as work happens, designed so
a single `grep` answers a question without loading anything:

    grep -n "dead_end" session.md              what has already failed
    grep -n "2026-08-27 01:" session.md        what happened in that hour
    grep -n "main@a1b2c3d" session.md          what happened at that commit
    grep -B2 -A6 "danger" session.md           a zone with its surroundings

Three properties make that work, and each is a deliberate constraint:

**Append-only, one entry per block.** Never rewritten, so a `grep -n` line
number stays valid and two processes appending cannot corrupt each other's
entries.

**Every line self-describing.** Each entry header carries the local time, the
type, and the repo state, so a matched line is useful on its own — no need to
scroll up for context. That is what makes `grep` without `-A/-B` worth
anything.

**Repo state on every entry.** Branch, short commit, and whether the tree was
dirty. A decision is not a free-floating fact; it was made against a specific
state of the code, and "why did we think that?" is usually answered by what the
tree looked like at the time. This is also the join back to `git log`: the same
commit id appears in both.

Timestamps are local time with an offset, because the reader is a human in a
timezone, and UTC is carried alongside for machines.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

JOURNAL_FILE = "session.md"
_GIT_TIMEOUT = 5

# Types that deserve a marker in the left margin, so scanning the file by eye
# finds the load-bearing entries without reading every line.
_MARKERS = {
    "decision": "*",
    "dead_end": "!",
    "assumption": "?",
    "danger_zone": "!!",
    "error": "x",
    "checkpoint": "#",
    "compaction": "#",
    "session_end": "#",
    "claim": "=",
}


def local_stamp() -> str:
    """`2026-08-27 01:41:09 +0545` — local time, because a human reads this."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GitContext:
    """Repo state at the moment something was recorded."""

    branch: str = ""
    commit: str = ""
    dirty: int = 0
    is_repo: bool = False

    def label(self) -> str:
        """One compact token, greppable: `main@a1b2c3d+3` (+3 = dirty files)."""
        if not self.is_repo:
            return "no-git"
        base = f"{self.branch or 'detached'}@{self.commit or 'unknown'}"
        return f"{base}+{self.dirty}" if self.dirty else base


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _count_dirty(status: Optional[str]) -> int:
    """Count changed paths, excluding our own store.

    `.cogsessions/` is where this tool writes, so counting it would report the
    developer's tree as dirty because the journal recorded an entry — and the
    count would climb every time we wrote to it. It is gitignored in a normal
    project, but the exclusion is done here too so the number means the same
    thing in a project that has not got round to that yet.
    """
    if not status:
        return 0
    changed = 0
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().split(" -> ")[-1].strip('"')
        if path.startswith(".cogsessions/") or path == ".cogsessions":
            continue
        changed += 1
    return changed


def git_context(project_root: Path) -> GitContext:
    """Capture branch, short commit and dirty count. Never raises.

    A project without git is a normal case, not an error — the journal simply
    records `no-git` and stays useful.
    """
    commit = _git(project_root, "rev-parse", "--short", "HEAD")
    if commit is None:
        return GitContext(is_repo=False)
    branch = _git(project_root, "rev-parse", "--abbrev-ref", "HEAD") or ""
    status = _git(project_root, "status", "--porcelain")
    dirty = _count_dirty(status)
    return GitContext(branch=branch, commit=commit, dirty=dirty, is_repo=True)


class Journal:
    """Appends to one session's `session.md`."""

    def __init__(self, project_root: Path, session_id: str):
        self.root = Path(project_root)
        self.session_id = session_id
        self.path = self.root / ".cogsessions" / session_id / JOURNAL_FILE

    # ── Header ─────────────────────────────────────────────────────────

    def ensure_header(self, focus: str = "", harness_session_id: str = "") -> bool:
        """Write the header once. True if this call created the file.

        Idempotent, because `SessionStart` can fire more than once for one
        session and a second header would split the timeline in two.
        """
        if self.path.exists():
            return False
        ctx = git_context(self.root)
        lines = [
            f"# Session {self.session_id}",
            "",
            f"- **Started:** {local_stamp()}  (`{utc_stamp()}`)",
            f"- **Focus:** {focus or '_not set yet_'}",
            f"- **Repo:** `{ctx.label()}`",
        ]
        if harness_session_id:
            lines.append(f"- **Host session:** `{harness_session_id}`")
        lines += [
            "",
            "> Append-only. One block per event, every header carrying its own time,",
            "> type and repo state, so a single `grep` answers a question without",
            "> loading the file. Markers: `*` decision `!` dead end `?` assumption",
            "> `!!` danger `x` error `#` lifecycle `=` claim",
            "",
            "## Timeline",
            "",
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(lines))
            return True
        except OSError:
            return False

    def set_focus(self, focus: str) -> None:
        """Record the focus as a timeline entry rather than editing the header.

        The header is history once written. A focus that arrives later is a fact
        about the session with a time of its own, and rewriting the file would
        invalidate every line number already handed out.
        """
        self.append("focus", focus)

    # ── Entries ────────────────────────────────────────────────────────

    def append(
        self,
        entry_type: str,
        content: str,
        context_pct: float = 0.0,
        detail: Optional[dict] = None,
    ) -> bool:
        """Append one entry. Never raises; returns whether it was written."""
        if not content and not detail:
            return False

        ctx = git_context(self.root)
        marker = _MARKERS.get(entry_type, "-")
        pct = f" · ctx {context_pct:.0f}%" if context_pct else ""

        block = [
            "",
            f"### `{local_stamp()}` {marker} **{entry_type}**{pct} · `{ctx.label()}`",
            "",
            content.strip() if content else "",
        ]
        for key, value in (detail or {}).items():
            if value in (None, "", [], {}):
                continue
            block.append(f"- _{key}_: {value}")
        block.append("")

        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(block))
            return True
        except OSError:
            return False

    # ── Reading ────────────────────────────────────────────────────────

    def entries(self) -> list[dict]:
        """Parse the timeline back out. For the log view, not for loading."""
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text()
        except OSError:
            return []

        out: list[dict] = []
        for line in text.splitlines():
            if not line.startswith("### `"):
                continue
            try:
                stamp = line.split("`")[1]
                rest = line.split("`")[2]
                etype = rest.split("**")[1] if "**" in rest else ""
                repo = line.rsplit("`", 2)[-2] if line.count("`") >= 4 else ""
            except (IndexError, ValueError):
                continue
            out.append({"stamp": stamp, "type": etype, "repo": repo})
        return out


def log_view(project_root: Path, session_ids: list[str], limit: int = 40,
             type_filter: str = "") -> str:
    """A `git log --oneline` for sessions: newest first, one line per entry.

    Deliberately one line each. The point is to scan a lot of history quickly
    and then `grep` the journal for the one that matters, not to read it all.
    """
    rows: list[str] = []
    for sid in session_ids:
        journal = Journal(project_root, sid)
        for entry in reversed(journal.entries()):
            if type_filter and entry["type"] != type_filter:
                continue
            rows.append(
                f"{entry['stamp']}  {entry['type']:<12} {entry['repo']:<22} {sid}"
            )
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    if not rows:
        return "[CogSession] No journal entries yet."
    header = f"{'when':<26} {'what':<12} {'repo':<22} session"
    return "\n".join([header, "-" * len(header), *rows])
