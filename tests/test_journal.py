"""`session.md` — the greppable, timestamped, git-aware record.

The structured files are for loading. This one is for looking something up
without loading anything, so the tests are mostly about grep behaving: one
matched line has to be useful on its own, and appending must never rewrite
what came before.
"""

import re
import subprocess
from pathlib import Path

import pytest

from cogsession.journal import JOURNAL_FILE, Journal, git_context, log_view


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app.py").write_text("code\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first")
    return tmp_path


@pytest.fixture
def journal(repo: Path) -> Journal:
    j = Journal(repo, "sess_demo")
    j.ensure_header(focus="the schema", harness_session_id="host-9")
    return j


# ── The header ─────────────────────────────────────────────────────────


def test_the_header_is_written_once(journal: Journal) -> None:
    """SessionStart can fire twice for one session, and a second header would
    split the timeline in two."""
    assert journal.ensure_header() is False
    assert journal.path.read_text().count("# Session") == 1


def test_the_header_records_when_where_and_what(journal: Journal) -> None:
    text = journal.path.read_text()
    assert "**Started:**" in text
    assert "the schema" in text
    assert "host-9" in text
    assert re.search(r"\*\*Repo:\*\* `\w+@[0-9a-f]+", text), "branch@commit missing"


def test_a_project_without_git_still_works(tmp_path: Path) -> None:
    """No git is a normal case, not an error."""
    j = Journal(tmp_path, "sess_demo")
    assert j.ensure_header() is True
    assert "no-git" in j.path.read_text()
    assert git_context(tmp_path).is_repo is False


# ── Entries: self-describing, so one grep line is enough ───────────────


def test_every_entry_header_carries_time_type_and_repo(journal: Journal) -> None:
    """The property that makes `grep` without -A/-B worth anything."""
    journal.append("decision", "pin the window to the pool", context_pct=42)
    line = [l for l in journal.path.read_text().splitlines() if "**decision**" in l][0]
    assert re.match(r"^### `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}`", line), line
    assert "ctx 42%" in line
    assert re.search(r"`\w+@[0-9a-f]+", line), "repo state missing from the entry line"


def test_the_timestamp_is_local_time_with_an_offset(journal: Journal) -> None:
    """A human reads this, and a human is in a timezone."""
    journal.append("decision", "x")
    assert re.search(r"\d{2}:\d{2}:\d{2} [+-]\d{4}", journal.path.read_text())


def test_detail_is_recorded_as_labelled_lines(journal: Journal) -> None:
    journal.append("dead_end", "adding the window to the dedup index",
                   detail={"why_failed": "silently destroys dedup", "use_instead": "pin to the parent"})
    text = journal.path.read_text()
    assert "_why_failed_: silently destroys dedup" in text
    assert "_use_instead_: pin to the parent" in text


def test_empty_detail_values_are_dropped(journal: Journal) -> None:
    journal.append("decision", "x", detail={"reasoning": "", "risk": None, "kept": "yes"})
    text = journal.path.read_text()
    assert "_kept_: yes" in text
    assert "_reasoning_" not in text and "_risk_" not in text


def test_an_entry_with_nothing_to_say_is_not_written(journal: Journal) -> None:
    before = journal.path.read_text()
    assert journal.append("decision", "") is False
    assert journal.path.read_text() == before


# ── Append-only ────────────────────────────────────────────────────────


def test_appending_never_rewrites_earlier_content(journal: Journal) -> None:
    """A grep line number handed out earlier has to stay valid."""
    journal.append("decision", "first thing")
    snapshot = journal.path.read_text()
    journal.append("decision", "second thing")
    assert journal.path.read_text().startswith(snapshot)


def test_setting_the_focus_later_appends_rather_than_edits(repo: Path) -> None:
    """A focus that arrives late is a fact with a time of its own; rewriting
    the header would invalidate every line number already given out."""
    j = Journal(repo, "sess_demo")
    j.ensure_header()
    header = j.path.read_text()
    j.set_focus("decided later")
    text = j.path.read_text()
    assert text.startswith(header)
    assert "**focus**" in text and "decided later" in text


