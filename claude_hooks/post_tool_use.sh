#!/usr/bin/env bash
# claude_hooks/post_tool_use.sh
#
# Fires AFTER every tool Claude Code uses.
# Receives JSON with the tool result on stdin.
#
# What we do:
#   1. Detect errors → log to error_graveyard
#   2. Detect test results → update environment snapshot
#   3. Detect successful resolutions → mark errors as resolved

set -euo pipefail

input="$(cat)"

TOOL=$(echo "$input"        | jq -r '.tool_name // ""')
CWD=$(echo "$input"         | jq -r '.cwd // ""')
SESSION_ID=$(echo "$input"  | jq -r '.session_id // "unknown"')
EXIT_CODE=$(echo "$input"   | jq -r '.tool_response.exit_code // 0')
OUTPUT=$(echo "$input"      | jq -r '.tool_response.output // "" | .[0:500]')

SESSION_DIR="${CWD}/.cogsessions"
if [ ! -d "$SESSION_DIR" ]; then exit 0; fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Find active session
ACTIVE_ID=""
if [ -f "${SESSION_DIR}/index.json" ]; then
    ACTIVE_ID=$(jq -r '.sessions | to_entries[] | select(.value.status=="active") | .key' \
                "${SESSION_DIR}/index.json" 2>/dev/null | head -1)
fi

[ -z "$ACTIVE_ID" ] && exit 0

ACTIVE_DIR="${SESSION_DIR}/${ACTIVE_ID}"
ACTIVE_LOG="${ACTIVE_DIR}/session_log.jsonl"
GRAVEYARD="${ACTIVE_DIR}/error_graveyard.json"

mkdir -p "$ACTIVE_DIR"

# ── Error detection ────────────────────────────────────────────────────

if [ "$EXIT_CODE" != "0" ] && [ "$TOOL" = "Bash" ]; then
    # Extract error type from output
    ERROR_TYPE="unknown"
    if echo "$OUTPUT" | grep -q "ModuleNotFoundError\|ImportError";  then ERROR_TYPE="import_error"
    elif echo "$OUTPUT" | grep -q "SyntaxError";                      then ERROR_TYPE="syntax_error"
    elif echo "$OUTPUT" | grep -q "ConnectionRefused\|ECONNREFUSED";  then ERROR_TYPE="connection_error"
    elif echo "$OUTPUT" | grep -q "PermissionError\|Permission denied"; then ERROR_TYPE="permission_error"
    elif echo "$OUTPUT" | grep -q "FileNotFoundError\|No such file";  then ERROR_TYPE="file_not_found"
    elif echo "$OUTPUT" | grep -q "FAILED\|ERROR\|error:";           then ERROR_TYPE="general_error"
    fi

    # Escape for JSON
    OUTPUT_ESCAPED=$(echo "$OUTPUT" | jq -Rs .)
    CMD=$(echo "$input" | jq -r '.tool_input.command // ""' | head -c 100)

    # Log to session_log
    echo "{\"ts\":\"${TS}\",\"type\":\"error\",\"content\":\"${CMD}\",\"error_type\":\"${ERROR_TYPE}\",\"exit_code\":${EXIT_CODE}}" \
        >> "$ACTIVE_LOG" 2>/dev/null || true

    # Append to error_graveyard.json
    if [ -f "$GRAVEYARD" ]; then
        # Add to existing array
        EXISTING=$(cat "$GRAVEYARD")
        NEW_ENTRY="{\"ts\":\"${TS}\",\"command\":\"${CMD}\",\"error_type\":\"${ERROR_TYPE}\",\"output\":${OUTPUT_ESCAPED},\"resolved\":false}"
        echo "$EXISTING" | jq ". + [${NEW_ENTRY}]" > "$GRAVEYARD" 2>/dev/null || true
    else
        # Create new file
        CMD_ESCAPED=$(echo "$CMD" | jq -Rs .)
        echo "[{\"ts\":\"${TS}\",\"command\":${CMD_ESCAPED},\"error_type\":\"${ERROR_TYPE}\",\"output\":${OUTPUT_ESCAPED},\"resolved\":false}]" \
            > "$GRAVEYARD" 2>/dev/null || true
    fi
fi

# ── Test result detection ─────────────────────────────────────────────

if [ "$TOOL" = "Bash" ] && echo "$input" | jq -r '.tool_input.command' | grep -qE "pytest|npm test|jest|go test"; then
    ENV_FILE="${ACTIVE_DIR}/environment.json"

    # Parse pytest output for pass/fail counts
    if echo "$OUTPUT" | grep -q "passed\|failed\|error"; then
        PASSING=$(echo "$OUTPUT" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" | head -1 || echo "0")
        FAILING=$(echo "$OUTPUT" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" | head -1 || echo "0")

        if [ -f "$ENV_FILE" ]; then
            jq ".test_passing = ${PASSING:-0} | .test_failing = ${FAILING:-0}" \
               "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE" 2>/dev/null || true
        fi

        LOG_CONTENT="Tests: ${PASSING:-0} passing, ${FAILING:-0} failing"
        echo "{\"ts\":\"${TS}\",\"type\":\"test_result\",\"content\":\"${LOG_CONTENT}\",\"passing\":${PASSING:-0},\"failing\":${FAILING:-0}}" \
            >> "$ACTIVE_LOG" 2>/dev/null || true
    fi
fi

exit 0
