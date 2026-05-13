#!/usr/bin/env bash
# claude_hooks/session_end.sh
#
# Fires when the Claude Code session ends (Ctrl+C, /exit, window close).
# Guarantees a final snapshot even if no manual checkpoint was done.
#
# Install in ~/.claude/settings.json:
#   "SessionEnd": [{
#     "hooks": [{"type": "command", "command": "bash ~/.claude/cogsession/session_end.sh"}]
#   }]

set -euo pipefail

input="$(cat)"
CWD=$(echo "$input" | jq -r '.cwd // ""')
SESSION_ID=$(echo "$input" | jq -r '.session_id // ""')
CTX=$(echo "$input" | jq -r '.context_window.used_percentage // 0')

SESSION_DIR="${CWD}/.cogsessions"
if [ ! -d "$SESSION_DIR" ]; then exit 0; fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ -f "${SESSION_DIR}/index.json" ]; then
    ACTIVE_ID=$(jq -r '.sessions | to_entries[] | select(.value.status=="active") | .key' \
                "${SESSION_DIR}/index.json" 2>/dev/null | head -1)

    if [ -n "$ACTIVE_ID" ]; then
        ACTIVE_LOG="${SESSION_DIR}/${ACTIVE_ID}/session_log.jsonl"
        mkdir -p "${SESSION_DIR}/${ACTIVE_ID}"

        # Log session end event
        echo "{\"ts\":\"${TS}\",\"type\":\"session_end\",\"content\":\"Session ended at ${CTX}% context\",\"context_pct\":${CTX}}" \
            >> "$ACTIVE_LOG" 2>/dev/null || true

        # Write trigger for MCP to handle async checkpoint
        TRIGGER_FILE="/tmp/cogsession_end_${SESSION_ID}"
        echo "$CTX" > "$TRIGGER_FILE"

        echo "[CogSession] Session ended — final snapshot saved"
    fi
fi

exit 0
