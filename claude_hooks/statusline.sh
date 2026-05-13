#!/usr/bin/env bash
# ~/.claude/cogsession/statusline.sh
#
# CogSession StatusLine hook.
# Reads context% from Claude Code JSON and shows session status.
# Triggers checkpoint warnings at configured thresholds.
#
# Install: add to ~/.claude/settings.json:
#   "statusLine": {
#     "type": "command",
#     "command": "bash ~/.claude/cogsession/statusline.sh"
#   }

set -euo pipefail

# Read JSON from stdin (Claude Code pipes this on every update)
input="$(cat)"

# ── Extract fields ───────────────────────────────────────────────────
MODEL=$(echo "$input" | jq -r '.model.display_name // "Claude"')
CTX=$(echo "$input"   | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
COST=$(echo "$input"  | jq -r '.cost.total_cost_usd // 0')
ADDED=$(echo "$input" | jq -r '.cost.total_lines_added // 0')
SESSION_ID=$(echo "$input" | jq -r '.session_id // ""')
CWD=$(echo "$input"   | jq -r '.workspace.current_dir // ""')
RATE_5H=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // 0' | cut -d. -f1)

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[91m'
YEL='\033[93m'
GRN='\033[92m'
CYN='\033[96m'
DIM='\033[2m'
RST='\033[0m'

# ── Context color ─────────────────────────────────────────────────────
if   [ "$CTX" -ge 80 ]; then CTX_COLOR="$RED"
elif [ "$CTX" -ge 65 ]; then CTX_COLOR="$YEL"
else                          CTX_COLOR="$GRN"
fi

# ── Context progress bar ──────────────────────────────────────────────
BAR_WIDTH=12
FILLED=$(( CTX * BAR_WIDTH / 100 ))
EMPTY=$(( BAR_WIDTH - FILLED ))
BAR=""
[ "$FILLED" -gt 0 ] && BAR=$(printf "%${FILLED}s" | tr ' ' '▓')
[ "$EMPTY"  -gt 0 ] && BAR="${BAR}$(printf "%${EMPTY}s" | tr ' ' '░')"

# ── Git info ──────────────────────────────────────────────────────────
GIT_INFO=""
if git -C "$CWD" rev-parse --git-dir > /dev/null 2>&1; then
    BRANCH=$(git -C "$CWD" branch --show-current 2>/dev/null || echo "")
    [ -n "$BRANCH" ] && GIT_INFO=" ${DIM}git:${RST}${CYN}${BRANCH}${RST}"
fi

# ── CogSession state ──────────────────────────────────────────────────
SESSION_DIR="${CWD}/.cogsessions"
ACTIVE_SESSION=""
if [ -f "${SESSION_DIR}/index.json" ]; then
    # Find active session
    ACTIVE=$(jq -r '.sessions | to_entries[] | select(.value.status=="active") | .key' \
             "${SESSION_DIR}/index.json" 2>/dev/null | head -1)
    if [ -n "$ACTIVE" ]; then
        SHORT_ID="${ACTIVE:0:24}"
        ACTIVE_SESSION=" ${DIM}[${SHORT_ID}]${RST}"
    fi
fi

# ── Checkpoint warning ────────────────────────────────────────────────
WARN=""
if   [ "$CTX" -ge 80 ]; then
    WARN=" ${RED}⚠ CHECKPOINT NOW (${CTX}%)${RST}"
    # Trigger async checkpoint via MCP (non-blocking)
    if [ -n "$SESSION_ID" ]; then
        COGSESSION_TRIGGER_FILE="/tmp/cogsession_checkpoint_${SESSION_ID}"
        if [ ! -f "$COGSESSION_TRIGGER_FILE" ]; then
            touch "$COGSESSION_TRIGGER_FILE"
            # Write trigger file — MCP server polls this
            echo "$CTX" > "$COGSESSION_TRIGGER_FILE"
        fi
    fi
elif [ "$CTX" -ge 75 ]; then
    WARN=" ${YEL}⚡ Checkpoint soon (${CTX}%)${RST}"
elif [ "$CTX" -ge 65 ]; then
    WARN=" ${YEL}· ${CTX}% ctx${RST}"
fi

# ── Format cost ──────────────────────────────────────────────────────
COST_FMT=$(printf "%.2f" "$COST" 2>/dev/null || echo "0.00")

# ── Output single status line ─────────────────────────────────────────
echo -e "${DIM}${MODEL}${RST} ${CTX_COLOR}${BAR}${RST}${WARN}${GIT_INFO}${ACTIVE_SESSION} ${DIM}\$${COST_FMT} +${ADDED}L${RST}"
