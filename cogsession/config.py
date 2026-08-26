"""
cogsession/config.py

All settings for CogSession.
Project-level config lives in .cogsession.json at project root.
Global config lives in ~/.cogsession/config.json
"""

import os
import json
import sys
from pathlib import Path


# ── Global defaults ───────────────────────────────────────────────────
SESSIONS_DIR_NAME = ".cogsessions"
CONFIG_FILE_NAME  = ".cogsession.json"

# Where the handoff is delivered so the next session auto-loads it. Deliberately
# the .local variant: it is auto-loaded like CLAUDE.md but is not committed, so
# session state never enters a shared source file or dirties the working tree.
HANDOFF_TARGET_NAME = "CLAUDE.local.md"


DEFAULT_CONFIG = {
    # Master switch — set false to completely disable
    "enabled": True,

    # Individual feature toggles — user can pick what they want
    "features": {
        "dead_ends":        True,   # Track approaches that failed
        "assumptions":      True,   # Track what agent assumed but didn't verify
        "token_map":        True,   # Tag decisions with context% when made
        "error_graveyard":  True,   # Log errors + how they were resolved
        "environment":      True,   # Snapshot run commands, ports, env vars
        "file_cache":       True,   # Cache file reads to avoid duplicates
        "danger_zones":     True,   # Flag files/areas that need care
        "auto_diagram":     True,   # Generate Mermaid architecture diagram
        "auto_handoff":     True,   # Auto-write handoff to CLAUDE.local.md (never a tracked file)
        "session_search":   True,   # SQLite FTS across all sessions
        "claim_checks":     True,   # Re-run the proof behind a recorded claim
    },

    # Context % thresholds
    "thresholds": {
        "warn_at":       65,    # First gentle warning
        "alert_at":      75,    # Strong warning + suggestion
        "checkpoint_at": 80,    # Auto-checkpoint fires
    },

    # How often to auto-save running state (every N tool calls)
    "autosave_every_n_tools": 10,

    # Files/paths to exclude from tracking
    "exclude": [
        "*.log", ".env", ".env.*",
        "node_modules/", "__pycache__/",
        ".git/", "*.pyc", "dist/", "build/"
    ],

    # Where session folder lives relative to project root
    "session_dir": ".cogsessions",
}


def load_project_config(project_root: Path) -> dict:
    """Load .cogsession.json from project root, merged with defaults."""
    config = DEFAULT_CONFIG.copy()
    config_file = project_root / CONFIG_FILE_NAME

    if config_file.exists():
        try:
            user_config = json.loads(config_file.read_text())
            # Deep merge features
            if "features" in user_config:
                config["features"].update(user_config["features"])
            if "thresholds" in user_config:
                config["thresholds"].update(user_config["thresholds"])
            # Top-level overrides
            for k, v in user_config.items():
                if k not in ("features", "thresholds"):
                    config[k] = v
        except Exception as e:
            print(f"[CogSession] Config parse error: {e}, using defaults", file=sys.stderr, flush=True)

    return config


def is_enabled(project_root: Path) -> bool:
    """Quick check — is cogsession enabled for this project?"""
    # Global disable via env var
    if os.getenv("COGSESSION_DISABLED"):
        return False

    config = load_project_config(project_root)
    return config.get("enabled", True)


def feature_enabled(project_root: Path, feature: str) -> bool:
    """Is a specific feature enabled?"""
    config = load_project_config(project_root)
    return config.get("features", {}).get(feature, True)
