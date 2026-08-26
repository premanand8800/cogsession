"""
cogsession/distiller.py

Observation into memory.
Deterministic tier: extracts touched files, commands, test results, error text without LLM.
Inferential tier: best-effort LLM extraction for handoff prose/decisions.
"""

from pathlib import Path
from typing import Dict, Any, List
import json
from datetime import datetime, timezone

from cogsession.session.models import Session, LogEntry, DeadEnd


class Distiller:
    """Pure functions converting transcript & hook observations into structured memory."""

    @staticmethod
    def process_deterministic_tool_use(
        session: Session, tool_name: str, tool_input: Dict[str, Any], tool_response: Dict[str, Any]
    ) -> None:
        """Deterministic tier — runs on every tool call."""
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        if tool_name == "Read":
            fp = tool_input.get("file_path", "")
            if fp and fp not in session.files_read:
                session.files_read.append(fp)
                session.append_log(LogEntry(type="file_read", content=fp))

        elif tool_name in ("Write", "Edit", "MultiEdit"):
            fp = tool_input.get("file_path", "")
            if fp:
                if tool_name == "Write" and fp not in session.files_created:
                    session.files_created.append(fp)
                elif fp not in session.files_edited:
                    session.files_edited.append(fp)
                session.append_log(LogEntry(type="file_edit", content=fp))

        elif tool_name == "Bash":
            cmd = tool_input.get("command", "")
            exit_code = tool_response.get("exit_code", 0)
            output = tool_response.get("output", "")

            # Log git operations
            if cmd.startswith("git "):
                session.append_log(LogEntry(type="git_op", content=cmd))

            # Log test runs
            if any(test_runner in cmd for test_runner in ("pytest", "npm test", "jest", "go test")):
                session.append_log(LogEntry(type="test_run", content=cmd))
                if "passed" in output or "failed" in output:
                    # Update environment snapshot counts if present
                    import re

                    pass_match = re.search(r"(\d+)\s+passed", output)
                    fail_match = re.search(r"(\d+)\s+failed", output)
                    if pass_match:
                        session.environment.test_passing = int(pass_match.group(1))
                    if fail_match:
                        session.environment.test_failing = int(fail_match.group(1))
                    session.environment.test_command = cmd

            # Extract tool errors
            if exit_code != 0 and output:
                error_type = "general_error"
                if "ModuleNotFoundError" in output or "ImportError" in output:
                    error_type = "import_error"
                elif "SyntaxError" in output:
                    error_type = "syntax_error"
                elif "PermissionError" in output or "Permission denied" in output:
                    error_type = "permission_error"
                elif "FileNotFoundError" in output:
                    error_type = "file_not_found"

                session.append_log(
                    LogEntry(
                        type="error",
                        content=cmd[:100],
                        context_pct=session.token_pct_at_close,
                    )
                )

                # Append dead end preview if command failed twice or produced obvious error
                if exit_code != 0 and len(output) > 20:
                    session.dead_ends.append(
                        DeadEnd(
                            timestamp=ts,
                            tried=cmd[:100],
                            why_failed=output[:150],
                            use_instead="",
                            context_pct=session.token_pct_at_close,
                        )
                    )
