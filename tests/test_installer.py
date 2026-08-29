"""Installing must never damage a settings file it did not write.

`~/.claude/settings.json` belongs to the user. It carries their model choice,
their permissions, their other tools' hooks and their status line. An installer
that treats it as its own is the fastest way to lose someone's trust, and the
damage is silent — they find out when something they built stops working.

So these tests are mostly about what install leaves *alone*.
"""

from __future__ import annotations

import json
from pathlib import Path

from cogsession.cli import install
from cogsession.installer import HOOKS, hook_command, merge_settings

REPO = Path("/opt/cogsession")


def _settings(claude_dir: Path) -> dict:
    return json.loads((claude_dir / "settings.json").read_text())


def _commands(data: dict, event: str) -> list[str]:
    return [h["command"] for g in data.get("hooks", {}).get(event, []) for h in g["hooks"]]


# ── The two install shapes ─────────────────────────────────────────────


def test_a_checkout_runs_the_hooks_through_uv() -> None:
    assert hook_command("SessionStart", REPO) == (
        f"uv --directory {REPO} run cogsession-hook SessionStart"
    )


def test_a_package_install_runs_the_console_script() -> None:
    """A pip install has no checkout to point at, so the bare command is used."""
    assert hook_command("SessionStart", None) == "cogsession-hook SessionStart"


def test_every_hook_is_registered(tmp_path: Path) -> None:
    merge_settings(tmp_path, REPO)
    data = _settings(tmp_path)
    assert set(data["hooks"]) == {event for event, _ in HOOKS}


# ── What it must leave alone ───────────────────────────────────────────


def test_an_existing_statusline_is_never_overwritten(tmp_path: Path) -> None:
    """A memory tool taking the status bar hostage on install is not a trade
    anyone agreed to, and the previous version did exactly that."""
    (tmp_path / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "their-own-statusline"}})
    )
    merge_settings(tmp_path, REPO)
    assert _settings(tmp_path)["statusLine"]["command"] == "their-own-statusline"


def test_the_statusline_is_claimed_only_when_free(tmp_path: Path) -> None:
    merge_settings(tmp_path, REPO)
    assert "cogsession-hook statusline" in _settings(tmp_path)["statusLine"]["command"]


def test_no_statusline_flag_leaves_it_unset(tmp_path: Path) -> None:
    merge_settings(tmp_path, REPO, set_statusline=False)
    assert "statusLine" not in _settings(tmp_path)


def test_unrelated_settings_survive(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"model": "opus", "permissions": {"allow": ["Bash(git *)"]}})
    )
    merge_settings(tmp_path, REPO)
    data = _settings(tmp_path)
    assert data["model"] == "opus"
    assert data["permissions"] == {"allow": ["Bash(git *)"]}


def test_another_tools_hook_on_the_same_event_survives(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "someone-elses-hook"}]}
                ]
            }
        })
    )
    merge_settings(tmp_path, REPO)
    commands = _commands(_settings(tmp_path), "SessionStart")
    assert "someone-elses-hook" in commands
    assert any("cogsession-hook SessionStart" in c for c in commands)


def test_a_backup_is_written_before_touching_anything(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"model": "opus"}))
    merge_settings(tmp_path, REPO)
    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == {"model": "opus"}


def test_a_corrupt_settings_file_does_not_stop_the_install(tmp_path: Path) -> None:
    """Refusing to install because their file is malformed helps nobody. The
    backup above is what makes overwriting it defensible."""
    (tmp_path / "settings.json").write_text("{ not json at all")
    merge_settings(tmp_path, REPO)
    assert set(_settings(tmp_path)["hooks"]) == {event for event, _ in HOOKS}


# ── Idempotency ────────────────────────────────────────────────────────


def test_installing_twice_does_not_duplicate_hooks(tmp_path: Path) -> None:
    merge_settings(tmp_path, REPO)
    merge_settings(tmp_path, REPO)
    for event, _ in HOOKS:
        assert len(_commands(_settings(tmp_path), event)) == 1


# ── The command ────────────────────────────────────────────────────────


def test_install_command_writes_the_hooks(tmp_path: Path, capsys) -> None:
    code = install(["--repo", str(REPO), "--claude-dir", str(tmp_path), "--no-mcp"])
    assert code == 0
    assert set(_settings(tmp_path)["hooks"]) == {event for event, _ in HOOKS}

    out = capsys.readouterr().out
    assert ".cogsessions/" in out, "must say where sessions land and that it is not committed"
    assert "new session" in out, "must say the change is not picked up by this session"


def test_install_refuses_when_nothing_could_run_the_hooks(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Writing hooks that point at a command which does not exist would look
    like success and behave like nothing."""
    monkeypatch.setattr("cogsession.cli.is_installed_on_path", lambda: False)
    code = install(["--claude-dir", str(tmp_path), "--no-mcp"])
    assert code == 1
    assert not (tmp_path / "settings.json").exists()
    assert "not on PATH" in capsys.readouterr().err
