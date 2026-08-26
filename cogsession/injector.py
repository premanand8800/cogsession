"""
cogsession/injector.py

Injects memory, nudges, mandates, and danger zone blocks based on context % and prompt/tool action.
"""

import re
from fnmatch import fnmatch
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
        sid = self.loader.resolve(session_id)
        if not sid:
            return ""

        manifest = self.loader.load_manifest(sid) or {}
        handoff = self.loader.load_handoff(sid)
        one_liner = manifest.get("one_liner")

        # Nothing to say is better than announcing that we found nothing. The
        # previous version printed "No summary" and the not-found message as
        # though they were the recovered memory.
        if not handoff and not one_liner:
            return ""

        output = [f"[CogSession] Previous memory loaded ({sid}):"]
        if one_liner:
            output.append(f"Summary: {one_liner}")
        if handoff:
            output += ["", "--- Handoff ---", handoff, "---------------"]
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

        # A checkpoint needs somewhere to go. Without an open session the
        # escalation below names a call that cannot succeed, which is worse
        # than silence: it is confident, unactionable and repeats every turn.
        has_session = self.loader.get_active_session_id() is not None

        # 1. Context pressure messaging
        if context_pct is not None and has_session:
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

        active_id = self.loader.resolve(None)
        if not active_id:
            return False, ""

        dz_path = self.root / ".cogsessions" / active_id / "danger_zones.md"
        if not dz_path.exists():
            return False, ""

        target = self._rel_path(file_path_str)
        if target is None:
            return False, ""

        for raw in dz_path.read_text().splitlines():
            entry = self._zone_pattern(raw)
            if not entry:
                continue
            # A declared bare name reaches into any directory; a path or glob
            # is matched as written so it can be narrowed to one location.
            patterns = [entry] if ("/" in entry or "*" in entry) else [entry, f"*/{entry}"]
            if any(fnmatch(target, pat) for pat in patterns):
                return True, (
                    f"Blocking write to danger zone '{entry}' "
                    f"(matched {target}): {raw.strip()}"
                )

        return False, ""

    # ── Danger zone helpers ────────────────────────────────────────────

    def _rel_path(self, file_path_str: str) -> Optional[str]:
        """Normalise a write target for matching.

        Inside the project this is the repo-relative POSIX path, which is what
        lets a zone be narrowed to one directory. A target outside the project
        has no such path, so fall back to its bare name: a declared bare name
        still protects it, while a declared path cannot match it by accident.
        """
        try:
            p = Path(file_path_str)
            resolved = p.resolve()
        except OSError:
            return None
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return p.name or None

    _FILENAME_RE = re.compile(r"^[\w.+-]+\.[A-Za-z0-9_]+$")

    @classmethod
    def _zone_pattern(cls, line: str) -> Optional[str]:
        """Extract a path pattern from one line of `danger_zones.md`.

        A zone has to be *declared*, not *mentioned*. Previously any filename
        appearing anywhere in the file matched as a substring, so a note
        explaining why something had once been dangerous armed a block against
        it, and a bare name could never be narrowed to one directory. This
        hook can stop a write, so a false positive costs real work.

        Recognised, one declaration per line, the pattern first:

            - src/core/ports.py            exact, repo-relative
            - src/core/*.py                glob
            - path: src/core/ports.py      explicit key
            - `src/core/ports.py` - why    backticked, prose after it
            - ports.py (why)               bare name: matches any directory

        A bare name keeps the old permissive reach, because that is how
        existing files are written, but only when it is the first token on the
        line. Anything whose first token is a word rather than a filename is
        prose and matches nothing.
        """
        text = line.strip()
        if not text or text.startswith("#"):
            return None
        text = text.lstrip("-*").strip()
        if not text:
            return None
        if text.lower().startswith("path:"):
            text = text.split(":", 1)[1].strip()
        if "`" in text:
            parts = text.split("`")
            if len(parts) >= 3 and parts[1].strip():
                text = parts[1].strip()
        else:
            text = text.split()[0] if text.split() else ""
        text = text.rstrip(":,;.").strip()
        if not text:
            return None
        # Either a path/glob, or something that actually looks like a filename.
        if "/" in text or "*" in text:
            return text
        if cls._FILENAME_RE.match(text):
            return text
        return None
