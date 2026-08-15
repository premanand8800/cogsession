"""
cogsession/sensor.py

Calculates live context load and window size from transcript JSONL.
"""

import json
from pathlib import Path
from typing import Optional, Tuple


def compute_transcript_tokens(transcript_path: Path) -> Optional[int]:
    """
    Computes context token load from transcript JSONL.
    Finds the LAST assistant entry carrying message.usage.
    Tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens.
    Returns None if unreadable or no assistant message usage is found yet.
    """
    if not transcript_path or not transcript_path.exists():
        return None

    last_tokens: Optional[int] = None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue

                # Check assistant messages with usage block
                # Format can be entry["message"]["usage"] or entry["usage"]
                usage = None
                if isinstance(entry, dict):
                    if entry.get("type") == "assistant" or entry.get("role") == "assistant":
                        msg = entry.get("message")
                        if isinstance(msg, dict) and "usage" in msg:
                            usage = msg["usage"]
                        elif "usage" in entry:
                            usage = entry["usage"]
                    elif "message" in entry and isinstance(entry["message"], dict):
                        msg = entry["message"]
                        if msg.get("role") == "assistant" and "usage" in msg:
                            usage = msg["usage"]

                if usage and isinstance(usage, dict):
                    input_tokens = usage.get("input_tokens", 0) or 0
                    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
                    cache_read = usage.get("cache_read_input_tokens", 0) or 0
                    output_tokens = usage.get("output_tokens", 0) or 0

                    total = input_tokens + cache_creation + cache_read + output_tokens
                    if total > 0:
                        last_tokens = total
    except Exception:
        return None

    return last_tokens


def resolve_context_window(
    project_root: Path,
    session_id: str,
    observed_tokens: Optional[int] = None,
) -> int:
    """
    Resolves the context window size, in precedence order:
      1. explicit context_window in .cogsession.json
      2. observed high-water mark — load above 200,000 proves a larger window
      3. a fresh value written by statusline state file (/tmp/cogsession_statusline_<session_id>.json)
      4. default 200,000
    """
    # 1. Explicit in .cogsession.json
    config_file = project_root / ".cogsession.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            if "context_window" in cfg and isinstance(cfg["context_window"], int):
                return cfg["context_window"]
        except Exception:
            pass

    # 2. Observed high-water mark
    if observed_tokens and observed_tokens > 200000:
        # Round up to 1M or 2M window
        if observed_tokens > 1000000:
            return 2000000
        return 1000000

    # 3. Fresh value written by statusline calibrator
    if session_id:
        status_file = Path(f"/tmp/cogsession_statusline_{session_id}.json")
        if status_file.exists():
            try:
                # Check if fresh (written within last 5 minutes)
                import time

                mtime = status_file.stat().st_mtime
                if time.time() - mtime < 300:
                    data = json.loads(status_file.read_text())
                    ctx_win = data.get("context_window")
                    if ctx_win and isinstance(ctx_win, int):
                        return ctx_win
            except Exception:
                pass

    # 4. Default
    return 200000


def calculate_context_pct(
    project_root: Path,
    session_id: str,
    transcript_path: Optional[Path],
) -> Tuple[Optional[float], Optional[int], int]:
    """
    Calculates (context_pct, tokens, context_window).
    Returns (None, None, window) if transcript is unreadable or token count unknown.
    """
    if not transcript_path:
        return None, None, resolve_context_window(project_root, session_id, None)

    tokens = compute_transcript_tokens(transcript_path)
    window = resolve_context_window(project_root, session_id, tokens)

    if tokens is None:
        return None, None, window

    pct = (tokens / window) * 100.0
    return pct, tokens, window
