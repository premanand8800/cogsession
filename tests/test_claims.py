"""Claims: assertions that carry their own proof.

The failure being targeted is not a wrong decision. It is a right one that
stopped being true — a description of a schema the code no longer has, a
comment naming a constraint that moved. So the tests care about two things
above all: that a broken claim is reported with the reason, and that a holding
claim is reported not at all.
"""

import subprocess
from pathlib import Path

import pytest

from cogsession.claims import Claim, ClaimStore, format_report, run_check

SCHEMA = "schema.sql"
GOOD = "UNIQUE (pool_id, workspace_id, retention_window)\n"
STALE = "UNIQUE (pool_id, retention_window)\n"
PROOF = f"grep -c 'UNIQUE (pool_id, workspace_id, retention_window)' {SCHEMA}"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / SCHEMA).write_text(GOOD)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (tmp_path / ".cogsessions" / "sess_demo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(repo: Path) -> ClaimStore:
    return ClaimStore(repo, "sess_demo")


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _claim(root: Path, **over) -> Claim:
    base = dict(
        claim="the composite key includes the tenant column",
        verified_by=PROOF,
        expect="1",
        watches=[SCHEMA],
        asserted_in="PR description, line 26",
        at_commit=_head(root),
    )
    base.update(over)
    return Claim(**base)


# ── The core behaviour ─────────────────────────────────────────────────


def test_a_true_claim_verifies(repo: Path) -> None:
    assert run_check(repo, _claim(repo)).status == "holds"


def test_a_claim_breaks_when_the_file_moves_under_it(repo: Path, store: ClaimStore) -> None:
    """The real case: the schema changed and the prose describing it did not."""
    store.add(_claim(repo))
    (repo / SCHEMA).write_text(STALE)

    results = store.verify(only_if_changed=True)
    assert [r.status for r in results] == ["broken"]

    report = format_report(results)
    assert "no longer hold" in report
    assert "expected '1', got '0'" in report
    assert "PR description, line 26" in report  # so you know where to go fix it
    assert PROOF in report                      # and how it was checked


def test_a_holding_claim_reports_nothing(repo: Path, store: ClaimStore) -> None:
    """Silence is the feature. A report every session becomes furniture."""
    store.add(_claim(repo))
    assert format_report(store.verify(only_if_changed=False)) == ""


def test_unchanged_files_are_not_rechecked(repo: Path, store: ClaimStore) -> None:
    store.add(_claim(repo))
    assert store.verify(only_if_changed=True) == []


def test_a_claim_watching_nothing_is_always_checked(repo: Path, store: ClaimStore) -> None:
    """A claim that cannot say what would invalidate it is the one worth
    re-running, so absence of a watch list means 'always'."""
    store.add(_claim(repo, watches=[]))
    assert len(store.verify(only_if_changed=True)) == 1


def test_an_unrelated_change_does_not_trigger_a_check(repo: Path, store: ClaimStore) -> None:
    store.add(_claim(repo))
    (repo / "README.md").write_text("nothing to do with the schema\n")
    assert store.verify(only_if_changed=True) == []


# ── Shapes of proof ────────────────────────────────────────────────────


def test_exit_code_only_proof_holds(repo: Path) -> None:
    """`expect=None` means the command merely has to succeed."""
    assert run_check(repo, _claim(repo, verified_by=f"test -f {SCHEMA}", expect=None)).status == "holds"


def test_exit_code_only_proof_breaks(repo: Path) -> None:
    result = run_check(repo, _claim(repo, verified_by="test -f nope.sql", expect=None))
    assert result.status == "broken"
    assert "exit 1" in result.detail


def test_a_command_that_cannot_run_is_an_error_not_a_break(repo: Path) -> None:
    """Distinguished on purpose: a broken proof is not the same as a false
    claim, and conflating them would cry wolf."""
    result = run_check(repo, _claim(repo, verified_by="definitely-not-a-binary --x", expect="1"))
    assert result.status in {"broken", "error"}


def test_a_slow_proof_is_cut_off(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cogsession.claims.DEFAULT_TIMEOUT", 1)
    result = run_check(repo, _claim(repo, verified_by="sleep 5", expect=""))
    assert result.status == "error"
    assert "timed out" in result.detail


# ── Store semantics ────────────────────────────────────────────────────


def test_re_asserting_replaces_rather_than_stacks(repo: Path, store: ClaimStore) -> None:
    """Three versions of one claim cannot say which is live."""
    store.add(_claim(repo))
    store.add(_claim(repo, verified_by=f"grep -c 'workspace_id' {SCHEMA}"))
    claims = store.load()
    assert len(claims) == 1
    assert "workspace_id" in claims[0].verified_by


def test_status_is_persisted_after_a_check(repo: Path, store: ClaimStore) -> None:
    store.add(_claim(repo))
    (repo / SCHEMA).write_text(STALE)
    store.verify(only_if_changed=True)
    assert store.load()[0].last_status == "broken"


def test_an_empty_store_is_silent(store: ClaimStore) -> None:
    assert store.verify() == []
    assert format_report([]) == ""


def test_a_corrupt_store_does_not_raise(store: ClaimStore) -> None:
    """A tripwire must never be the thing that breaks the session."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json")
    assert store.load() == []


# ── The trust boundary ─────────────────────────────────────────────────


def test_a_tracked_claims_file_is_never_executed(repo: Path, store: ClaimStore) -> None:
    """Running stored shell commands is the same trust boundary as .git/hooks:
    local and developer-owned. A *tracked* file can arrive from someone else,
    so it is refused rather than trusted."""
    store.add(_claim(repo))
    # Force the claims file to be tracked, which normally .gitignore prevents.
    _git(repo, "add", "-f", str(store.path))
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "track claims")
    (repo / SCHEMA).write_text(STALE)
    assert store.verify(only_if_changed=False) == []


def test_verification_works_outside_a_git_repo(tmp_path: Path) -> None:
    """No git means no change detection, so everything is checked rather than
    silently skipped. Failing towards checking is right for a tripwire."""
    (tmp_path / SCHEMA).write_text(GOOD)
    (tmp_path / ".cogsessions" / "sess_demo").mkdir(parents=True)
    store = ClaimStore(tmp_path, "sess_demo")
    store.add(Claim(claim="key has the tenant column", verified_by=PROOF, expect="1"))
    assert [r.status for r in store.verify(only_if_changed=True)] == ["holds"]
