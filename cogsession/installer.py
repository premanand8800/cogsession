"""
cogsession/installer.py

Merges CogSession hooks into ~/.claude/settings.json using Python.
Idempotent, preserves existing keys and unrelated hooks, creates a backup first.
"""

import json
from pathlib import Path
import shutil
from datetime import datetime


def merge_settings(claude_dir: Path, repo_dir: Path) -> Path:
    """Merges cogsession-hook settings into ~/.claude/settings.json."""
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
        return f"uv --directory {repo_dir} run cogsession-hook {event}"

    # Statusline
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

    # PreToolUse
    pre_tool = hooks.get("PreToolUse", [])
    if not isinstance(pre_tool, list):
        pre_tool = []
    pre_tool = ensure_hook_entry(pre_tool, "Read|Write|Edit|MultiEdit|Bash", make_cmd("PreToolUse"))
    hooks["PreToolUse"] = pre_tool

    # PostToolUse
    post_tool = hooks.get("PostToolUse", [])
    if not isinstance(post_tool, list):
        post_tool = []
    post_tool = ensure_hook_entry(post_tool, "Bash|Write|Edit", make_cmd("PostToolUse"))
    hooks["PostToolUse"] = post_tool

    # SessionStart
    session_start = hooks.get("SessionStart", [])
    if not isinstance(session_start, list):
        session_start = []
    session_start = ensure_hook_entry(session_start, "*", make_cmd("SessionStart"))
    hooks["SessionStart"] = session_start

    # UserPromptSubmit
    user_prompt = hooks.get("UserPromptSubmit", [])
    if not isinstance(user_prompt, list):
        user_prompt = []
    user_prompt = ensure_hook_entry(user_prompt, "*", make_cmd("UserPromptSubmit"))
    hooks["UserPromptSubmit"] = user_prompt

    # PreCompact
    pre_compact = hooks.get("PreCompact", [])
    if not isinstance(pre_compact, list):
        pre_compact = []
    pre_compact = ensure_hook_entry(pre_compact, "*", make_cmd("PreCompact"))
    hooks["PreCompact"] = pre_compact

    # SessionEnd
    session_end = hooks.get("SessionEnd", [])
    if not isinstance(session_end, list):
        session_end = []
    session_end = ensure_hook_entry(session_end, "*", make_cmd("SessionEnd"))
    hooks["SessionEnd"] = session_end

    existing_config["hooks"] = hooks

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(existing_config, indent=2))
    return settings_file