# ── Git context is captured live, per entry ────────────────────────────


def test_the_dirty_count_moves_with_the_tree(journal: Journal, repo: Path) -> None:
    """A decision was made against a specific state of the code. That state is
    the usual answer to 'why did we think that?'."""
    journal.append("decision", "clean tree")
    (repo / "app.py").write_text("changed\n")
    journal.append("decision", "dirty tree")

    lines = [l for l in journal.path.read_text().splitlines() if "**decision**" in l]
    assert "+" not in lines[0].rsplit("`", 2)[-2], "clean tree should carry no dirty marker"
    assert "+1" in lines[1], "dirty tree should be marked"


def test_the_commit_is_greppable_and_joins_back_to_git_log(journal: Journal, repo: Path) -> None:
    commit = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    journal.append("decision", "made here")
    hits = subprocess.run(["grep", "-c", commit, str(journal.path)],
                          capture_output=True, text=True)
    assert int(hits.stdout.strip()) >= 1


# ── grep, which is the whole point ─────────────────────────────────────


def test_grep_by_type_answers_what_has_already_failed(journal: Journal) -> None:
    journal.append("decision", "the good path")
    journal.append("dead_end", "the bad path", detail={"why_failed": "breaks streaming"})
    out = subprocess.run(["grep", "-A4", "dead_end", str(journal.path)],
                         capture_output=True, text=True).stdout
    assert "the bad path" in out
    assert "breaks streaming" in out
    assert "the good path" not in out


def test_grep_by_hour_answers_what_happened_then(journal: Journal) -> None:
    journal.append("decision", "in this hour")
    hour = journal.path.read_text().split("`")[-2][:13]  # YYYY-MM-DD HH
    out = subprocess.run(["grep", "-c", hour, str(journal.path)],
                         capture_output=True, text=True)
    assert int(out.stdout.strip()) >= 1


# ── The log view: a git log for sessions ───────────────────────────────


def test_the_log_view_is_newest_first(journal: Journal) -> None:
    journal.append("decision", "older")
    journal.append("error", "newer")
    view = log_view(journal.root, ["sess_demo"])
    assert view.index("error") < view.index("decision")


def test_the_log_view_filters_by_type(journal: Journal) -> None:
    journal.append("decision", "keep me")
    journal.append("error", "drop me")
    view = log_view(journal.root, ["sess_demo"], type_filter="decision")
    assert "decision" in view and "error" not in view


def test_the_log_view_respects_the_limit(journal: Journal) -> None:
    for i in range(10):
        journal.append("decision", f"entry {i}")
    body = log_view(journal.root, ["sess_demo"], limit=3).splitlines()[2:]
    assert len(body) == 3


def test_the_log_view_is_honest_when_empty(tmp_path: Path) -> None:
    assert "No journal entries" in log_view(tmp_path, ["nope"])


def test_a_malformed_journal_does_not_raise(repo: Path) -> None:
    """The journal is a convenience and must never be the thing that breaks."""
    j = Journal(repo, "sess_demo")
    j.path.parent.mkdir(parents=True, exist_ok=True)
    j.path.write_text("### not a real header\nrandom text\n")
    assert j.entries() == []
    assert "No journal entries" in log_view(repo, ["sess_demo"])


# ── Wiring: recording anything reaches the journal ─────────────────────


def test_recording_through_the_session_reaches_the_journal(repo: Path) -> None:
    """One choke point: anything that logs an event lands in both files, so the
    journal cannot develop holes. A grep that finds nothing would otherwise
    read as proof that nothing happened."""
    from cogsession.session.models import LogEntry, Session

    session = Session(id="sess_wired", project_root=str(repo))
    session.ensure_dir()
    session.append_log(LogEntry(type="decision", content="recorded via the session", context_pct=30))

    text = (repo / ".cogsessions" / "sess_wired" / JOURNAL_FILE).read_text()
    assert "recorded via the session" in text
    assert "**decision**" in text
