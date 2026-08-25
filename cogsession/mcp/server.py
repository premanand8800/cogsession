"""
cogsession/mcp/server.py

The CogSession MCP Server.

8 tools for managing coding session memory:

  session_init        → Start a new session (with optional parent)
  session_checkpoint  → Save everything NOW (call at 70-80% context)
  session_load        → Load handoff from previous session
  session_update      → Add decisions, dead ends, assumptions, tasks
  session_tree        → Show the full session tree
  session_search      → Search across all sessions
  session_status      → What's the current session state?
  session_diagram     → Show/regenerate the architecture diagram

How to run:
  uv run cogsession

Connect to Claude Code:
  claude mcp add cogsession -- uv run cogsession
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from cogsession.session.models import (
    Session, LogEntry, DeadEnd, Assumption, Decision,
    TaskList, EnvironmentSnapshot, new_id, now_iso
)
from cogsession.session.writer import SessionWriter
from cogsession.session.loader import SessionLoader
from cogsession.config import load_project_config, is_enabled

# ── Active session state (in-memory) ──────────────────────────────────
# One session per MCP server instance (one project at a time)
_active_session: Optional[Session] = None
_project_root:   Optional[Path]    = None

app = Server("cogsession")


# ══════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [

        types.Tool(
            name="session_init",
            description=(
                "Initialize a new CogSession for the current project. "
                "Call this at the START of a Claude Code session. "
                "Provide parent_session_id to continue from a previous session (tree structure). "
                "Provide focus to describe what this session is about."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {
                        "type": "string",
                        "description": "Absolute path to project root"
                    },
                    "focus": {
                        "type": "string",
                        "description": "What this session is about (e.g. 'auth module JWT implementation')",
                        "default": ""
                    },
                    "parent_session_id": {
                        "type": "string",
                        "description": "ID of the session to continue from (for tree structure)"
                    }
                },
                "required": ["project_root"]
            }
        ),

        types.Tool(
            name="session_checkpoint",
            description=(
                "Save the current session state to disk. "
                "CALL THIS at 70-80% context. "
                "Writes: handoff.md, tasks.json, dead_ends.md, assumptions.md, "
                "environment.json, architecture.mermaid, session_log.jsonl. "
                "Also auto-writes handoff.md to CLAUDE.md so next session loads it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "context_pct": {
                        "type": "number",
                        "description": "Current context window usage percentage"
                    },
                    "one_liner": {
                        "type": "string",
                        "description": "One sentence summary of what happened this session"
                    },
                    "trigger": {
                        "type": "string",
                        "enum": ["manual", "auto_threshold", "pre_compact", "session_end"],
                        "default": "manual"
                    }
                }
            }
        ),

        types.Tool(
            name="session_load",
            description=(
                "Load the handoff from a previous session. "
                "Call at the START of a new session to get context from the last one. "
                "Returns the handoff brief (~250 tokens) — exactly what the new session needs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {
                        "type": "string",
                        "description": "Absolute path to project root"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to load (default: latest)",
                        "default": "latest"
                    },
                    "load_level": {
                        "type": "string",
                        "enum": ["handoff", "full", "dead_ends", "environment", "diagram"],
                        "description": "How much to load. 'handoff' = minimal (~250t). 'full' = everything.",
                        "default": "handoff"
                    }
                },
                "required": ["project_root"]
            }
        ),

        types.Tool(
            name="session_update",
            description=(
                "Add information to the current session. "
                "Use this throughout the session to record: "
                "decisions made, dead ends found, assumptions made, tasks completed/added, "
                "danger zones discovered, errors seen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "decision", "dead_end", "assumption",
                            "task_complete", "task_add", "task_block",
                            "danger_zone", "error_resolved", "environment"
                        ]
                    },
                    "content": {
                        "type": "string",
                        "description": "Main content"
                    },
                    "context_pct": {
                        "type": "number",
                        "description": "Current context% (for decision quality flagging)",
                        "default": 0
                    },
                    # For dead_ends
                    "why_failed": {
                        "type": "string",
                        "description": "Why it failed [for dead_end type]"
                    },
                    "use_instead": {
                        "type": "string",
                        "description": "What to use instead [for dead_end type]"
                    },
                    # For assumptions
                    "risk_level": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                        "default": "MEDIUM"
                    },
                    "how_to_verify": {
                        "type": "string",
                        "description": "How to verify this assumption [for assumption type]"
                    },
                    # For decisions
                    "reasoning": {
                        "type": "string",
                        "description": "Why this decision was made [for decision type]"
                    },
                    # For environment
                    "start_commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Commands to start the project"
                    },
                    "test_command": {
                        "type": "string",
                        "description": "How to run tests"
                    }
                },
                "required": ["type", "content"]
            }
        ),

        types.Tool(
            name="session_tree",
            description=(
                "Show the full session tree for this project. "
                "Like 'git log --graph' but for CogSession work history. "
                "Shows all sessions, their relationships, and their status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {
                        "type": "string",
                        "description": "Absolute path to project root"
                    }
                },
                "required": ["project_root"]
            }
        ),

        types.Tool(
            name="session_search",
            description=(
                "Search across ALL sessions for a query. "
                "Useful for: 'when did we decide X?', 'what errors have we seen?', "
                "'which session touched file Y?', 'what dead ends are there?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "query":        {"type": "string"},
                    "type_filter": {
                        "type": "string",
                        "enum": ["decision", "dead_end", "error", "file_edit", "all"],
                        "default": "all"
                    }
                },
                "required": ["project_root", "query"]
            }
        ),

        types.Tool(
            name="session_status",
            description=(
                "Get the current session's status: what's been recorded, "
                "how many dead ends, task progress, context warning level."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "context_pct": {
                        "type": "number",
                        "description": "Current context% for threshold warning"
                    }
                }
            }
        ),

        types.Tool(
            name="session_diagram",
            description=(
                "Show or regenerate the architecture diagram (Mermaid format). "
                "Auto-scans the project files to build a dependency graph. "
                "Pass to a Mermaid renderer to visualize."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "session_id": {
                        "type": "string",
                        "description": "Session to get diagram from (default: latest)",
                        "default": "latest"
                    },
                    "regenerate": {
                        "type": "boolean",
                        "description": "Force regenerate from current files",
                        "default": False
                    }
                },
                "required": ["project_root"]
            }
        ),
    ]


# ══════════════════════════════════════════════════════════════════════
# TOOL HANDLERS
# ══════════════════════════════════════════════════════════════════════

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    global _active_session, _project_root

    try:
        if   name == "session_init":     return await _init(arguments)
        elif name == "session_checkpoint": return await _checkpoint(arguments)
        elif name == "session_load":     return await _load(arguments)
        elif name == "session_update":   return await _update(arguments)
        elif name == "session_tree":     return await _tree(arguments)
        elif name == "session_search":   return await _search(arguments)
        elif name == "session_status":   return await _status(arguments)
        elif name == "session_diagram":  return await _diagram(arguments)
        else:
            return _txt(f"[CogSession] Unknown tool: {name}")
    except Exception as e:
        return _txt(f"[CogSession ERROR] {name}: {e}")


# ── session_init ───────────────────────────────────────────────────────

async def _init(args: dict) -> list[types.TextContent]:
    global _active_session, _project_root

    root    = Path(args["project_root"])
    focus   = args.get("focus", "")
    parent  = args.get("parent_session_id")

    _project_root = root
    config = load_project_config(root)

    if not config.get("enabled", True):
        return _txt("[CogSession] Disabled for this project (.cogsession.json enabled=false)")

    # Create new session
    session_id = new_id(focus)
    _active_session = Session(
        id           = session_id,
        project_root = str(root),
        focus        = focus,
        parent_id    = parent,
    )
    _active_session.ensure_dir()

    # Write initial manifest
    writer = SessionWriter(_active_session)
    writer.write_manifest()

    # Update index
    writer.update_index()

    # Log session start
    _active_session.append_log(LogEntry(
        type    = "session_start",
        content = f"Session started: {focus or 'general development'}",
    ))

    # Check if there's a previous session to inherit from
    inherit_note = ""
    if parent:
        loader = SessionLoader(root)
        parent_manifest = loader.load_manifest(parent)
        if parent_manifest:
            parent_one_liner = parent_manifest.get("one_liner", "")
            inherit_note = f"\nInheriting from: {parent} — {parent_one_liner}"

    return _txt(
        f"[CogSession] ✓ Session started\n"
        f"  ID:      {session_id}\n"
        f"  Focus:   {focus or 'general'}\n"
        f"  Parent:  {parent or 'none (root session)'}\n"
        f"  Folder:  {_active_session.session_dir()}"
        f"{inherit_note}\n\n"
        f"Session is now tracking. Use session_update() to record:\n"
        f"  • decisions made\n"
        f"  • dead ends found\n"
        f"  • assumptions made\n"
        f"  • tasks completed\n"
        f"\nCall session_checkpoint() at 70-80% context."
    )


# ── session_checkpoint ─────────────────────────────────────────────────

async def _checkpoint(args: dict) -> list[types.TextContent]:
    global _active_session

    if not _active_session:
        return _txt("[CogSession] No active session. Call session_init() first.")

    ctx_pct   = args.get("context_pct", 0.0)
    one_liner = args.get("one_liner", "")
    trigger   = args.get("trigger", "manual")

    if one_liner:
        _active_session.one_liner = one_liner

    _active_session.checkpoint_trigger = trigger
    _active_session.closed_at = now_iso()
    _active_session.status    = "completed"

    # Log checkpoint event
    _active_session.append_log(LogEntry(
        type        = "checkpoint",
        content     = f"Checkpoint triggered: {trigger} at {ctx_pct:.0f}%",
        context_pct = ctx_pct,
    ))

    # Write all artifacts
    writer = SessionWriter(_active_session)
    writer.write_all(context_pct=ctx_pct)

    # Auto-update CLAUDE.md with handoff
    if _project_root:
        handoff_path = _active_session.session_dir() / "handoff.md"
        claude_md    = _project_root / "CLAUDE.md"
        if handoff_path.exists():
            handoff_content = handoff_path.read_text()
            # Prepend to CLAUDE.md (keep existing content below)
            existing = claude_md.read_text() if claude_md.exists() else ""
            # Remove old cogsession block if present
            if "<!-- COGSESSION:HANDOFF -->" in existing:
                start = existing.find("<!-- COGSESSION:HANDOFF -->")
                end   = existing.find("<!-- /COGSESSION:HANDOFF -->")
                if end > start:
                    existing = existing[:start] + existing[end + 27:]

            new_content = (
                f"<!-- COGSESSION:HANDOFF -->\n"
                f"{handoff_content}\n"
                f"<!-- /COGSESSION:HANDOFF -->\n\n"
                f"{existing.strip()}\n"
            )
            claude_md.write_text(new_content)
            handoff_injected = "✓ Handoff written to CLAUDE.md (loads automatically next session)"
        else:
            handoff_injected = ""

    # Quality warning for high-context decisions
    flagged = [d for d in _active_session.decisions if d.context_pct >= 75]
    flag_note = ""
    if flagged:
        flag_note = (
            f"\n  ⚠ {len(flagged)} decisions made at >75% context — review in decisions.json"
        )

    return _txt(
        f"[CogSession] ✓ Checkpoint complete\n"
        f"  Session:   {_active_session.id}\n"
        f"  Context:   {ctx_pct:.0f}%\n"
        f"  Trigger:   {trigger}\n"
        f"  Folder:    {_active_session.session_dir()}\n"
        f"  Dead ends: {len(_active_session.dead_ends)}\n"
        f"  Decisions: {len(_active_session.decisions)}\n"
        f"  Tasks done:{len(_active_session.tasks.completed)}\n"
        f"  Tasks left:{len(_active_session.tasks.remaining)}\n"
        f"  {handoff_injected}"
        f"{flag_note}\n\n"
        f"Next session: run session_load(project_root=...) first."
    )


# ── session_load ───────────────────────────────────────────────────────

async def _load(args: dict) -> list[types.TextContent]:
    root       = Path(args["project_root"])
    session_id = args.get("session_id", "latest")
    level      = args.get("load_level", "handoff")

    loader = SessionLoader(root)

    if session_id == "latest":
        session_id = loader.get_latest_session_id()
        if not session_id:
            return _txt(
                "[CogSession] No sessions found.\n"
                "This appears to be a fresh start.\n"
                "Run session_init() to begin tracking."
            )

    if level == "handoff":
        content = loader.load_handoff(session_id)
        return _txt(f"[CogSession] Handoff from {session_id}:\n\n{content}")

    elif level == "dead_ends":
        content = loader.load_dead_ends(session_id)
        return _txt(f"[CogSession] Dead ends from {session_id}:\n\n{content}")

    elif level == "environment":
        env = loader.load_environment(session_id)
        return _txt(
            f"[CogSession] Environment from {session_id}:\n\n"
            f"```json\n{json.dumps(env, indent=2)}\n```"
        )

    elif level == "diagram":
        diagram = loader.load_mermaid(session_id)
        return _txt(
            f"[CogSession] Architecture diagram from {session_id}:\n\n"
            f"```mermaid\n{diagram}\n```"
        )

    elif level == "full":
        handoff  = loader.load_handoff(session_id)
        tasks    = loader.load_tasks(session_id)
        dead_ends = loader.load_dead_ends(session_id)
        env      = loader.load_environment(session_id)

        content = (
            f"[CogSession] Full context from {session_id}:\n\n"
            f"## Handoff\n{handoff}\n\n"
            f"## Tasks\n```json\n{json.dumps(tasks, indent=2)}\n```\n\n"
            f"## Environment\n```json\n{json.dumps(env, indent=2)}\n```\n\n"
            f"## Dead Ends\n{dead_ends}"
        )
        return _txt(content)

    return _txt(f"[CogSession] Unknown load_level: {level}")


# ── session_update ─────────────────────────────────────────────────────

async def _update(args: dict) -> list[types.TextContent]:
    global _active_session

    if not _active_session:
        return _txt("[CogSession] No active session. Call session_init() first.")

    update_type = args["type"]
    content     = args["content"]
    ctx_pct     = float(args.get("context_pct", 0.0))

    if update_type == "decision":
        decision = Decision(
            timestamp   = now_iso(),
            decision    = content,
            reasoning   = args.get("reasoning", ""),
            context_pct = ctx_pct,
            reversible  = args.get("reversible", True),
        )
        _active_session.decisions.append(decision)
        flag = " ⚠ FLAGGED (high ctx)" if decision.quality_flag == "degraded_context" else ""
        _active_session.append_log(LogEntry(
            type="decision", content=content, context_pct=ctx_pct
        ))
        return _txt(f"[CogSession] Decision recorded{flag}: {content[:60]}")

    elif update_type == "dead_end":
        dead_end = DeadEnd(
            timestamp   = now_iso(),
            tried       = content,
            why_failed  = args.get("why_failed", "Not specified"),
            use_instead = args.get("use_instead", ""),
            context_pct = ctx_pct,
        )
        _active_session.dead_ends.append(dead_end)
        _active_session.append_log(LogEntry(
            type="dead_end", content=f"DEAD END: {content}", context_pct=ctx_pct
        ))
        return _txt(f"[CogSession] Dead end recorded: {content[:60]}")

    elif update_type == "assumption":
        assumption = Assumption(
            timestamp      = now_iso(),
            assumption     = content,
            risk_level     = args.get("risk_level", "MEDIUM"),
            how_to_verify  = args.get("how_to_verify", "Manual inspection"),
        )
        _active_session.assumptions.append(assumption)
        _active_session.append_log(LogEntry(
            type="assumption",
            content=f"[{assumption.risk_level}] {content}",
            context_pct=ctx_pct,
        ))
        return _txt(f"[CogSession] Assumption recorded [{assumption.risk_level}]: {content[:60]}")

    elif update_type == "task_complete":
        _active_session.tasks.completed.append(content)
        if content in _active_session.tasks.remaining:
            _active_session.tasks.remaining.remove(content)
        _active_session.append_log(LogEntry(type="task_complete", content=content))
        return _txt(f"[CogSession] Task completed ✓: {content[:60]}")

    elif update_type == "task_add":
        _active_session.tasks.remaining.append(content)
        priority = args.get("priority", False)
        if priority:
            _active_session.tasks.next_priority = content
        return _txt(f"[CogSession] Task added: {content[:60]}")

    elif update_type == "task_block":
        _active_session.tasks.blocked.append(content)
        if content in _active_session.tasks.remaining:
            _active_session.tasks.remaining.remove(content)
        return _txt(f"[CogSession] Task blocked: {content[:60]}")

    elif update_type == "danger_zone":
        _active_session.danger_zones.append(content)
        _active_session.append_log(LogEntry(type="danger_zone", content=content))
        return _txt(f"[CogSession] Danger zone flagged: {content[:60]}")

    elif update_type == "environment":
        env = _active_session.environment
        if "start_commands" in args:
            env.start_commands = args["start_commands"]
        if "test_command" in args:
            env.test_command = args["test_command"]
        if "ports" in args:
            env.ports = args["ports"]
        if "env_vars_needed" in args:
            env.env_vars_needed = args["env_vars_needed"]
        return _txt(f"[CogSession] Environment updated")

    return _txt(f"[CogSession] Unknown update type: {update_type}")


# ── session_tree ───────────────────────────────────────────────────────

async def _tree(args: dict) -> list[types.TextContent]:
    root   = Path(args["project_root"])
    loader = SessionLoader(root)
    tree   = loader.render_tree()
    return _txt(f"[CogSession] Session Tree\n\n{tree}")


# ── session_search ─────────────────────────────────────────────────────

async def _search(args: dict) -> list[types.TextContent]:
    root    = Path(args["project_root"])
    query   = args["query"]
    tf      = args.get("type_filter", "all")

    loader  = SessionLoader(root)
    results = loader.search_all_sessions(
        query,
        session_type=None if tf == "all" else tf
    )

    if not results:
        return _txt(f"[CogSession] No results for '{query}'")

    lines = [f"[CogSession] Search: '{query}' — {len(results)} results\n"]
    for r in results:
        ctx = f" ({r['context_pct']:.0f}%ctx)" if r.get("context_pct") else ""
        lines.append(
            f"• [{r['session_id'][:24]}] [{r['type']}]{ctx}\n"
            f"  {r['content']}"
        )

    return _txt("\n".join(lines))


# ── session_status ─────────────────────────────────────────────────────

async def _status(args: dict) -> list[types.TextContent]:
    global _active_session
    ctx_pct = float(args.get("context_pct", 0.0))

    if not _active_session:
        return _txt(
            "[CogSession] No active session.\n"
            "Run session_init(project_root=...) to start."
        )

    s = _active_session

    # Context warning
    if ctx_pct >= 80:
        ctx_warn = "🔴 CHECKPOINT NOW — at threshold!"
    elif ctx_pct >= 75:
        ctx_warn = "🟡 Checkpoint recommended soon"
    elif ctx_pct >= 65:
        ctx_warn = "🟡 Context getting full"
    else:
        ctx_warn = "🟢 OK"

    # Flagged decisions
    flagged = [d for d in s.decisions if d.context_pct >= 75]
    flag_note = f"\n  ⚠ {len(flagged)} decisions at high context — may need review" if flagged else ""

    return _txt(
        f"[CogSession] Status: {s.id}\n"
        f"  Focus:      {s.focus or 'general'}\n"
        f"  Context:    {ctx_pct:.0f}% — {ctx_warn}\n"
        f"  Tasks done: {len(s.tasks.completed)}\n"
        f"  Tasks left: {len(s.tasks.remaining)}\n"
        f"  Tasks blocked: {len(s.tasks.blocked)}\n"
        f"  Decisions:  {len(s.decisions)}{flag_note}\n"
        f"  Dead ends:  {len(s.dead_ends)}\n"
        f"  Assumptions:{len(s.assumptions)}\n"
        f"  Danger zones:{len(s.danger_zones)}\n"
        f"  Files read: {len(s.files_read)}\n"
        f"  Files edited:{len(s.files_edited)}\n"
        f"  Tool calls: {s.tool_call_count}\n"
        f"\nSession folder: {s.session_dir()}"
    )


# ── session_diagram ────────────────────────────────────────────────────

async def _diagram(args: dict) -> list[types.TextContent]:
    root       = Path(args["project_root"])
    session_id = args.get("session_id", "latest")
    regenerate = args.get("regenerate", False)

    loader = SessionLoader(root)

    if session_id == "latest":
        session_id = loader.get_latest_session_id()
        if not session_id:
            return _txt("[CogSession] No sessions found.")

    if regenerate and _active_session:
        writer = SessionWriter(_active_session)
        writer.write_mermaid_diagram()

    diagram = loader.load_mermaid(session_id)
    return _txt(
        f"[CogSession] Architecture Diagram — {session_id}\n\n"
        f"```mermaid\n{diagram}\n```\n\n"
        f"Copy the mermaid block into https://mermaid.live to visualize."
    )


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _txt(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=msg)]


def _log(msg: str) -> None:
    """Diagnostics to stderr — stdout carries the JSON-RPC stream."""
    print(f"[CogSession] {msg}", file=sys.stderr, flush=True)


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

async def main():
    # stdout is the JSON-RPC channel for a stdio server — diagnostics go to stderr.
    _log("MCP server ready - waiting for client connection")

    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
