"""
cogsession/session/models.py

Data structures for a CogSession.

A Session is everything about ONE Claude Code work period:
  - Who its parent is (tree structure)
  - What files were touched
  - What decisions were made
  - What failed (dead ends)
  - What was assumed but not verified
  - Current task list
  - Environment snapshot

Sessions connect in a tree:
  sess_001_discover
  ├── sess_002_auth       (parent: 001)
  │   └── sess_004_fix   (parent: 002)
  └── sess_003_payments   (parent: 001)
"""

import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(focus: str = "") -> str:
    """
    Generate a session ID like: sess_20260413_143022_auth
    Readable + sortable + descriptive.
    """
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug  = focus.lower().replace(" ", "_")[:20] if focus else "session"
    short = str(uuid.uuid4())[:4]
    return f"sess_{ts}_{slug}_{short}"


# ── Individual log entry types ─────────────────────────────────────────

@dataclass
class LogEntry:
    """One event in session_log.jsonl"""
    type:       str            # decision|dead_end|error|file_read|file_edit|test_run|warning|checkpoint
    content:    str
    timestamp:  str = field(default_factory=now_iso)
    context_pct: float = 0.0  # How full was context when this happened?
    metadata:   dict = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps({
            "ts":          self.timestamp,
            "type":        self.type,
            "content":     self.content,
            "context_pct": self.context_pct,
            **self.metadata
        })


@dataclass
class DeadEnd:
    """Something that was tried and failed — prevent repeating it."""
    timestamp:  str
    tried:      str      # What was attempted
    why_failed: str      # Why it didn't work
    use_instead: str     # What to do instead (if known)
    context_pct: float


@dataclass
class Assumption:
    """Something the agent assumed but never verified."""
    timestamp:   str
    assumption:  str
    risk_level:  str     # HIGH | MEDIUM | LOW
    how_to_verify: str
    verified:    bool = False


@dataclass
class Decision:
    """An architectural or technical decision made this session."""
    timestamp:   str
    decision:    str
    reasoning:   str
    context_pct: float   # Context% when made — low-context decisions flagged
    reversible:  bool = True

    @property
    def quality_flag(self) -> str:
        if self.context_pct >= 75:
            return "degraded_context"
        if self.context_pct >= 60:
            return "moderate_context"
        return "high_context"


@dataclass
class TaskList:
    completed:  list = field(default_factory=list)
    remaining:  list = field(default_factory=list)
    blocked:    list = field(default_factory=list)
    next_priority: str = ""


@dataclass
class EnvironmentSnapshot:
    """Exact state needed to restore the working environment."""
    python_version:    str = ""
    venv_path:         str = ""
    start_commands:    list = field(default_factory=list)
    ports:             dict = field(default_factory=dict)
    env_vars_needed:   list = field(default_factory=list)
    env_vars_missing:  list = field(default_factory=list)
    last_git_commit:   str = ""
    git_branch:        str = ""
    dirty_files:       list = field(default_factory=list)
    test_command:      str = ""
    test_passing:      int = 0
    test_failing:      int = 0
    test_failing_files: list = field(default_factory=list)
    snapshot_at:       str = field(default_factory=now_iso)


# ── Main Session object ────────────────────────────────────────────────

