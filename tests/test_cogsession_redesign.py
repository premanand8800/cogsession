import json
import pytest
from pathlib import Path
from cogsession.sensor import compute_transcript_tokens, resolve_context_window, calculate_context_pct
from cogsession.injector import MemoryInjector
from cogsession.installer import merge_settings


def test_transcript_token_parsing(tmp_path: Path):
    transcript_file = tmp_path / "transcript.jsonl"
    entries = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 250,
                    "output_tokens": 20,
                },
            },
        },
    ]
    with open(transcript_file, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    tokens = compute_transcript_tokens(transcript_file)
    assert tokens == 420


def test_context_window_resolution(tmp_path: Path):
    # Default 200k
    assert resolve_context_window(tmp_path, "sess_1", 50000) == 200000

    # High water mark > 200k
    assert resolve_context_window(tmp_path, "sess_1", 250000) == 1000000

    # Explicit config
    cfg = tmp_path / ".cogsession.json"
    cfg.write_text(json.dumps({"context_window": 1000000}))
    assert resolve_context_window(tmp_path, "sess_1", 50000) == 1000000


def _seed_active_session(root: Path, sid: str = "sess_active") -> None:
    """A minimal index with one active session, so a checkpoint has a target."""
    sessions = root / ".cogsessions"
    (sessions / sid).mkdir(parents=True, exist_ok=True)
    (sessions / "index.json").write_text(
        json.dumps({"sessions": {sid: {"id": sid, "created_at": "2026-01-01", "status": "active"}}})
    )


def test_injector_pressure_messages(tmp_path: Path):
    # The escalation ladder needs an open session: a checkpoint has to have
    # somewhere to go. This test used to run with no session at all, which is
    # why it passed while the escalation was firing into nothing.
    _seed_active_session(tmp_path)
    injector = MemoryInjector(tmp_path)
    assert "MANDATE" in injector.on_user_prompt_submit("test", 82.5)
    assert "WARNING" in injector.on_user_prompt_submit("test", 76.0)
    assert "NOTE" in injector.on_user_prompt_submit("test", 67.0)


def test_no_pressure_messages_without_an_open_session(tmp_path: Path):
    """Never demand a call that cannot succeed.

    With no active session `session_checkpoint` has no target, so telling the
    model it MUST call it is confident, unactionable, and repeats every turn.
    """
    injector = MemoryInjector(tmp_path)
    for pct in (67.0, 76.0, 82.5, 99.9):
        out = injector.on_user_prompt_submit("test", pct)
        assert "MANDATE" not in out
        assert "WARNING" not in out
        assert "NOTE" not in out


def test_settings_merger(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    repo_dir = tmp_path / "repo"
    settings_path = merge_settings(claude_dir, repo_dir)

    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "statusLine" in data
    assert "hooks" in data
    assert "SessionStart" in data["hooks"]
    assert "UserPromptSubmit" in data["hooks"]
