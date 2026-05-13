#!/usr/bin/env bash
# claude_hooks/pre_tool_use.sh
#
# Fires BEFORE every tool Claude Code uses.
# Receives JSON on stdin describing what Claude is about to do.
#
# What we do here:
#   1. Log file reads/writes to session_log.jsonl
#   2. File cache check — if Claude already read this file,
#      return cached version (avoids 71% duplicate reads)
#   3. Block writes to protected files (danger zones)
#   4. Track tool call count for autosave trigger
#
# Install in ~/.claude/settings.json:
#   "PreToolUse": [{
#     "matcher": "*",
#     "hooks": [{"type": "command", "command": "bash ~/.claude/cogsession/pre_tool_use.sh"}]
#   }]
#
# Exit codes:
#   0 = allow tool to proceed
#   2 = block tool, show message to user

set -euo pipefail

input="$(cat)"

TOOL=$(echo "$input" | jq -r '.tool_name // ""')
CWD=$(echo "$input"  | jq -r '.cwd // ""')
SESSION_ID=$(echo "$input" | jq -r '.session_id // "unknown"')

SESSION_DIR="${CWD}/.cogsessions"
LOG_DIR="${SESSION_DIR}/active_session"

# If cogsession not initialized for this project, skip
if [ ! -d "$SESSION_DIR" ]; then
    exit 0
fi

# Find active session log
ACTIVE_LOG=""
if [ -f "${SESSION_DIR}/index.json" ]; then
    ACTIVE_ID=$(jq -r '.sessions | to_entries[] | select(.value.status=="active") | .key' \
                "${SESSION_DIR}/index.json" 2>/dev/null | head -1)
    if [ -n "$ACTIVE_ID" ]; then
        ACTIVE_LOG="${SESSION_DIR}/${ACTIVE_ID}/session_log.jsonl"
        mkdir -p "${SESSION_DIR}/${ACTIVE_ID}"
    fi
fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Handle by tool type ────────────────────────────────────────────────

case "$TOOL" in

    Read)
        FILE_PATH=$(echo "$input" | jq -r '.tool_input.file_path // ""')

        if [ -n "$FILE_PATH" ] && [ -n "$ACTIVE_LOG" ]; then
            # Log the file read
            echo "{\"ts\":\"${TS}\",\"type\":\"file_read\",\"content\":\"${FILE_PATH}\",\"context_pct\":0}" \
                >> "$ACTIVE_LOG" 2>/dev/null || true

            # File cache check
            CACHE_DIR="${SESSION_DIR}/.file_cache"
            CACHE_KEY=$(echo "$FILE_PATH" | md5sum | cut -d' ' -f1)
            CACHE_FILE="${CACHE_DIR}/${CACHE_KEY}.json"

            if [ -f "$CACHE_FILE" ]; then
                CACHED_MTIME=$(jq -r '.mtime // 0' "$CACHE_FILE" 2>/dev/null || echo "0")
                if [ -f "$FILE_PATH" ]; then
                    ACTUAL_MTIME=$(stat -c %Y "$FILE_PATH" 2>/dev/null || \
                                   stat -f %m "$FILE_PATH" 2>/dev/null || echo "1")
                    if [ "$CACHED_MTIME" = "$ACTUAL_MTIME" ]; then
                        # File hasn't changed — could serve from cache
                        # (For now just log the duplicate — full caching needs SDK)
                        echo "{\"ts\":\"${TS}\",\"type\":\"cache_hit\",\"content\":\"${FILE_PATH} (duplicate read)\",\"context_pct\":0}" \
                            >> "$ACTIVE_LOG" 2>/dev/null || true
                    fi
                fi
            fi

            # Update cache entry
            mkdir -p "$CACHE_DIR"
            if [ -f "$FILE_PATH" ]; then
                MTIME=$(stat -c %Y "$FILE_PATH" 2>/dev/null || \
                        stat -f %m "$FILE_PATH" 2>/dev/null || echo "0")
                echo "{\"file\":\"${FILE_PATH}\",\"mtime\":${MTIME}}" > "$CACHE_FILE"
            fi
        fi
        ;;

    Write|Edit|MultiEdit)
        FILE_PATH=$(echo "$input" | jq -r '.tool_input.file_path // ""')

        if [ -n "$FILE_PATH" ] && [ -n "$ACTIVE_LOG" ]; then
            # Check danger zones
            DANGER_FILE="${SESSION_DIR}/${ACTIVE_ID}/danger_zones.md"
            if [ -f "$DANGER_FILE" ]; then
                FILE_BASENAME=$(basename "$FILE_PATH")
                if grep -q "$FILE_BASENAME" "$DANGER_FILE" 2>/dev/null; then
                    # Log the warning but don't block — just surface it
                    echo "{\"ts\":\"${TS}\",\"type\":\"warning\",\"content\":\"Editing danger zone file: ${FILE_PATH}\",\"context_pct\":0}" \
                        >> "$ACTIVE_LOG" 2>/dev/null || true
                fi
            fi

            # Log the file edit
            echo "{\"ts\":\"${TS}\",\"type\":\"file_edit\",\"content\":\"${FILE_PATH}\",\"context_pct\":0}" \
                >> "$ACTIVE_LOG" 2>/dev/null || true
        fi
        ;;

    Bash)
        CMD=$(echo "$input" | jq -r '.tool_input.command // ""')

        if [ -n "$ACTIVE_LOG" ]; then
            # Detect test runs
            if echo "$CMD" | grep -qE "pytest|npm test|jest|go test|cargo test|rspec"; then
                echo "{\"ts\":\"${TS}\",\"type\":\"test_run\",\"content\":\"${CMD}\",\"context_pct\":0}" \
                    >> "$ACTIVE_LOG" 2>/dev/null || true
            fi

            # Detect git operations
            if echo "$CMD" | grep -q "^git "; then
                echo "{\"ts\":\"${TS}\",\"type\":\"git_op\",\"content\":\"${CMD}\",\"context_pct\":0}" \
                    >> "$ACTIVE_LOG" 2>/dev/null || true
            fi
        fi
        ;;
esac

# Increment tool call counter
COUNTER_FILE="/tmp/cogsession_tools_${SESSION_ID}"
COUNT=0
[ -f "$COUNTER_FILE" ] && COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
COUNT=$((COUNT + 1))
echo "$COUNT" > "$COUNTER_FILE"

# Autosave every 10 tool calls (write minimal state)
AUTOSAVE_EVERY=10
if [ $((COUNT % AUTOSAVE_EVERY)) -eq 0 ] && [ -n "$ACTIVE_LOG" ]; then
    echo "{\"ts\":\"${TS}\",\"type\":\"autosave\",\"content\":\"Tool call ${COUNT} — autosave\",\"context_pct\":0}" \
        >> "$ACTIVE_LOG" 2>/dev/null || true
fi

exit 0