@dataclass
class Session:
    """
    Everything about one Claude Code work session.

    Stored as a folder: .cogsessions/sess_20260413_143022_auth/
    """

    # Identity
    id:          str
    project_root: str
    focus:       str = ""     # What this session was about
    parent_id:   Optional[str] = None
    children:    list = field(default_factory=list)

    # Status
    status:      str = "active"   # active | completed | abandoned
    created_at:  str = field(default_factory=now_iso)
    closed_at:   Optional[str] = None
    token_pct_at_close: float = 0.0
    checkpoint_trigger: str = ""  # manual | auto_threshold | pre_compact | session_end

    # Narrative
    one_liner:   str = ""     # Quick summary: "Built JWT auth, refresh token TODO"
    summary:     str = ""     # Full summary (written at close)

    # Cognitive artifacts
    tasks:       TaskList = field(default_factory=TaskList)
    decisions:   list = field(default_factory=list)   # List[Decision]
    dead_ends:   list = field(default_factory=list)   # List[DeadEnd]
    assumptions: list = field(default_factory=list)   # List[Assumption]
    danger_zones: list = field(default_factory=list)  # Free text warnings
    environment: EnvironmentSnapshot = field(default_factory=EnvironmentSnapshot)

    # File tracking
    files_read:    list = field(default_factory=list)   # Files agent read
    files_edited:  list = field(default_factory=list)   # Files agent changed
    files_created: list = field(default_factory=list)   # New files created

    # Error tracking
    errors_seen:    list = field(default_factory=list)  # {error, resolved, how}

    # File cache (avoid re-reading same file)
    file_cache: dict = field(default_factory=dict)   # path → content hash

    # Tool call counter (for autosave trigger)
    tool_call_count: int = 0

    # ── Directory helpers ──────────────────────────────────────────────

    def session_dir(self) -> Path:
        return Path(self.project_root) / ".cogsessions" / self.id

    def ensure_dir(self) -> Path:
        d = self.session_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log_path(self) -> Path:
        return self.session_dir() / "session_log.jsonl"

    # ── Log appender ───────────────────────────────────────────────────

    def append_log(self, entry: LogEntry):
        """Append one event to session_log.jsonl (append-only, never overwrites)."""
        try:
            with open(self.log_path(), "a") as f:
                f.write(entry.to_jsonl() + "\n")
        except Exception as e:
            print(f"[CogSession] Log write error: {e}", file=sys.stderr, flush=True)

    # ── Serialization ──────────────────────────────────────────────────

    def to_manifest(self) -> dict:
        """manifest.json — always loaded (tiny, ~40 tokens)."""
        return {
            "id":                  self.id,
            "parent_id":           self.parent_id,
            "children":            self.children,
            "focus":               self.focus,
            "status":              self.status,
            "created_at":          self.created_at,
            "closed_at":           self.closed_at,
            "token_pct_at_close":  self.token_pct_at_close,
            "checkpoint_trigger":  self.checkpoint_trigger,
            "one_liner":           self.one_liner,
        }

    def to_full_dict(self) -> dict:
        """Full session state for saving to disk."""
        return {
            **self.to_manifest(),
            "summary":       self.summary,
            "project_root":  self.project_root,
            "tasks": {
                "completed":     self.tasks.completed,
                "remaining":     self.tasks.remaining,
                "blocked":       self.tasks.blocked,
                "next_priority": self.tasks.next_priority,
            },
            "decisions": [
                {
                    "timestamp":   d.timestamp,
                    "decision":    d.decision,
                    "reasoning":   d.reasoning,
                    "context_pct": d.context_pct,
                    "reversible":  d.reversible,
                    "quality":     d.quality_flag,
                }
                for d in self.decisions
            ],
            "dead_ends": [
                {
                    "timestamp":   de.timestamp,
                    "tried":       de.tried,
                    "why_failed":  de.why_failed,
                    "use_instead": de.use_instead,
                    "context_pct": de.context_pct,
                }
                for de in self.dead_ends
            ],
            "assumptions": [
                {
                    "timestamp":      a.timestamp,
                    "assumption":     a.assumption,
                    "risk_level":     a.risk_level,
                    "how_to_verify":  a.how_to_verify,
                    "verified":       a.verified,
                }
                for a in self.assumptions
            ],
            "danger_zones":  self.danger_zones,
            "environment": {
                "python_version":    self.environment.python_version,
                "venv_path":         self.environment.venv_path,
                "start_commands":    self.environment.start_commands,
                "ports":             self.environment.ports,
                "env_vars_needed":   self.environment.env_vars_needed,
                "env_vars_missing":  self.environment.env_vars_missing,
                "last_git_commit":   self.environment.last_git_commit,
                "git_branch":        self.environment.git_branch,
                "dirty_files":       self.environment.dirty_files,
                "test_command":      self.environment.test_command,
                "test_passing":      self.environment.test_passing,
                "test_failing":      self.environment.test_failing,
                "test_failing_files": self.environment.test_failing_files,
                "snapshot_at":       self.environment.snapshot_at,
            },
            "files_read":    self.files_read,
            "files_edited":  self.files_edited,
            "files_created": self.files_created,
            "errors_seen":   self.errors_seen,
            "tool_call_count": self.tool_call_count,
        }
