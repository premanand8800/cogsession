"""Regression tests for the v2 foundation fixes.

Each test here corresponds to a bug that shipped in v1 and was observed in real
use. See `docs/V2_DESIGN_NOTES.md`.

The theme is that every one of these bugs was *silent*: the tool kept running,
printed something plausible, and did the wrong thing. So the assertions below
care as much about what is NOT said as about what is.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from cogsession.injector import MemoryInjector
from cogsession.mcp.server import _is_git_tracked
from cogsession.session.loader import SessionLoader

SLUG = "sess_20260101_demo"
HOST_ID = "abc-123-host-id"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one completed session on disk, keyed by our own slug."""
    sess = tmp_path / ".cogsessions" / SLUG
    sess.mkdir(parents=True)
    (sess / "handoff.md").write_text("the handoff body")
    (sess / "manifest.json").write_text(json.dumps({"one_liner": "a summary"}))
    (tmp_path / ".cogsessions" / "index.json").write_text(
        json.dumps(
            {
                "sessions": {
                    SLUG: {
                        "id": SLUG,
                        "created_at": "2026-01-01",
                        "status": "completed",
                        "harness_session_id": HOST_ID,
                    }
                }
            }
        )
    )
    return tmp_path


# ── The two id namespaces ──────────────────────────────────────────────
#
# Sessions are keyed on disk by our slug. Hooks are handed the *host* session
# id. v1 used the argument directly as a directory name, so every lookup missed
# and the tool reported no memory while holding a full store.


def test_a_host_session_id_resolves_to_our_slug(project: Path) -> None:
    assert SessionLoader(project).resolve(HOST_ID) == SLUG


def test_our_own_slug_passes_through(project: Path) -> None:
    assert SessionLoader(project).resolve(SLUG) == SLUG


def test_an_unknown_reference_falls_back_rather_than_failing(project: Path) -> None:
    """The v1 fallback existed but was unreachable, because it only ran when
    the argument was falsy and hooks always pass one."""
    assert SessionLoader(project).resolve("d3adb33f-unknown") == SLUG


def test_no_sessions_resolves_to_none(tmp_path: Path) -> None:
    assert SessionLoader(tmp_path).resolve(HOST_ID) is None


def test_a_missing_handoff_returns_none_not_a_message(project: Path) -> None:
    """v1 returned the not-found text *as* the handoff, so a caller could not
    tell absence from content and printed the message to the user."""
    assert SessionLoader(project).load_handoff("no-such-session") is None


def test_session_start_finds_the_handoff_via_the_host_id(project: Path) -> None:
    out = MemoryInjector(project).on_session_start(HOST_ID)
    assert "the handoff body" in out
    assert "a summary" in out
    assert "No handoff found" not in out
    assert "No summary" not in out


def test_session_start_is_silent_when_there_is_nothing_to_report(tmp_path: Path) -> None:
    assert MemoryInjector(tmp_path).on_session_start(HOST_ID) == ""


# ── Danger zones ───────────────────────────────────────────────────────
#
# This hook can block a write, so a false positive costs real work. v1 matched a
# bare filename as a substring of the whole file, which meant prose armed
# blocks and a zone could never be narrowed to one directory.


@pytest.fixture
def zoned(project: Path) -> MemoryInjector:
    sess = project / ".cogsessions" / SLUG
    (sess / "danger_zones.md").write_text(
        "# Danger Zones\n"
        "# we fixed the bug in leftover.py last week\n"
        "- path: src/api/config.py\n"
        "- `src/core/*.py` - the ports\n"
        "- legacy_shim.py (bare name, any directory)\n"
        "- this line is prose about src/api/config.py and declares nothing\n"
    )
    (project / ".cogsessions" / "index.json").write_text(
        json.dumps(
            {"sessions": {SLUG: {"id": SLUG, "created_at": "2026-01-01", "status": "active"}}}
        )
    )
    return MemoryInjector(project)


def _blocks(inj: MemoryInjector, root: Path, rel: str) -> bool:
    return inj.on_pre_tool_use("Edit", {"file_path": str(root / rel)})[0]


def test_a_declared_path_blocks(zoned: MemoryInjector, project: Path) -> None:
    assert _blocks(zoned, project, "src/api/config.py") is True


def test_the_same_basename_elsewhere_does_not_block(zoned: MemoryInjector, project: Path) -> None:
    """The whole point of declaring a path rather than a name."""
    assert _blocks(zoned, project, "tests/fixtures/config.py") is False


def test_a_declared_glob_blocks(zoned: MemoryInjector, project: Path) -> None:
    assert _blocks(zoned, project, "src/core/contracts.py") is True


def test_a_declared_bare_name_still_reaches_any_directory(
    zoned: MemoryInjector, project: Path
) -> None:
    """Back-compatible: existing files declare bare names and must keep working."""
    assert _blocks(zoned, project, "deep/nested/legacy_shim.py") is True


def test_a_commented_mention_does_not_arm_a_block(zoned: MemoryInjector, project: Path) -> None:
    assert _blocks(zoned, project, "leftover.py") is False


def test_an_unrelated_file_does_not_block(zoned: MemoryInjector, project: Path) -> None:
    assert _blocks(zoned, project, "README.md") is False


def test_a_prose_line_declares_nothing(zoned: MemoryInjector) -> None:
    assert MemoryInjector._zone_pattern("- this line is prose about it") is None
    assert MemoryInjector._zone_pattern("# a comment naming thing.py") is None
    assert MemoryInjector._zone_pattern("") is None


def test_a_write_outside_the_project_matches_on_its_name(
    zoned: MemoryInjector, project: Path
) -> None:
    """Out of tree there is no repo-relative path, so a declared bare name is
    the only thing that can protect it — and a declared path must not match it
    by accident."""
    with tempfile.TemporaryDirectory() as other:
        blocked, _ = zoned.on_pre_tool_use(
            "Edit", {"file_path": str(Path(other) / "legacy_shim.py")}
        )
        assert blocked is True
        allowed, _ = zoned.on_pre_tool_use("Edit", {"file_path": str(Path(other) / "config.py")})
        assert allowed is False


# ── Refusing to write a tracked file ───────────────────────────────────
#
# v1 prepended the handoff into CLAUDE.md, which is committed and shared, and
# did so without reading the `auto_handoff` flag that was supposed to govern it.


def test_a_tracked_file_is_detected(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "CLAUDE.md"
    tracked.write_text("shared, committed, everyone reads it\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "CLAUDE.md"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "add"],
        check=True,
    )
    assert _is_git_tracked(tmp_path, tracked) is True
    assert _is_git_tracked(tmp_path, tmp_path / "CLAUDE.local.md") is False


def test_tracked_check_is_quiet_outside_a_repo(tmp_path: Path) -> None:
    """Must not raise or block in a project that is not version controlled."""
    assert _is_git_tracked(tmp_path, tmp_path / "anything.md") is False
