"""
cogsession/cli.py

`cogsession install` — wire the hooks into Claude Code after a package install.

The MCP server gives an agent eleven tools it can call. The hooks are what make
CogSession work *without being asked*: the session opening itself, the journal
recording as work happens, claims re-checked when a session starts. Those live
in `~/.claude/settings.json`, which a package install cannot write on its own.

So this is the second half of `pip install cogsession`. Without it you get the
tools and none of the recording, which is a quieter and worse product than the
one described in the README.

It is idempotent, backs up the settings file first, and prints what it changed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from cogsession.installer import HOOKS, hook_command, is_installed_on_path, merge_settings

MCP_SERVER_NAME = "cogsession"


def _register_mcp_server(repo_dir: Path | None) -> str:
    """Register the MCP server with the Claude Code CLI, if it is present.

    A missing `claude` binary is not an error. Plenty of people wire MCP servers
    up by hand or use another client, and failing the whole install over an
    optional convenience would be wrong.
    """
    claude = shutil.which("claude")
    if claude is None:
        return "  ~ `claude` not on PATH — register the MCP server yourself (see README)"

    if repo_dir is not None:
        command = [claude, "mcp", "add", MCP_SERVER_NAME, "--",
                   "uv", "--directory", str(repo_dir), "run", MCP_SERVER_NAME]
    else:
        command = [claude, "mcp", "add", MCP_SERVER_NAME, "--", MCP_SERVER_NAME]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"  ~ could not register the MCP server: {exc}"

    if result.returncode == 0:
        return f"  ✓ MCP server registered as `{MCP_SERVER_NAME}`"
    # Already-registered is the common non-zero case and is not a failure.
    detail = (result.stderr or result.stdout).strip().splitlines()
    return f"  ~ MCP server not registered: {detail[0] if detail else 'unknown reason'}"


def install(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cogsession install",
        description="Wire CogSession's hooks into Claude Code.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        metavar="DIR",
        help="Run the hooks from this checkout via uv, instead of the installed "
             "console script. Use when working on CogSession itself.",
    )
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=Path.home() / ".claude",
        metavar="DIR",
        help="Claude Code config directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--no-statusline",
        action="store_true",
        help="Do not offer to set the status line.",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Only write the hooks; skip registering the MCP server.",
    )
    args = parser.parse_args(argv)

    repo_dir = args.repo.resolve() if args.repo else None

    if repo_dir is None and not is_installed_on_path():
        print(
            "cogsession-hook is not on PATH, so the hooks would not be able to run.\n"
            "Either install the package (`pip install cogsession`) or point at a\n"
            "checkout with `cogsession install --repo /path/to/cogsession`.",
            file=sys.stderr,
        )
        return 1

    settings = merge_settings(
        args.claude_dir, repo_dir, set_statusline=not args.no_statusline
    )

    print("CogSession installed.\n")
    print(f"  ✓ {len(HOOKS)} hooks written to {settings}")
    print(f"    they run: {hook_command('<event>', repo_dir)}")

    if not args.no_mcp:
        print(_register_mcp_server(repo_dir))

    print(
        "\n  Add `.cogsessions/` to your .gitignore — that is where sessions are kept,\n"
        "  and it is yours, not something to commit.\n"
        "\n  Open a new session to pick it up. Existing sessions loaded their\n"
        "  settings at start and will not see these hooks."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `cogsession-admin` console script."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "install":
        return install(argv[1:])

    print(
        "Usage: cogsession-admin install [options]\n"
        "\n"
        "  install   Wire CogSession's hooks into Claude Code.\n"
        "\n"
        "The MCP server itself is the `cogsession` command, which an MCP client\n"
        "launches for you — it is not meant to be run by hand.",
        file=sys.stderr,
    )
    return 0 if not argv else 1


if __name__ == "__main__":
    raise SystemExit(main())
