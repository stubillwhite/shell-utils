import subprocess

import pytest

from copilot_metrics.git_history import MAX_HUNKS_PER_COMMIT
from copilot_metrics.insights import session_quality
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import event, write_records
from tests.copilot_metrics.test_git_history import (
    ME,
    git,
    import_copilot,
    make_repo,
    quality,
    session_records,
)
from tests.copilot_metrics.test_insights import tool_start

FIRST = "2026-01-01T10:02:00Z"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as opened:
        yield opened


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "work")


def write_commit(repo, name, lines, when, email=ME):
    (repo / name).write_text("".join(f"{line}\n" for line in lines))
    git(repo, "add", name)
    git(
        repo,
        "commit",
        "-q",
        "-m",
        "secret message",
        GIT_AUTHOR_DATE=when,
        GIT_COMMITTER_DATE=when,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_EMAIL=email,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def edges(store):
    store.sync_git(idle_minutes=5)
    return store.connection.execute("SELECT sha, origin_sha, lines FROM git_rework ORDER BY committed_at").fetchall()


def start_session(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))


def test_a_later_own_commit_that_replaces_lines_from_a_session_commit_is_recorded_against_the_original(
    store, tmp_path, repo
):
    start_session(store, tmp_path, repo)
    original = write_commit(repo, "a.txt", ["a", "b", "c", "d", "e"], FIRST)
    fix = write_commit(repo, "a.txt", ["a", "B", "C", "d", "e"], "2026-01-02T09:00:00Z")
    assert edges(store) == [(fix, original, 2)]


def test_lines_deleted_outright_count_as_rework_because_the_work_was_thrown_away(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    original = write_commit(repo, "a.txt", ["a", "b", "c"], FIRST)
    removal = write_commit(repo, "a.txt", ["a"], "2026-01-02T09:00:00Z")
    assert edges(store) == [(removal, original, 2)]


def test_pure_additions_are_not_rework_since_nothing_earlier_was_touched(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["a", "b", "c", "d"], "2026-01-02T09:00:00Z")
    assert edges(store) == []


def test_a_rewrite_after_the_rework_window_is_ignored_so_ordinary_evolution_is_not_blamed_on_the_session(
    store, tmp_path, repo
):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-16T09:00:00Z")
    assert edges(store) == []


def test_a_rewrite_just_inside_the_rework_window_is_recorded(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    original = write_commit(repo, "a.txt", ["a", "b"], FIRST)
    late = write_commit(repo, "a.txt", ["x", "y"], "2026-01-15T09:00:00Z")
    assert edges(store) == [(late, original, 2)]


def test_a_teammates_rewrite_is_not_the_users_rework(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T09:00:00Z", email="other@example.com")
    assert edges(store) == []


def test_rewriting_lines_that_predate_the_session_is_not_counted_against_it(store, tmp_path, repo):
    write_commit(repo, "a.txt", ["a", "b"], "2025-12-01T10:00:00Z")
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["x", "y"], FIRST)
    assert edges(store) == []


def test_a_resync_replaces_edges_instead_of_duplicating_them(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T09:00:00Z")
    edges(store)
    assert len(edges(store)) == 1


def test_rework_edges_hold_only_short_shas_counts_and_times_never_paths_or_messages(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "secret-file.txt", ["a", "b"], FIRST)
    write_commit(repo, "secret-file.txt", ["x", "y"], "2026-01-02T09:00:00Z")
    edges(store)
    columns = {
        row[0]
        for row in store.connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'git_rework'"
        ).fetchall()
    }
    stored = repr(store.connection.execute("SELECT * FROM git_rework").fetchall())
    assert columns == {"repo", "sha", "origin_sha", "lines", "committed_at"}
    assert "secret" not in stored and str(repo) not in stored


def test_disabling_git_purges_rework_edges(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T09:00:00Z")
    edges(store)
    store.set_git_enabled(False)
    assert store.connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'git_rework'"
    ).fetchone() == (0,)


def test_rework_lines_and_share_are_credited_to_the_session_that_made_the_original_commit(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b", "c", "d"], FIRST)
    write_commit(repo, "a.txt", ["a", "B", "C", "d"], "2026-01-05T09:00:00Z")
    (row,) = quality(store)
    assert (row["lines_added"], row["rework_lines"], row["rework_pct"]) == (4, 2, 50.0)


def test_commits_fixed_counts_session_commits_rewritten_within_the_fix_window_only(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "b.txt", ["a", "b"], "2026-01-01T10:03:00Z")
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T09:00:00Z")
    write_commit(repo, "b.txt", ["x", "y"], "2026-01-05T09:00:00Z")
    (row,) = quality(store)
    assert (row["commits_fixed"], row["rework_lines"]) == (1, 4)


def test_the_fix_window_is_configurable_in_hours(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T09:00:00Z")
    store.sync_git(idle_minutes=5)
    rows = lambda hours: session_quality(store, prices={}, zone="UTC", fix_hours=hours)["rows"]  # noqa: E731
    assert (rows(1)[0]["commits_fixed"], rows(24)[0]["commits_fixed"]) == (0, 1)


def test_a_session_with_commits_but_no_rewrites_reports_zero_rework_not_unavailable(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    (row,) = quality(store)
    assert (row["commits_fixed"], row["rework_lines"], row["rework_pct"]) == (0, 0, 0.0)


def test_rework_share_is_unavailable_with_no_added_lines_and_all_rework_columns_when_git_is_off(store, tmp_path, repo):
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))
    (off,) = quality(store)
    assert (off["commits_fixed"], off["rework_lines"], off["rework_pct"]) == (None,) * 3
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))
    (empty,) = quality(store)
    assert (empty["commits_fixed"], empty["rework_lines"], empty["rework_pct"]) == (0, 0, None)


def test_a_rewrite_made_in_a_later_session_is_still_charged_to_the_original_session(store, tmp_path, repo):
    start_session(store, tmp_path, repo)
    write_commit(repo, "a.txt", ["a", "b"], FIRST)
    folder = tmp_path / "later"
    folder.mkdir()
    next_day = [
        event(
            "session.start",
            {"sessionId": "later", "context": {"cwd": str(repo)}},
            "later-start",
            "2026-01-02T10:00:00Z",
        ),
        tool_start("n1", "bash", "2026-01-02T10:04:00Z"),
    ]
    store.import_file("copilot", write_records(folder, next_day))
    write_commit(repo, "a.txt", ["x", "y"], "2026-01-02T10:02:00Z")
    original, later = quality(store)
    assert (original["rework_lines"], later["rework_lines"], later["commits"]) == (2, 0, 1)


def test_the_sessions_note_warns_that_very_large_rewrites_are_undercounted(store):
    notes = " ".join(session_quality(store, prices={}, zone="UTC")["notes"])
    assert str(MAX_HUNKS_PER_COMMIT) in notes and "squash" in notes.lower()
