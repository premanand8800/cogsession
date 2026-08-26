"""
cogsession/hook_entry.py

Single Python console script replacing bash hooks for Claude Code.
Dispatches on event name, reads hook JSON from stdin, and handles all hook logic.
Guaranteed contract: Never break developer's session — all exceptions caught and logged to .cogsessions/.debug/hook-errors.log, exiting 0.
Exception: PreToolUse exit 2 on positive danger-zone match.
"""

import sys
import json
import traceback
from pathlib import Path
from datetime import datetime, timezone

from cogsession.claims import ClaimStore, format_report
from cogsession.config import feature_enabled, is_enabled
from cogsession.sensor import calculate_context_pct
from cogsession.injector import MemoryInjector
from cogsession.session.loader import SessionLoader
from cogsession.session.models import Session, new_id
from cogsession.session.writer import SessionWriter


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    event = sys.argv[1]

    # Read stdin JSON
    raw_input = sys.stdin.read()
    payload = {}
    if raw_input.strip():
        try:
            payload = json.loads(raw_input)
        except Exception:
            payload = {}

    cwd_str = payload.get("cwd") or payload.get("workspace", {}).get("current_dir") or "."
    project_root = Path(cwd_str).resolve()
    session_id = payload.get("session_id", "")
    transcript_path_str = payload.get("transcript_path", "")
    transcript_path = Path(transcript_path_str) if transcript_path_str else None

    # Handle statusline calibration special file
    if event == "statusline":
        _handle_statusline(payload, project_root, session_id)
        sys.exit(0)

    try:
        # Calculate true context percentage
        context_pct, tokens, window = calculate_context_pct(
            project_root, session_id, transcript_path
        )

        injector = MemoryInjector(project_root)

        if event == "SessionStart":
            msg = injector.on_session_start(session_id)
            if msg:
                print(msg)
            # Before anything else about this session: is what the last one
            # wrote down still true? A stale claim believed is the failure this
            # exists to catch, and the only moment it is cheap to catch is
            # before work resumes on top of it.
            stale = _check_claims(project_root, session_id)
            if stale:
                print(stale)
            note = _ensure_active_session(project_root, session_id)
            if note:
                print(note)

        elif event == "UserPromptSubmit":
            prompt = payload.get("user_prompt") or payload.get("prompt") or ""
            msg = injector.on_user_prompt_submit(prompt, context_pct)
            if msg:
                print(msg)

        elif event == "PreToolUse":
            tool_name = payload.get("tool_name") or payload.get("tool") or ""
            tool_input = payload.get("tool_input") or payload.get("input") or {}
            should_block, reason = injector.on_pre_tool_use(tool_name, tool_input)
            if should_block:
                print(f"[CogSession DANGER ZONE BLOCK] {reason}")
                sys.exit(2)

        elif event == "PostToolUse":
            _handle_post_tool_use(payload, project_root, session_id, context_pct)

        elif event == "PreCompact":
            _handle_pre_compact(payload, project_root, session_id, context_pct)

        elif event == "SessionEnd":
            _handle_session_end(payload, project_root, session_id, context_pct)

    except SystemExit as se:
        if se.code == 2:
            sys.exit(2)
        sys.exit(0)
    except Exception as e:
        _log_error(project_root, event, e)
        sys.exit(0)

    sys.exit(0)


# ── Helpers ────────────────────────────────────────────────────────────


