#!/usr/bin/env bash
# Install CogSession — one command setup
# Usage: bash scripts/install.sh

set -e
COGSESSION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"
HOOK_DIR="$CLAUDE_DIR/cogsession"

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║   CogSession Installer           ║"
echo "  ╚══════════════════════════════════╝"
echo ""

# ── 1. Install Python package ─────────────────────────────────────────
echo "  [1/5] Installing Python package..."
cd "$COGSESSION_DIR"
uv sync
echo "  ✓ Package installed"

# ── 2. Copy hook scripts to ~/.claude/cogsession/ ────────────────────
echo "  [2/5] Installing hook scripts..."
mkdir -p "$HOOK_DIR"
cp "$COGSESSION_DIR/claude_hooks/"*.sh "$HOOK_DIR/"
chmod +x "$HOOK_DIR/"*.sh
echo "  ✓ Hooks installed → $HOOK_DIR"

# ── 3. Add MCP server to Claude Code ─────────────────────────────────
echo "  [3/5] Registering MCP server..."
if command -v claude &> /dev/null; then
    claude mcp add cogsession -- uv --directory "$COGSESSION_DIR" run cogsession 2>/dev/null || true
    echo "  ✓ MCP server registered"
else
    echo "  ⚠ claude CLI not found — manually add MCP:"
    echo "    claude mcp add cogsession -- uv --directory $COGSESSION_DIR run cogsession"
fi

# ── 4. Merge settings.json ────────────────────────────────────────────
echo "  [4/5] Configuring ~/.claude/settings.json..."
SETTINGS="$CLAUDE_DIR/settings.json"
TEMPLATE="$COGSESSION_DIR/claude_settings_template.json"

if [ -f "$SETTINGS" ]; then
    echo "  ⚠ settings.json already exists."
    echo "  Manually merge these hooks from: $TEMPLATE"
    echo "  Key sections: statusLine, hooks.PreToolUse, hooks.PostToolUse,"
    echo "                hooks.PreCompact, hooks.SessionEnd"
else
    mkdir -p "$CLAUDE_DIR"
    cp "$TEMPLATE" "$SETTINGS"
    # Remove the comment field
    jq 'del(._comment)' "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
    echo "  ✓ settings.json created"
fi

# ── 5. Create default project config ─────────────────────────────────
echo "  [5/5] Done!"
echo ""
echo "  ══════════════════════════════════════"
echo "  CogSession is installed!"
echo ""
echo "  Usage in Claude Code:"
echo "    session_init(project_root='/your/project')"
echo "    session_update(type='task_add', content='build auth module')"
echo "    session_checkpoint(context_pct=75, one_liner='Built JWT auth')"
echo ""
echo "  New session next time:"
echo "    session_load(project_root='/your/project')"
echo "    session_tree(project_root='/your/project')"
echo ""
echo "  To disable for a project:"
echo "    echo '{\"enabled\": false}' > .cogsession.json"
echo "  ══════════════════════════════════════"
echo ""
