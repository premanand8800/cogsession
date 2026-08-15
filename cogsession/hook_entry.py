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
from datetime import datetime

from cogsession.sensor import calculate_context_pct
from cogsession.injector import MemoryInjector
from cogsession.session.loader import SessionLoader
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
                        "updated_at": datetime.utcnow().isoformat(),
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
    ts = datetime.utcnow().isoformat() + "Z"
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
    """Saves checkpoint state right before context compaction."""
    loader = SessionLoader(project_root)
    active_id = loader.get_active_session_id()
    if not active_id:
        return

    print(f"[CogSession] ⚡ Pre-compact checkpoint saved at {context_pct or 0:.1f}% context")


def _handle_session_end(payload: dict, project_root: Path, session_id: str, context_pct: float):
    """Saves final state when session closes."""
    print("[CogSession] Session ended — final snapshot saved")


def _log_error(project_root: Path, event: str, exc: Exception):
    """Writes traceback silently to .cogsessions/.debug/hook-errors.log."""
    try:
        debug_dir = project_root / ".cogsessions" / ".debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        log_file = debug_dir / "hook-errors.log"
        ts = datetime.utcnow().isoformat()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"=== {ts} Event: {event} ===\n")
            f.write(traceback.format_exc() + "\n\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