def _now() -> str:
    """UTC timestamp. `datetime.utcnow()` is deprecated from 3.12."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_log(project_root: Path, session_id: str, entry: dict) -> bool:
    """Append one entry to a session's append-only log. True if written."""
    session_dir = project_root / ".cogsessions" / session_id
    if not session_dir.is_dir():
        return False
    try:
        with open(session_dir / "session_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **entry}) + "\n")
        return True
    except OSError:
        return False


def _ensure_active_session(project_root: Path, harness_session_id: str) -> str:
    """Open a session if none is active, so tracking never depends on memory.

    This is the difference between a tool that works and one that has to be
    remembered. Previously a session existed only if someone called
    `session_init` by hand, so the common case was: hooks firing, nothing
    recorded, and a context mandate demanding a checkpoint with nowhere to put
    it. Nothing downstream logs anything without an active session.

    Deliberately quiet and cheap: it writes a manifest and an index entry, and
    nothing else. The focus line is filled in later by whoever knows what the
    session is about.
    """
    if not is_enabled(project_root):
        return ""

    loader = SessionLoader(project_root)
    active = loader.get_active_session_id()
    if active:
        # Already open. Bind the host id if this is the first hook to see it,
        # so later lookups from this session resolve without a fallback.
        _bind_harness_id(project_root, active, harness_session_id)
        return ""

    session = Session(
        id=new_id(),
        project_root=str(project_root),
        harness_session_id=harness_session_id or None,
    )
    try:
        session.ensure_dir()
        writer = SessionWriter(session)
        writer.write_manifest()
        writer.update_index()
    except OSError:
        return ""

    return f"[CogSession] Tracking this session as {session.id}"



def _check_claims(project_root: Path, harness_session_id: str) -> str:
    """Re-verify the previous session's claims, reporting only what broke.

    Quiet by default. A report that shows up every session becomes furniture,
    and furniture is not read — so silence means everything still holds.
    """
    if not feature_enabled(project_root, "claim_checks"):
        return ""
    loader = SessionLoader(project_root)
    sid = loader.resolve(harness_session_id)
    if not sid:
        return ""
    try:
        results = ClaimStore(project_root, sid).verify(only_if_changed=True)
        return format_report(results)
    except Exception:
        # A tripwire must never be the thing that breaks the session.
        return ""


def _bind_harness_id(project_root: Path, session_id: str, harness_session_id: str) -> None:
    """Attach the host session id to an already-open session, once."""
    if not harness_session_id:
        return
    index_path = project_root / ".cogsessions" / "index.json"
    try:
        index = json.loads(index_path.read_text())
        entry = index.get("sessions", {}).get(session_id)
        if entry is None or entry.get("harness_session_id"):
            return
        entry["harness_session_id"] = harness_session_id
        index_path.write_text(json.dumps(index, indent=2))
    except (OSError, ValueError):
        return


def _handle_statusline(payload: dict, project_root: Path, session_id: str):
    """Calibrates context window and displays status line."""
    try:
        ctx_used = payload.get("context_window", {}).get("used_percentage")
        ctx_win = payload.get("context_window", {}).get("context_window_size")
        if session_id and ctx_win:
            status_file = Path(f"/tmp/cogsession_statusline_{session_id}.json")
            status_file.write_text(
                json.dumps(
                    {
                        "context_window": ctx_win,
                        "used_percentage": ctx_used,
                        "updated_at": _now(),
                    }
                )
            )
    except Exception:
        pass


def _handle_post_tool_use(payload: dict, project_root: Path, session_id: str, context_pct: float):
    """Logs tool usage and test results to active session log."""
    loader = SessionLoader(project_root)
    active_id = loader.get_active_session_id()
    if not active_id:
        return

    active_dir = project_root / ".cogsessions" / active_id
    if not active_dir.exists():
        return

    log_file = active_dir / "session_log.jsonl"
    ts = _now()
    tool_name = payload.get("tool_name", "")
    entry = {
        "ts": ts,
        "type": "tool_use",
        "tool": tool_name,
        "context_pct": context_pct if context_pct is not None else 0,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _handle_pre_compact(payload: dict, project_root: Path, session_id: str, context_pct: float):
    """Record the compaction boundary in the session log.

    This used to print "checkpoint saved" and write nothing at all, which is
    the worst thing a memory tool can do: claim to have saved state it never
    saved. It now appends a real entry, and says only what it actually did.
    A full checkpoint needs the in-memory session that lives in the MCP
    server, so the honest thing here is a marker, not a promise.
    """
    active_id = SessionLoader(project_root).get_active_session_id()
    if not active_id:
        return

    if _append_log(project_root, active_id, {
        "type": "compaction",
        "content": f"Context compacted at {context_pct or 0:.1f}%",
        "context_pct": context_pct or 0,
    }):
        print(
            f"[CogSession] Compaction boundary recorded at {context_pct or 0:.1f}% "
            f"in {active_id}"
        )


def _handle_session_end(payload: dict, project_root: Path, session_id: str, context_pct: float):
    """Close the active session in the index.

    Also previously printed "final snapshot saved" while saving nothing. A
    session left `active` forever is not harmless: `get_active_session_id`
    returns the first active one it finds, so a stale session captures every
    later lookup and new work is logged against last week's folder.
    """
    loader = SessionLoader(project_root)
    active_id = loader.get_active_session_id()
    if not active_id:
        return

    _append_log(project_root, active_id, {
        "type": "session_end",
        "content": f"Session ended at {context_pct or 0:.1f}% context",
        "context_pct": context_pct or 0,
    })

    index_path = project_root / ".cogsessions" / "index.json"
    try:
        index = json.loads(index_path.read_text())
        entry = index.get("sessions", {}).get(active_id)
        if entry is None:
            return
        entry["status"] = "completed"
        entry["closed_at"] = _now()
        entry["token_pct_at_close"] = context_pct or 0
        if not entry.get("checkpoint_trigger"):
            entry["checkpoint_trigger"] = "session_end"
        index_path.write_text(json.dumps(index, indent=2))
        print(f"[CogSession] {active_id} closed at {context_pct or 0:.1f}% context")
    except (OSError, ValueError):
        return


def _log_error(project_root: Path, event: str, exc: Exception):
    """Writes traceback silently to .cogsessions/.debug/hook-errors.log."""
    try:
        debug_dir = project_root / ".cogsessions" / ".debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        log_file = debug_dir / "hook-errors.log"
        ts = _now()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"=== {ts} Event: {event} ===\n")
            f.write(traceback.format_exc() + "\n\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
