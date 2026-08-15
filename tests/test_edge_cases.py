import json
import pytest
import subprocess
import sys
from pathlib import Path

from cogsession.sensor import (
    compute_transcript_tokens,
    resolve_context_window,
    calculate_context_pct,
)
from cogsession.injector import MemoryInjector
from cogsession.distiller import Distiller
from cogsession.installer import merge_settings
from cogsession.session.models import Session


# ── Edge Case 1: Corrupted / Non-existent / Truncated Transcript ──────


def test_transcript_corrupted_json(tmp_path: Path):
    transcript = tmp_path / "corrupted.jsonl"
    transcript.write_text("invalid json line\n{truncated json...\n")

    # Must return None safely instead of crashing
    tokens = compute_transcript_tokens(transcript)
    assert tokens is None

    pct, tok, win = calculate_context_pct(tmp_path, "sess_1", transcript)
    assert pct is None
    assert tok is None
    assert win == 200000


def test_transcript_missing(tmp_path: Path):
    missing_path = tmp_path / "does_not_exist.jsonl"
    tokens = compute_transcript_tokens(missing_path)
    assert tokens is None


def test_transcript_without_assistant_usage(tmp_path: Path):
    transcript = tmp_path / "no_usage.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "hello"}) + "\n"
    )
    tokens = compute_transcript_tokens(transcript)
    assert tokens is None


# ── Edge Case 2: Window Resolution (1M vs 200k High Watermark) ────────


def test_window_resolution_high_watermark(tmp_path: Path):
    # Load > 200k proves larger window (1M)
    assert resolve_context_window(tmp_path, "sess_1", 200001) == 1000000
    # Load > 1M proves 2M window
    assert resolve_context_window(tmp_path, "sess_1", 1050000) == 2000000


def test_window_resolution_statusline_fresh_vs_stale(tmp_path: Path):
    status_file = Path("/tmp/cogsession_statusline_test_stale.json")
    status_file.write_text(json.dumps({"context_window": 1000000}))

    # Fresh statusline read returns 1M
    assert resolve_context_window(tmp_path, "test_stale", 50000) == 1000000

    if status_file.exists():
        status_file.unlink()


# ── Edge Case 3: Danger Zone Blocking (PreToolUse) ────────────────────


def test_pre_tool_use_danger_zone_block(tmp_path: Path):
    sess_dir = tmp_path / ".cogsessions" / "sess_active"
    sess_dir.mkdir(parents=True)
    (tmp_path / ".cogsessions" / "index.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "sess_active": {"id": "sess_active", "status": "active"}
                }
            }
        )
    )
    (sess_dir / "danger_zones.md").write_text(
        "# Danger Zones\n- critical_config.py (Custom CORS setup)\n"
    )

    injector = MemoryInjector(tmp_path)

    # Editing non-danger file -> allow
    should_block, reason = injector.on_pre_tool_use(
        "Edit", {"file_path": "/path/to/normal.py"}
    )
    assert should_block is False

    # Editing danger zone file -> block with exit 2 reason
    should_block, reason = injector.on_pre_tool_use(
        "Edit", {"file_path": "/path/to/critical_config.py"}
    )
    assert should_block is True
    assert "critical_config.py" in reason


# ── Edge Case 4: Hook Entry Subprocess Exit Codes & Error Handling ───


def test_hook_entry_silent_fail_on_crash(tmp_path: Path):
    # Run hook_entry with invalid payload / empty stdin -> must exit 0 cleanly
    proc = subprocess.run(
        [sys.executable, "-m", "cogsession.hook_entry", "UserPromptSubmit"],
        cwd=tmp_path,
        input="",
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_hook_entry_exit_2_on_danger_zone(tmp_path: Path):
    sess_dir = tmp_path / ".cogsessions" / "sess_active"
    sess_dir.mkdir(parents=True)
    (tmp_path / ".cogsessions" / "index.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "sess_active": {"id": "sess_active", "status": "active"}
                }
            }
        )
    )
    (sess_dir / "danger_zones.md").write_text("- protected.py\n")

    payload = json.dumps(
        {
            "cwd": str(tmp_path),
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "protected.py")},
        }
    )

    proc = subprocess.run(
        [sys.executable, "-m", "cogsession.hook_entry", "PreToolUse"],
        cwd=tmp_path,
        input=payload,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "protected.py" in proc.stdout


# ── Edge Case 5: Settings Merger Idempotency & Backup ──────────────────


def test_settings_merger_idempotent(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    repo_dir = tmp_path / "repo"

    # First merge
    path1 = merge_settings(claude_dir, repo_dir)
    content1 = path1.read_text()

    # Second merge (must change nothing)
    path2 = merge_settings(claude_dir, repo_dir)
    content2 = path2.read_text()

    assert content1 == content2
    # Verify backup file was created
    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups) >= 1
