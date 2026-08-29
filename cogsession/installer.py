"""
cogsession/installer.py

Merges CogSession hooks into ~/.claude/settings.json.

Idempotent, preserves existing keys and unrelated hooks, backs up first.

Two install shapes, and the difference is the command the hooks run:

  from a clone      uv --directory <repo> run cogsession-hook <event>
  pip installed     cogsession-hook <event>          (console script on PATH)

The second exists because the hooks are most of what makes this work — a
session opening itself, the journal recording as work happens, claims
re-checked on start. A package install cannot write to `~/.claude/settings.json`
on its own, so without `cogsession install` a pip user gets eleven tools they
must call by hand and none of the recording. That is a different, worse product.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from shutil import which
from typing import Optional

#: Every hook, and the tool pattern it fires on. One list so the six are
#: registered the same way and a new event cannot be added to only half.
HOOKS: tuple[tuple[str, str], ...] = (
    ("PreToolUse", "Read|Write|Edit|MultiEdit|Bash"),
    ("PostToolUse", "Bash|Write|Edit"),
    ("SessionStart", "*"),
    ("UserPromptSubmit", "*"),
    ("PreCompact", "*"),
    ("SessionEnd", "*"),
)


def hook_command(event: str, repo_dir: Optional[Path] = None) -> str:
    """The shell command a hook runs, for whichever way this was installed.

    `repo_dir` means "run from that checkout via uv". Omitting it means the
    console script is on PATH, which is the pip case.
    """
    if repo_dir is not None:
        return f"uv --directory {repo_dir} run cogsession-hook {event}"
    return f"cogsession-hook {event}"


def is_installed_on_path() -> bool:
    """Is `cogsession-hook` runnable as a bare command?"""
    return which("cogsession-hook") is not None


def merge_settings(
    claude_dir: Path,
    repo_dir: Optional[Path] = None,
    *,
    set_statusline: bool = True,
) -> Path:
    """Merge the hooks into `~/.claude/settings.json` and return its path.

    `repo_dir=None` registers the console-script form, for a pip install.
    """
    settings_file = claude_dir / "settings.json"

    # Backup if settings file exists
    if settings_file.exists():
        backup_file = claude_dir / f"settings.json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(settings_file, backup_file)

    existing_config = {}
    if settings_file.exists():
        try:
            existing_config = json.loads(settings_file.read_text())
        except Exception:
            existing_config = {}

    # Define cogsession hook commands using uv --directory <repo> run cogsession-hook <event>
    def make_cmd(event: str) -> str:
        return hook_command(event, repo_dir)

    # Only claim the status line if nothing else has it. Overwriting silently
    # destroys whatever the user built, and a memory tool taking the status bar
    # hostage on install is not a trade anyone agreed to.
    existing_statusline = existing_config.get("statusLine")
    if set_statusline and not existing_statusline:
        existing_config["statusLine"] = {
            "type": "command",
            "command": make_cmd("statusline"),
            "padding": 0,
        }

    # Hooks configuration
    hooks = existing_config.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    def ensure_hook_entry(hook_list: list, matcher: str, command: str) -> list:
        # Avoid duplicate registration
        for item in hook_list:
            if isinstance(item, dict) and item.get("matcher") == matcher:
                inner_hooks = item.get("hooks", [])
                if any(h.get("command") == command for h in inner_hooks if isinstance(h, dict)):
                    return hook_list
        hook_list.append(
            {
                "matcher": matcher,
                "hooks": [{"type": "command", "command": command}],
            }
        )
        return hook_list

    for event, matcher in HOOKS:
        current = hooks.get(event, [])
        if not isinstance(current, list):
            current = []
        hooks[event] = ensure_hook_entry(current, matcher, make_cmd(event))

    existing_config["hooks"] = hooks

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(existing_config, indent=2))
    return settings_file
