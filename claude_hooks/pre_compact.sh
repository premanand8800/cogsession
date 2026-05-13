#!/usr/bin/env bash
# claude_hooks/pre_compact.sh
#
# Fires BEFORE Claude Code compresses the context window.
# This is CRITICAL — compaction loses detail.
# We save everything NOW while we still have it.
#
# Install in ~/.claude/settings.json:
#   "PreCompact": [{
#     "hooks": [{"type": "command", "command": "bash ~/.claude/cogsession/pre_compact.sh"}]
#   }]

set -euo pipefail

input="$(cat)"
CWD=$(echo "$input" | jq -r '.cwd // ""')
SESSION_ID=$(echo "$input" | jq -r '.session_id // ""')
CTX=$(echo "$input" | jq -r '.context_window.used_percentage // 0')

SESSION_DIR="${CWD}/.cogsessions"
if [ ! -d "$SESSION_DIR" ]; then exit 0; fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Find active session
if [ -f "${SESSION_DIR}/index.json" ]; then
    ACTIVE_ID=$(jq -r '.sessions | to_entries[] | select(.value.status=="active") | .key' \
                "${SESSION_DIR}/index.json" 2>/dev/null | head -1)

    if [ -n "$ACTIVE_ID" ]; then
        ACTIVE_LOG="${SESSION_DIR}/${ACTIVE_ID}/session_log.jsonl"

        # Log the pre-compact event
        echo "{\"ts\":\"${TS}\",\"type\":\"pre_compact\",\"content\":\"Context at ${CTX}% — auto-compaction about to fire\",\"context_pct\":${CTX}}" \
            >> "$ACTIVE_LOG" 2>/dev/null || true

        # Call MCP tool to trigger checkpoint
        # (MCP server handles the actual file writing)
        TRIGGER_FILE="/tmp/cogsession_precompact_${SESSION_ID}"
        echo "$CTX" > "$TRIGGER_FILE"

        echo "[CogSession] ⚡ Pre-compact checkpoint triggered at ${CTX}% context"
    fi
fi

exit 0
