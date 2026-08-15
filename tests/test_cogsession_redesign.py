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


def test_injector_pressure_messages(tmp_path: Path):
    injector = MemoryInjector(tmp_path)
    assert "MANDATE" in injector.on_user_prompt_submit("test", 82.5)
    assert "WARNING" in injector.on_user_prompt_submit("test", 76.0)
    assert "NOTE" in injector.on_user_prompt_submit("test", 67.0)


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
