"""The session lifecycle, driven through the real hook entry point.

These go through `hook_entry` as a subprocess rather than calling functions,
because the bugs they cover only appeared in the wiring: a session that was
never created, and two handlers that printed a save they never performed.

The hook contract is that it must never break the developer's session, so
every case here also asserts a clean exit.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _hook(event: str, root: Path, **payload) -> subprocess.CompletedProcess:
    body = {"cwd": str(root), "session_id": payload.pop("session_id", "host-abc-123"), **payload}
    return subprocess.run(
        [sys.executable, "-m", "cogsession.hook_entry", event],
        input=json.dumps(body), capture_output=True, text=True, cwd=str(root),
    )


def _index(root: Path) -> dict:
    return json.loads((root / ".cogsessions" / "index.json").read_text())


def _sessions(root: Path) -> list[dict]:
    return list(_index(root).get("sessions", {}).values())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


# ── Auto-init: tracking must not depend on anyone remembering ──────────


def test_session_start_opens_a_session_in_a_fresh_project(repo: Path) -> None:
    """The whole point. Previously a session existed only if someone called
    session_init by hand, so the common case was hooks firing and nothing
    being recorded at all."""
    proc = _hook("SessionStart", repo)
    assert proc.returncode == 0
    sessions = _sessions(repo)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "active"


def test_the_host_session_id_is_recorded(repo: Path) -> None:
    """Without this the next hook cannot map its own id back to our slug."""
    _hook("SessionStart", repo, session_id="host-xyz-789")
    assert _sessions(repo)[0]["harness_session_id"] == "host-xyz-789"


def test_a_second_session_start_does_not_open_a_duplicate(repo: Path) -> None:
    _hook("SessionStart", repo)
    _hook("SessionStart", repo)
    assert len(_sessions(repo)) == 1


def test_nothing_is_created_when_disabled(repo: Path) -> None:
    (repo / ".cogsession.json").write_text(json.dumps({"enabled": False}))
    _hook("SessionStart", repo)
    assert not (repo / ".cogsessions" / "index.json").exists()


# ── The two handlers that used to lie ──────────────────────────────────


def test_pre_compact_writes_what_it_claims_to_write(repo: Path) -> None:
    """It printed "checkpoint saved" and wrote nothing. For a memory tool,
    claiming a save it never made is the worst failure available."""
    _hook("SessionStart", repo)
    proc = _hook("PreCompact", repo)
    assert proc.returncode == 0

    sid = _sessions(repo)[0]["id"]
    log = (repo / ".cogsessions" / sid / "session_log.jsonl").read_text()
    assert any(json.loads(l)["type"] == "compaction" for l in log.splitlines() if l.strip())


def test_session_end_actually_closes_the_session(repo: Path) -> None:
    """A session left active forever is not harmless: the lookup returns the
    first active one it finds, so a stale session captures every later lookup
    and new work is logged against old work."""
    _hook("SessionStart", repo)
    proc = _hook("SessionEnd", repo)
    assert proc.returncode == 0

    entry = _sessions(repo)[0]
    assert entry["status"] == "completed"
    assert entry["closed_at"]
    assert entry["checkpoint_trigger"] == "session_end"


def test_a_closed_session_frees_the_active_slot(repo: Path) -> None:
    _hook("SessionStart", repo)
    first = _sessions(repo)[0]["id"]
    _hook("SessionEnd", repo)
    _hook("SessionStart", repo, session_id="host-second-run")

    ids = {s["id"] for s in _sessions(repo)}
    assert len(ids) == 2, "a new session must open once the previous one closed"
    active = [s for s in _sessions(repo) if s["status"] == "active"]
    assert len(active) == 1 and active[0]["id"] != first


def test_post_tool_use_logs_against_the_auto_opened_session(repo: Path) -> None:
    """Before auto-init this returned early every time, so tool activity was
    never recorded for anyone who had not run session_init."""
    _hook("SessionStart", repo)
    _hook("PostToolUse", repo, tool_name="Edit")

    sid = _sessions(repo)[0]["id"]
    log = (repo / ".cogsessions" / sid / "session_log.jsonl").read_text()
    assert any(json.loads(l)["tool"] == "Edit" for l in log.splitlines() if l.strip())


def test_hooks_emit_no_diagnostics_on_stderr(repo: Path) -> None:
    """A deprecation warning from a hook is still noise in the transcript."""
    _hook("SessionStart", repo)
    for event, kw in (("PostToolUse", {"tool_name": "Edit"}), ("PreCompact", {}), ("SessionEnd", {})):
        proc = _hook(event, repo, **kw)
        assert proc.stderr.strip() == "", f"{event} wrote to stderr: {proc.stderr!r}"


# ── The hook contract: never break the session ─────────────────────────


@pytest.mark.parametrize("event", ["SessionStart", "PostToolUse", "PreCompact", "SessionEnd"])
def test_every_handler_exits_cleanly_on_garbage_input(repo: Path, event: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "cogsession.hook_entry", event],
        input="not json at all", capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode == 0


@pytest.mark.parametrize("event", ["PostToolUse", "PreCompact", "SessionEnd"])
def test_handlers_are_quiet_with_no_session(repo: Path, event: str) -> None:
    """Nothing to report is not an error, and must not be announced."""
    proc = _hook(event, repo)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
