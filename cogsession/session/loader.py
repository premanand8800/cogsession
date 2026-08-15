"""
cogsession/session/loader.py

Loads session data from disk. Progressive loading — never loads everything.

Loading levels:
  L0 — manifest.json only (~40 tokens)
  L1 — handoff.md (~250 tokens)
  L2 — tasks + decisions + environment (~600 tokens)
  L3 — dead_ends + assumptions + file_tree (~800 tokens)
  L4 — full session_log.jsonl + architecture (on demand only)
"""

import json
from pathlib import Path
from typing import Optional


class SessionLoader:

    def __init__(self, project_root: Path):
        self.root = project_root
        self.sessions_dir = project_root / ".cogsessions"

    # ── Index operations ───────────────────────────────────────────────

    def load_index(self) -> dict:
        """Load the master session index."""
        index_path = self.sessions_dir / "index.json"
        if not index_path.exists():
            return {"sessions": {}, "tree": {}}
        try:
            return json.loads(index_path.read_text())
        except Exception:
            return {"sessions": {}, "tree": {}}

    def list_sessions(self) -> list[dict]:
        """List all sessions sorted by creation date (newest first)."""
        index = self.load_index()
        sessions = list(index.get("sessions", {}).values())
        sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return sessions

    def get_latest_session_id(self) -> Optional[str]:
        """Get the most recent session ID."""
        sessions = self.list_sessions()
        return sessions[0]["id"] if sessions else None

    def get_active_session_id(self) -> Optional[str]:
        """Get the currently active (in-progress) session."""
        for s in self.list_sessions():
            if s.get("status") == "active":
                return s["id"]
        return None

    # ── Progressive loading ────────────────────────────────────────────

    def load_manifest(self, session_id: str) -> Optional[dict]:
        """L0 — manifest only. Always loaded. ~40 tokens."""
        path = self.sessions_dir / session_id / "manifest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def load_handoff(self, session_id: str) -> str:
        """L1 — handoff brief. First thing new session reads. ~250 tokens."""
        path = self.sessions_dir / session_id / "handoff.md"
        if not path.exists():
            return f"[CogSession] No handoff found for {session_id}"
        return path.read_text()

    def load_tasks(self, session_id: str) -> dict:
        """L2 — task list."""
        path = self.sessions_dir / session_id / "tasks.json"
        if not path.exists():
            return {"completed": [], "remaining": [], "blocked": []}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def load_decisions(self, session_id: str) -> list:
        """L2 — decisions, flagged by context quality."""
        path = self.sessions_dir / session_id / "decisions.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except Exception:
            return []

    def load_environment(self, session_id: str) -> dict:
        """L2 — environment snapshot."""
        path = self.sessions_dir / session_id / "environment.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def load_dead_ends(self, session_id: str) -> str:
        """L3 — dead ends. What not to try."""
        path = self.sessions_dir / session_id / "dead_ends.md"
        if not path.exists():
            return "_No dead ends recorded._"
        return path.read_text()

    def load_assumptions(self, session_id: str) -> str:
        """L3 — unverified assumptions."""
        path = self.sessions_dir / session_id / "assumptions.md"
        if not path.exists():
            return "_No assumptions recorded._"
        return path.read_text()

    def load_file_tree(self, session_id: str) -> dict:
        """L3 — files touched this session (delta)."""
        path = self.sessions_dir / session_id / "file_tree.json"
        if not path.exists():
            return {"read": [], "edited": [], "created": []}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def load_mermaid(self, session_id: str) -> str:
        """L4 — architecture diagram."""
        path = self.sessions_dir / session_id / "architecture.mermaid"
        if not path.exists():
            return "graph TD\n    A[No diagram generated yet]\n"
        return path.read_text()

    def load_log(self, session_id: str,
                 type_filter: Optional[str] = None,
                 last_n: int = 50) -> list[dict]:
        """L4 — session event log (last N entries, optional type filter)."""
        path = self.sessions_dir / session_id / "session_log.jsonl"
        if not path.exists():
            return []
        try:
            entries = []
            for line in path.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if type_filter is None or entry.get("type") == type_filter:
                        entries.append(entry)
                except Exception:
                    pass
            return entries[-last_n:]
        except Exception:
            return []

    # ── Tree reconstruction ────────────────────────────────────────────

    def get_parent_chain(self, session_id: str) -> list[str]:
        """Walk up the tree and return all ancestor session IDs."""
        index   = self.load_index()
        sessions = index.get("sessions", {})
        chain   = []
        current = session_id
        visited = set()

        while current and current not in visited:
            visited.add(current)
            manifest = sessions.get(current, {})
            parent   = manifest.get("parent_id")
            if parent:
                chain.append(parent)
                current = parent
            else:
                break

        return chain

    def get_full_file_tree(self, session_id: str) -> dict:
        """
        Build complete file tree by walking up the session tree
        and merging deltas from each ancestor.

        Most recent session's edits take priority.
        """
        chain = [session_id] + self.get_parent_chain(session_id)
        chain.reverse()   # Start from root

        full_tree = {"read": set(), "edited": set(), "created": set()}

        for sid in chain:
            delta = self.load_file_tree(sid)
            full_tree["read"].update(delta.get("read", []))
            full_tree["edited"].update(delta.get("edited", []))
            full_tree["created"].update(delta.get("created", []))

        return {
            "read":    sorted(full_tree["read"]),
            "edited":  sorted(full_tree["edited"]),
            "created": sorted(full_tree["created"]),
        }

    def render_tree(self) -> str:
        """Render the session tree as ASCII for session_tree MCP tool."""
        index    = self.load_index()
        sessions = index.get("sessions", {})
        tree     = index.get("tree", {})

        if not sessions:
            return "No sessions yet."

        # Find root sessions (no parent)
        roots = [
            sid for sid, s in sessions.items()
            if not s.get("parent_id")
        ]
        roots.sort()

        lines = [f"Session Tree — {self.root.name}\n"]

        def render_node(sid: str, prefix: str = "", is_last: bool = True):
            s       = sessions.get(sid, {})
            branch  = "└── " if is_last else "├── "
            status  = {"active": "🟡", "completed": "✅", "abandoned": "❌"}.get(
                s.get("status", ""), "❓"
            )
            date    = s.get("created_at", "")[:10]
            pct     = s.get("token_pct_at_close", 0)
            pct_str = f"({pct:.0f}%ctx)" if pct else "(unknown ctx)"

            liner   = s.get("one_liner", "")[:50]

            lines.append(
                f"{prefix}{branch}{status} {sid}  "
                f"{date}  {pct_str}\n"
                f"{prefix}    {liner}"
            )

            children = tree.get(sid, [])
            child_prefix = prefix + ("    " if is_last else "│   ")
            for i, child in enumerate(children):
                render_node(child, child_prefix, i == len(children) - 1)

        for i, root in enumerate(roots):
            render_node(root, "", i == len(roots) - 1)

        return "\n".join(lines)

    # ── Search ─────────────────────────────────────────────────────────

    def search_all_sessions(self, query: str,
                            session_type: Optional[str] = None) -> list[dict]:
        """
        Search across all session logs for a query string.
        Returns matching entries with session context.
        """
        results = []
        query_lower = query.lower()

        if not self.sessions_dir.exists():
            return []

        for session_dir in self.sessions_dir.iterdir():

            if not session_dir.is_dir():
                continue
            sid = session_dir.name

            # Search session_log.jsonl
            log_path = session_dir / "session_log.jsonl"
            if log_path.exists():
                for line in log_path.read_text().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if session_type and entry.get("type") != session_type:
                            continue
                        if query_lower in entry.get("content", "").lower():
                            results.append({
                                "session_id": sid,
                                "type":       entry.get("type"),
                                "content":    entry.get("content", "")[:120],
                                "timestamp":  entry.get("ts", ""),
                                "context_pct": entry.get("context_pct", 0),
                            })
                    except Exception:
                        pass

            # Also search handoff.md
            handoff_path = session_dir / "handoff.md"
            if handoff_path.exists():
                handoff_text = handoff_path.read_text().lower()
                if query_lower in handoff_text:
                    results.append({
                        "session_id": sid,
                        "type":       "handoff",
                        "content":    f"Query found in handoff.md — see {sid}/handoff.md",
                        "timestamp":  "",
                        "context_pct": 0,
                    })

        # Sort by timestamp descending
        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results[:20]   # Max 20 results
