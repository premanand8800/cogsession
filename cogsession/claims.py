"""
cogsession/claims.py

Checkable claims: store the command that *proved* something, then re-run it.

Everything else in this package records what was believed at a moment —
decisions, dead ends, assumptions. None of it is ever re-checked, so the most
expensive recurring failure is not a wrong decision. It is a right one that
quietly stopped being true:

  - a pull request description explaining a schema the code no longer has
  - a source comment naming a constraint that moved
  - a planning document one day old, read as current
  - a test asserting a shape the implementation dropped
  - a docstring contradicting its own function

Each reduces to one sentence: *something was true when it was written and
stopped being true.* A model cannot notice that from a transcript, and a human
notices it in review, which is the expensive place.

So a claim carries its own proof. Not a natural-language description of how to
check it — a command, its expected output, and the commit it held at. When the
files it touches change, re-run it. What comes back is not "you decided X" but
"what you wrote about X is now false", which is the only version that is
actionable.

## On running stored commands

Verification executes shell commands from `claims.json`. That is the same trust
boundary as `.git/hooks`: local, developer-owned, not shared. Two guards make
that boundary explicit rather than assumed:

  - a claims file **tracked by git** is never executed, because a tracked file
    can arrive from someone else
  - every command runs with a timeout and its output is compared, never
    interpreted

Keep commands to cheap, read-only checks — `grep -c`, `test -f`, a fast unit
test. A claim is a tripwire, not a build step.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAIMS_FILE = "claims.json"
DEFAULT_TIMEOUT = 20
MAX_OUTPUT = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Claim:
    """One assertion, with the command that proves it.

    `expect` is compared against stripped stdout. Leave it None to mean "the
    command must merely succeed", which suits `test -f` or a test invocation
    where the exit code is the whole signal.
    """

    claim: str
    verified_by: str
    expect: Optional[str] = None
    watches: list = field(default_factory=list)   # paths whose change re-checks
    asserted_in: str = ""                          # where the claim was made
    at_commit: str = ""
    recorded_at: str = field(default_factory=_now)
    last_status: str = "unverified"                # holds | broken | error | unverified
    last_checked: str = ""
    last_output: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Claim":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class CheckResult:
    claim: Claim
    status: str          # holds | broken | error
    detail: str

    @property
    def broken(self) -> bool:
        return self.status != "holds"


class ClaimStore:
    """Claims for one session, persisted as `claims.json` in its folder."""

    def __init__(self, project_root: Path, session_id: str):
        self.root = Path(project_root)
        self.session_id = session_id
        self.path = self.root / ".cogsessions" / session_id / CLAIMS_FILE

    # ── Persistence ────────────────────────────────────────────────────

    def load(self) -> list[Claim]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return [Claim.from_dict(c) for c in raw.get("claims", []) if isinstance(c, dict)]

    def save(self, claims: list[Claim]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"session_id": self.session_id, "updated_at": _now(),
                 "claims": [c.to_dict() for c in claims]},
                indent=2,
            )
        )

    def add(self, claim: Claim) -> Claim:
        """Record a claim, replacing any earlier one with the same text.

        Replacing rather than appending is deliberate: re-asserting something
        means the current wording is what should be checked, and a store with
        three versions of one claim cannot say which is live.
        """
        claims = [c for c in self.load() if c.claim != claim.claim]
        claims.append(claim)
        self.save(claims)
        return claim

    # ── Verification ───────────────────────────────────────────────────

    def verify(self, only_if_changed: bool = False) -> list[CheckResult]:
        """Re-run each claim's proof. Returns one result per claim checked."""
        if _is_git_tracked(self.root, self.path):
            return []

        claims = self.load()
        results: list[CheckResult] = []
        for claim in claims:
            if only_if_changed and not self._touched(claim):
                continue
            result = run_check(self.root, claim)
            claim.last_status = result.status
            claim.last_checked = _now()
            claim.last_output = result.detail[:MAX_OUTPUT]
            results.append(result)
        if results:
            self.save(claims)
        return results

    def _touched(self, claim: Claim) -> bool:
        """Has anything this claim watches changed since it was recorded?

        No commit and no watch list means "always check": a claim that cannot
        say what would invalidate it is exactly the one worth re-running.
        """
        if not claim.at_commit:
            return True
        changed = _changed_since(self.root, claim.at_commit)
        if changed is None:
            return True
        if not claim.watches:
            return True
        return any(
            any(path == w or path.startswith(w.rstrip("/") + "/") for w in claim.watches)
            for path in changed
        )


def run_check(project_root: Path, claim: Claim) -> CheckResult:
    """Execute one claim's command and compare against `expect`."""
    try:
        proc = subprocess.run(
            claim.verified_by,
            shell=True,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(claim, "error", f"timed out after {DEFAULT_TIMEOUT}s")
    except OSError as exc:
        return CheckResult(claim, "error", f"could not run: {exc}")

    got = (proc.stdout or "").strip()

    if claim.expect is None:
        if proc.returncode == 0:
            return CheckResult(claim, "holds", "command succeeded")
        return CheckResult(
            claim, "broken",
            f"exit {proc.returncode}: {(proc.stderr or got).strip()[:200]}",
        )

    if got == claim.expect.strip():
        return CheckResult(claim, "holds", f"got {got!r}")
    return CheckResult(
        claim, "broken", f"expected {claim.expect.strip()!r}, got {got!r}"
    )


def format_report(results: list[CheckResult]) -> str:
    """One short block, and only when something moved.

    Silence when everything holds is the point. A report that appears every
    session becomes furniture, and furniture is not read.
    """
    if not results:
        return ""
    broken = [r for r in results if r.broken]
    if not broken:
        return ""

    held = len(results) - len(broken)
    lines = [
        f"[CogSession] {len(broken)} claim(s) no longer hold"
        + (f", {held} still do" if held else "")
        + ":"
    ]
    for r in broken:
        lines.append(f"  ✗ {r.claim.claim}")
        lines.append(f"    {r.detail}")
        if r.claim.asserted_in:
            lines.append(f"    asserted in: {r.claim.asserted_in}")
        lines.append(f"    proof: {r.claim.verified_by}")
    return "\n".join(lines)


# ── git helpers ────────────────────────────────────────────────────────


def _git(project_root: Path, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _is_git_tracked(project_root: Path, path: Path) -> bool:
    """Refuse to execute a claims file that could have come from someone else."""
    proc = _git(project_root, "ls-files", "--error-unmatch", str(path))
    return bool(proc and proc.returncode == 0)


def _changed_since(project_root: Path, commit: str) -> Optional[list[str]]:
    """Repo-relative paths changed since `commit`, including uncommitted work.

    None means git could not answer, which callers treat as "check anyway".
    Failing towards checking is right for a tripwire: a missed re-check is a
    stale claim believed, while an extra one costs a `grep`.
    """
    diff = _git(project_root, "diff", "--name-only", commit)
    if diff is None or diff.returncode != 0:
        return None
    paths = {p for p in diff.stdout.splitlines() if p}
    status = _git(project_root, "status", "--porcelain")
    if status and status.returncode == 0:
        for line in status.stdout.splitlines():
            if len(line) > 3:
                paths.add(line[3:].strip().split(" -> ")[-1])
    return sorted(paths)
