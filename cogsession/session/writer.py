"""
cogsession/session/writer.py

Writes all session artifacts to the session folder.

Session folder structure:
  .cogsessions/sess_20260413_143022_auth/
    manifest.json          ← always loaded (~40 tokens)
    handoff.md             ← new session reads first (~250 tokens)
    tasks.json             ← current task state
    decisions.json         ← decisions + quality flags
    dead_ends.md           ← what failed and why (THE KEY FILE)
    assumptions.md         ← what agent assumed but didn't verify
    environment.json       ← exact env to restore working state
    danger_zones.md        ← files/areas that need special care
    architecture.mermaid   ← auto-generated codebase diagram
    session_log.jsonl      ← append-only timestamped event stream
    file_tree.json         ← files touched this session (delta)
"""

import json
import subprocess
from pathlib import Path
from datetime import datetime

from cogsession.session.models import Session


class SessionWriter:
    """Writes session state to disk in multiple artifact files."""

    def __init__(self, session: Session):
        self.session = session
        self.dir = session.ensure_dir()

    def write_all(self, context_pct: float = 0.0):
        """Write every artifact. Called at checkpoint."""
        self.session.token_pct_at_close = context_pct
        self.write_manifest()
        self.write_tasks()
        self.write_decisions()
        self.write_dead_ends()
        self.write_assumptions()
        self.write_environment()
        self.write_danger_zones()
        self.write_file_tree()
        self.write_handoff(context_pct)
        self.write_mermaid_diagram()
        self.update_index()
        print(f"[CogSession] ✓ Checkpoint written → {self.dir.name}")

    # ── manifest.json — always loaded ─────────────────────────────────

    def write_manifest(self):
        path = self.dir / "manifest.json"
        path.write_text(json.dumps(self.session.to_manifest(), indent=2))

    # ── tasks.json ────────────────────────────────────────────────────

    def write_tasks(self):
        t = self.session.tasks
        data = {
            "next_priority": t.next_priority,
            "completed":     t.completed,
            "remaining":     t.remaining,
            "blocked":       t.blocked,
        }
        (self.dir / "tasks.json").write_text(json.dumps(data, indent=2))

    # ── decisions.json ────────────────────────────────────────────────

    def write_decisions(self):
        decisions = [
            {
                "timestamp":   d.timestamp,
                "decision":    d.decision,
                "reasoning":   d.reasoning,
                "context_pct": d.context_pct,
                "quality":     d.quality_flag,
                "reversible":  d.reversible,
                "flag":        "⚠ review" if d.quality_flag == "degraded_context" else "",
            }
            for d in self.session.decisions
        ]
        (self.dir / "decisions.json").write_text(json.dumps(decisions, indent=2))

    # ── dead_ends.md — THE MOST VALUABLE FILE ─────────────────────────

    def write_dead_ends(self):
        """
        What didn't work and why. Prevents next session from repeating mistakes.
        Nobody else builds this. This is the unique value.
        """
        if not self.session.dead_ends:
            content = "# Dead Ends\n\n_No dead ends recorded this session._\n"
        else:
            lines = ["# Dead Ends — Approaches That Don't Work\n",
                     "_Read this BEFORE trying anything new. Don't repeat these._\n"]
            for de in self.session.dead_ends:
                ctx_note = ""
                if de.context_pct > 70:
                    ctx_note = f" _(recorded at {de.context_pct:.0f}% ctx — verify)_"
                lines += [
                    f"\n## [{de.timestamp[:10]}] {de.tried[:60]}",
                    f"**Tried:** {de.tried}",
                    f"**Why it failed:** {de.why_failed}",
                    f"**Use instead:** {de.use_instead or 'Unknown — needs investigation'}",
                    f"_Context when recorded: {de.context_pct:.0f}%{ctx_note}_",
                ]
            content = "\n".join(lines) + "\n"

        (self.dir / "dead_ends.md").write_text(content)

    # ── assumptions.md ────────────────────────────────────────────────

    def write_assumptions(self):
        """What the agent assumed but never verified — risk items for next session."""
        if not self.session.assumptions:
            content = "# Assumptions\n\n_No unverified assumptions recorded._\n"
        else:
            risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            sorted_assumptions = sorted(
                self.session.assumptions,
                key=lambda a: risk_order.get(a.risk_level, 3)
            )
            lines = ["# Unverified Assumptions\n",
                     "_Verify these early in the next session before relying on them._\n"]
            for a in sorted_assumptions:
                icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(a.risk_level, "⚪")
                status = "✓ verified" if a.verified else "❌ not verified"
                lines += [
                    f"\n## {icon} [{a.risk_level}] {a.assumption[:70]}",
                    f"**Full assumption:** {a.assumption}",
                    f"**How to verify:** {a.how_to_verify}",
                    f"**Status:** {status}",
                ]
            content = "\n".join(lines) + "\n"

        (self.dir / "assumptions.md").write_text(content)

    # ── environment.json ──────────────────────────────────────────────

    def write_environment(self):
        """
        Exact environment state so next session can restore working state instantly.
        Prevents the 20-minute "how do I run this?" at session start.
        """
        env = self.session.environment

        # Try to auto-detect git state
        try:
            project = Path(self.session.project_root)
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"],
                cwd=project, text=True, stderr=subprocess.DEVNULL
            ).strip()
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=project, text=True, stderr=subprocess.DEVNULL
            ).strip()
            dirty = subprocess.check_output(
                ["git", "diff", "--name-only"],
                cwd=project, text=True, stderr=subprocess.DEVNULL
            ).strip().split("\n")
            dirty = [f for f in dirty if f]

            env.git_branch = branch or env.git_branch
            env.last_git_commit = commit or env.last_git_commit
            if dirty:
                env.dirty_files = dirty
        except Exception:
            pass

        (self.dir / "environment.json").write_text(
            json.dumps({
                "snapshot_at":       env.snapshot_at,
                "git_branch":        env.git_branch,
                "last_git_commit":   env.last_git_commit,
                "dirty_files":       env.dirty_files,
                "python_version":    env.python_version,
                "venv_path":         env.venv_path,
                "start_commands":    env.start_commands,
                "ports":             env.ports,
                "env_vars_needed":   env.env_vars_needed,
                "env_vars_missing":  env.env_vars_missing,
                "test_command":      env.test_command,
                "test_passing":      env.test_passing,
                "test_failing":      env.test_failing,
                "test_failing_files": env.test_failing_files,
            }, indent=2)
        )

    # ── danger_zones.md ───────────────────────────────────────────────

    def write_danger_zones(self):
        if not self.session.danger_zones:
            content = "# Danger Zones\n\n_No danger zones flagged._\n"
        else:
            lines = ["# Danger Zones — Handle With Care\n",
                     "_These files/areas have gotchas. Read before touching._\n"]
            for dz in self.session.danger_zones:
                lines.append(f"\n- {dz}")
            content = "\n".join(lines) + "\n"

        (self.dir / "danger_zones.md").write_text(content)

    # ── file_tree.json ────────────────────────────────────────────────

    def write_file_tree(self):
        """Delta of files touched this session — not full tree."""
        data = {
            "session_id": self.session.id,
            "delta_note": "Files touched THIS session only. Walk parent chain for full tree.",
            "read":    list(set(self.session.files_read)),
            "edited":  list(set(self.session.files_edited)),
            "created": list(set(self.session.files_created)),
        }
        (self.dir / "file_tree.json").write_text(json.dumps(data, indent=2))

    # ── handoff.md — NEW SESSION READS THIS FIRST ─────────────────────

    def write_handoff(self, context_pct: float):
        """
        The handoff document. This is the product.
        Next session reads this first — must orient in ~250 tokens.
        Written at checkpoint time.
        """
        t = self.session.tasks
        env = self.session.environment

        # Quality note about context level
        quality_note = ""
        if context_pct >= 75:
            quality_note = (
                f"\n> ⚠ **Context Quality Warning:** This handoff was written at "
                f"**{context_pct:.0f}% context**. Decisions made in the last 20% of "
                f"this session may be lower quality. Review flagged decisions in "
                f"`decisions.json` before proceeding.\n"
            )

        # Completed tasks
        done_section = ""
        if t.completed:
            done_section = "## Done This Session\n"
            for item in t.completed[-10:]:   # Last 10 only
                done_section += f"- [x] {item}\n"

        # Remaining tasks
        todo_section = "## Do This Next\n"
        if t.next_priority:
            todo_section += f"**PRIORITY 1:** {t.next_priority}\n\n"
        if t.remaining:
            for item in t.remaining[:8]:     # Top 8
                todo_section += f"- [ ] {item}\n"
        if t.blocked:
            todo_section += "\n**Blocked:**\n"
            for item in t.blocked:
                todo_section += f"- [ ] ~~{item}~~ (blocked)\n"

        # Environment quick-start
        start_cmd = ""
        if env.start_commands:
            start_cmd = "## Start Environment\n```bash\n"
            start_cmd += "\n".join(env.start_commands)
            start_cmd += "\n```\n"
        if env.test_command:
            fail_note = ""
            if env.test_failing > 0:
                fail_note = f" ← ⚠ {env.test_failing} failing"
            start_cmd += f"\n```bash\n{env.test_command}  # {env.test_passing} passing{fail_note}\n```\n"

        # Dead ends warning
        dead_ends_note = ""
        if self.session.dead_ends:
            dead_ends_note = (
                f"\n## ⛔ Don't Repeat These ({len(self.session.dead_ends)} dead ends)\n"
                + "See `dead_ends.md` for details.\n"
            )
            for de in self.session.dead_ends[-3:]:   # Last 3 as preview
                dead_ends_note += f"- **{de.tried[:50]}** → {de.why_failed[:60]}\n"

        # High-risk assumptions
        high_risk = [a for a in self.session.assumptions
                     if a.risk_level == "HIGH" and not a.verified]
        assumptions_note = ""
        if high_risk:
            assumptions_note = (
                f"\n## 🔴 Verify These First ({len(high_risk)} HIGH risk assumptions)\n"
            )
            for a in high_risk[:3]:
                assumptions_note += f"- {a.assumption[:80]}\n  → {a.how_to_verify}\n"

        # Danger zones
        danger_note = ""
        if self.session.danger_zones:
            danger_note = "\n## ⚡ Handle With Care\n"
            for dz in self.session.danger_zones[:5]:
                danger_note += f"- {dz}\n"

        # Files to look at first
        key_files = list(set(self.session.files_edited + self.session.files_created))[:6]
        files_note = ""
        if key_files:
            files_note = "\n## Key Files This Session\n"
            for f in key_files:
                files_note += f"- `{f}`\n"

        handoff = f"""# Handoff: {self.session.id}

**Focus:** {self.session.focus or 'General development'}
**Closed:** {context_pct:.0f}% context | {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Parent:** {self.session.parent_id or 'root'}
{quality_note}
## Summary
{self.session.one_liner or 'No summary recorded.'}

{start_cmd}
{done_section}
{todo_section}
{files_note}
{dead_ends_note}
{assumptions_note}
{danger_note}
---
_Full details: `tasks.json` | `decisions.json` | `dead_ends.md` | `assumptions.md`_
_Architecture: `architecture.mermaid`_
"""
        (self.dir / "handoff.md").write_text(handoff.strip() + "\n")

    # ── architecture.mermaid ──────────────────────────────────────────

    def write_mermaid_diagram(self):
        """
        Auto-generate Mermaid diagram from project file structure.
        Scans Python/JS/TS files and maps imports to build the graph.
        """
        project = Path(self.session.project_root)
        diagram = self._generate_mermaid(project)
        (self.dir / "architecture.mermaid").write_text(diagram)

    def _generate_mermaid(self, project: Path) -> str:
        """
        Scan project files and generate a dependency graph.
        Simple heuristic-based — no AST parsing needed for MVP.
        """
        import re

        # Find Python files (or JS/TS)
        py_files = list(project.glob("**/*.py"))[:50]  # Cap at 50
        py_files = [f for f in py_files if not any(
            skip in str(f) for skip in
            ["__pycache__", "node_modules", ".git", "venv", ".venv"]
        )]

        if not py_files:
            return (
                "graph TD\n"
                "    A[Project Root]\n"
                "    A --> B[No Python files found]\n"
                f"    note[Generated: {datetime.now().strftime('%Y-%m-%d')}]\n"
            )

        edges = set()
        nodes = {}

        for py_file in py_files:
            rel = py_file.relative_to(project)
            node_id = str(rel).replace("/", "_").replace(".", "_")
            label   = str(rel)
            nodes[node_id] = label

            try:
                content = py_file.read_text(errors="ignore")
                # Find local imports: from .auth import router  /  from src.auth import
                imports = re.findall(
                    r'^(?:from|import)\s+([\w.]+)',
                    content, re.MULTILINE
                )
                for imp in imports:
                    # Try to find matching file
                    parts = imp.replace(".", "/")
                    candidates = [
                        project / f"{parts}.py",
                        project / parts / "__init__.py",
                    ]
                    for candidate in candidates:
                        if candidate.exists():
                            try:
                                rel2    = candidate.relative_to(project)
                                node2   = str(rel2).replace("/","_").replace(".","_")
                                label2  = str(rel2)
                                nodes[node2] = label2
                                edges.add((node_id, node2))
                            except ValueError:
                                pass
            except Exception:
                pass

        # Build diagram
        lines = [
            f"graph TD",
            f"    %% Generated by CogSession on {datetime.now().strftime('%Y-%m-%d')}",
            f"    %% Session: {self.session.id}",
            "",
        ]

        # Add nodes
        for node_id, label in nodes.items():
            # Shorten label for readability
            short = label.replace("src/", "").replace(".py", "")
            lines.append(f'    {node_id}["{short}"]')

        lines.append("")

        # Add edges
        for src, dst in sorted(edges):
            lines.append(f"    {src} --> {dst}")

        # Add external connections if detected
        lines.append("")
        lines.append("    %% External dependencies (detected from imports)")

        if py_files:
            all_content = " ".join(
                f.read_text(errors="ignore") for f in py_files[:10]
            )
            if "fastapi" in all_content.lower():
                lines.append('    ext_fastapi["FastAPI 🌐"]')
            if "sqlalchemy" in all_content.lower() or "sqlmodel" in all_content.lower():
                lines.append('    ext_db[("Database 🗄")]')
            if "redis" in all_content.lower():
                lines.append('    ext_redis[("Redis ⚡")]')
            if "qdrant" in all_content.lower():
                lines.append('    ext_qdrant[("Qdrant 🔍")]')

        return "\n".join(lines) + "\n"

    # ── Global index ──────────────────────────────────────────────────

    def update_index(self):
        """Update .cogsessions/index.json with this session."""
        index_path = Path(self.session.project_root) / ".cogsessions" / "index.json"

        # Load existing
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text())
            except Exception:
                index = {"sessions": {}, "tree": {}}
        else:
            index = {"sessions": {}, "tree": {}}

        # Update
        index["sessions"][self.session.id] = self.session.to_manifest()

        # Update tree links
        if self.session.parent_id:
            if self.session.parent_id not in index["tree"]:
                index["tree"][self.session.parent_id] = []
            children = index["tree"][self.session.parent_id]
            if self.session.id not in children:
                children.append(self.session.id)

        index["updated_at"] = datetime.now().isoformat()
        index_path.write_text(json.dumps(index, indent=2))
