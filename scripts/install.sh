#!/usr/bin/env bash
# Install CogSession — one command setup
# Usage: bash scripts/install.sh

set -e
COGSESSION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║   CogSession Installer           ║"
echo "  ╚══════════════════════════════════╝"
echo ""

# ── 1. Install Python package ─────────────────────────────────────────
echo "  [1/4] Installing Python package..."
cd "$COGSESSION_DIR"
uv sync
echo "  ✓ Package installed"

# ── 2. Add MCP server to Claude Code ─────────────────────────────────
echo "  [2/4] Registering MCP server..."
if command -v claude &> /dev/null; then
    claude mcp add cogsession -- uv --directory "$COGSESSION_DIR" run cogsession 2>/dev/null || true
    echo "  ✓ MCP server registered"
else
    echo "  ⚠ claude CLI not found — manually add MCP:"
    echo "    claude mcp add cogsession -- uv --directory $COGSESSION_DIR run cogsession"
fi

# ── 3. Merge settings.json via Python ────────────────────────────────
echo "  [3/4] Configuring ~/.claude/settings.json using Python merger..."
uv run python -c "from pathlib import Path; from cogsession.installer import merge_settings; merge_settings(Path.home() / '.claude', Path('$COGSESSION_DIR'))"
echo "  ✓ settings.json updated and backed up"

# ── 4. Done ──────────────────────────────────────────────────────────
echo "  [4/4] Done!"
echo ""
echo "  ══════════════════════════════════════"
echo "  CogSession Intelligent Memory is installed!"
echo "  Automatic context measurement, hook injection, and distillation are active."
echo "  ══════════════════════════════════════"
echo ""
