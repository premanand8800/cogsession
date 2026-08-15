"""
cogsession/injector.py

Injects memory, nudges, mandates, and danger zone blocks based on context % and prompt/tool action.
"""

from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from cogsession.session.loader import SessionLoader


class MemoryInjector:
    """Handles injection logic for SessionStart, UserPromptSubmit, PreToolUse, PreCompact."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.loader = SessionLoader(project_root)

    def on_session_start(self, session_id: Optional[str] = None) -> str:
        """L0 manifest + L1 handoff unprompted on SessionStart."""
        sid = session_id or self.loader.get_latest_session_id()
        if not sid:
            return ""

        manifest = self.loader.load_manifest(sid) or {}
        handoff = self.loader.load_handoff(sid)
        one_liner = manifest.get("one_liner", "No summary")

        output = [
            f"[CogSession] Previous Memory Loaded ({sid}):",
            f"Summary: {one_liner}",
            "",
            "--- Handoff ---",
            handoff,
            "---------------",
        ]
        return "\n".join(output)

    def on_user_prompt_submit(
        self, prompt: str, context_pct: Optional[float]
    ) -> str:
        """
        Nudges / mandates based on context_pct:
          <65%: silent (unless prompt matches dead ends/danger zones)
          65-74%: one-line nudge
          75-79%: strong recommendation
          >=80%: mandate naming checkpoint tool
        Also performs targeted recall against prompt.
        """
        messages = []

        # 1. Context pressure messaging
        if context_pct is not None:
            if context_pct >= 80:
                messages.append(
                    f"⚠ [CogSession MANDATE] Context load is at {context_pct:.1f}% (>=80%). "
                    "You MUST call `session_checkpoint` now to save session state before context truncation."
                )
            elif context_pct >= 75:
                messages.append(
                    f"⚡ [CogSession WARNING] Context load is at {context_pct:.1f}% (>=75%). "
                    "Strongly recommend calling `session_checkpoint` soon."
                )
            elif context_pct >= 65:
                messages.append(
                    f"· [CogSession NOTE] Context load is at {context_pct:.1f}%."
                )

        # 2. Targeted recall (relevance search against incoming prompt)
        if prompt and len(prompt.strip()) > 3:
            search_results = self.loader.search_all_sessions(prompt.strip())
            if search_results:
                relevant = []
                for r in search_results[:3]:
                    relevant.append(
                        f"- [{r['type']}] ({r['session_id'][:12]}): {r['content']}"
                    )
                if relevant:
                    messages.append("\n[CogSession Relevant Memories]:")
                    messages.extend(relevant)

        return "\n".join(messages)

    def on_pre_tool_use(
        self, tool_name: str, tool_input: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Checks if a Write/Edit operation targets a danger zone file.
        Returns (should_block: bool, reason: str).
        Should block (exit 2) ONLY on positive danger zone match.
        """
        if tool_name not in ("Write", "Edit", "MultiEdit"):
            return False, ""

        file_path_str = tool_input.get("file_path", "")
        if not file_path_str:
            return False, ""

        file_path = Path(file_path_str)
        target_name = file_path.name

        active_id = self.loader.get_active_session_id() or self.loader.get_latest_session_id()
        if not active_id:
            return False, ""

        dz_path = self.root / ".cogsessions" / active_id / "danger_zones.md"
        if dz_path.exists():
            content = dz_path.read_text()
            # If target filename is in danger_zones.md
            if target_name in content:
                for line in content.splitlines():
                    if target_name in line:
                        return True, f"Blocking write to danger zone file '{target_name}': {line.strip()}"
                return True, f"Blocking write to danger zone file '{target_name}' per {dz_path.name}"

        return False, ""
