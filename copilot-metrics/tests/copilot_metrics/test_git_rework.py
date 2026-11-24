import subprocess

import pytest

from copilot_metrics.storage import Store
from tests.copilot_metrics.test_git_history import (
    ME,
    git,
    import_copilot,
    make_repo,
    session_records,
)

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
