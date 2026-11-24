import os
import subprocess

import pytest

from copilot_metrics.collectors import collect
from copilot_metrics.insights import session_quality
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import event, write_records
from tests.copilot_metrics.test_insights import (
    PRICES,
    import_copilot,
    priced_usage,
    tool_start,
)

ME = "me@example.com"


def git(repo, *arguments, **environment):
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", **environment},
    )


def make_repo(path, email=ME):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", email)
    git(path, "config", "user.name", "Someone")
    return path


def commit(repo, name, when, lines=3, email=ME):
    (repo / name).write_text("line\n" * lines)
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


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as opened:
        yield opened


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "work")


def session_records(*minutes):
    return [tool_start(f"t{minute}", "bash", f"2026-01-01T10:{minute:02d}:00Z") for minute in minutes]


def quality(store, **options):
    store.sync_git(idle_minutes=options.get("idle_minutes", 5))
    return session_quality(store, prices=PRICES, zone="UTC", **options)["rows"]


def table_names(store):
    return {row[0] for row in store.connection.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def test_the_collector_reports_each_session_working_directory_so_git_can_be_located(tmp_path):
    path = write_records(tmp_path, [event("session.start", {"sessionId": "s", "context": {"cwd": "/a/b"}})])
    (directory,) = collect("copilot", path, keep_directories=True).directories
    assert directory[1] == "/a/b"


def test_the_collector_returns_no_directories_unless_asked_so_paths_never_leak_by_default(tmp_path):
    path = write_records(tmp_path, [event("session.start", {"sessionId": "s", "context": {"cwd": "/a/b"}})])
    assert collect("copilot", path).directories == ()


def test_no_working_directory_is_stored_while_git_is_disabled_so_opting_out_leaves_no_paths_behind(store, tmp_path):
    import_copilot(store, tmp_path, session_records(0), cwd="/secret/place")
    assert not [name for name in table_names(store) if name.startswith("git_")]
    dump = store.connection.execute("SELECT * FROM events").fetchall()
    assert "/secret/place" not in repr(dump)


def test_working_directories_are_stored_while_enabled_and_not_duplicated_by_reimport(store, tmp_path):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd="/a/b")
    import_copilot(store, tmp_path, session_records(0, 1), cwd="/a/b")
    rows = store.connection.execute("SELECT cwd FROM git_session_dirs").fetchall()
    assert rows == [("/a/b",)]


def test_enabling_git_reimports_unchanged_files_so_history_gains_working_directories(store, tmp_path):
    import_copilot(store, tmp_path, session_records(0), cwd="/a/b")
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd="/a/b")
    assert store.connection.execute("SELECT count(*) FROM git_session_dirs").fetchone() == (1,)


def test_disabling_purges_working_directories_and_commits(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd=str(repo))
    commit(repo, "a.txt", "2026-01-01T10:00:30Z")
    store.sync_git(idle_minutes=5)
    store.set_git_enabled(False)
    assert not [name for name in table_names(store) if name.startswith("git_")]


def test_commits_made_during_a_session_are_counted_with_line_totals(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))
    commit(repo, "a.txt", "2026-01-01T10:02:00Z", lines=3)
    commit(repo, "b.txt", "2026-01-01T10:03:00Z", lines=5)
    (row,) = quality(store)
    assert (row["commits"], row["lines_added"], row["lines_deleted"]) == (2, 8, 0)


def test_a_commit_shortly_after_the_last_event_still_belongs_to_the_session_but_a_late_one_does_not(
    store, tmp_path, repo
):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd=str(repo))
    commit(repo, "a.txt", "2026-01-01T10:04:00Z")
    commit(repo, "b.txt", "2026-01-01T10:30:00Z")
    (row,) = quality(store)
    assert row["commits"] == 1


def test_only_the_users_own_commits_count_so_teammates_on_a_shared_branch_are_not_credited(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))
    commit(repo, "a.txt", "2026-01-01T10:02:00Z")
    commit(repo, "b.txt", "2026-01-01T10:03:00Z", email="other@example.com")
    (row,) = quality(store)
    assert row["commits"] == 1


def test_cost_per_commit_divides_estimated_cost_by_commits_and_is_unavailable_with_no_commits(store, tmp_path, repo):
    store.set_git_enabled(True)
    priced = priced_usage("u", timestamp="2026-01-01T10:01:00Z")
    import_copilot(store, tmp_path, [priced, *session_records(4)], cwd=str(repo))
    (without,) = quality(store)
    assert without["commits"] == 0 and without["cost_per_commit_usd"] is None
    commit(repo, "a.txt", "2026-01-01T10:02:00Z")
    commit(repo, "b.txt", "2026-01-01T10:03:00Z")
    (row,) = quality(store)
    assert row["cost_per_commit_usd"] == pytest.approx(row["estimated_cost_usd"] / 2)


def test_overlapping_sessions_in_one_repo_each_get_credit_for_the_commit(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo), session_id="one")
    import_copilot(store, tmp_path, session_records(1, 5), cwd=str(repo), session_id="two")
    commit(repo, "a.txt", "2026-01-01T10:03:00Z")
    assert [row["commits"] for row in quality(store)] == [1, 1]


def test_git_columns_are_unavailable_not_zero_when_git_is_off(store, tmp_path):
    import_copilot(store, tmp_path, session_records(0), cwd="/a/b")
    (row,) = session_quality(store, prices=PRICES, zone="UTC")["rows"]
    assert (row["commits"], row["lines_added"], row["lines_deleted"], row["cost_per_commit_usd"]) == (None,) * 4


def test_a_working_directory_that_is_not_a_repository_is_unavailable_not_zero(store, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd=str(plain))
    (row,) = quality(store)
    assert row["commits"] is None


def test_a_session_without_a_recorded_directory_is_unavailable_not_zero(store, tmp_path):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0))
    (row,) = quality(store)
    assert row["commits"] is None


def test_a_deleted_repository_is_skipped_without_failing_the_sync(store, tmp_path):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0), cwd=str(tmp_path / "gone"))
    (row,) = quality(store)
    assert row["commits"] is None


def test_stored_git_data_holds_only_short_shas_times_and_counts_never_messages_or_paths(store, tmp_path, repo):
    store.set_git_enabled(True)
    import_copilot(store, tmp_path, session_records(0, 4), cwd=str(repo))
    commit(repo, "a.txt", "2026-01-01T10:02:00Z")
    store.sync_git(idle_minutes=5)
    columns = {
        row[0]
        for row in store.connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'git_commits'"
        ).fetchall()
    }
    assert columns == {"repo", "sha", "committed_at", "added", "deleted"}
    (sha,) = store.connection.execute("SELECT sha FROM git_commits").fetchone()
    assert len(sha) <= 12
    assert "secret message" not in repr(store.connection.execute("SELECT * FROM git_commits").fetchall())
    assert str(repo) not in repr(store.connection.execute("SELECT * FROM git_commits").fetchall())


def test_the_import_command_reads_git_history_when_enabled_so_the_dashboard_never_shells_out(
    tmp_path, repo, monkeypatch, capsys
):
    from tests.copilot_metrics.test_app import run_cli

    database = tmp_path / "metrics.duckdb"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    records = [
        event("session.start", {"sessionId": "s", "context": {"cwd": str(repo)}}, "start", "2026-01-01T10:00:00Z"),
        *session_records(4),
    ]
    write_records(sessions, records)
    commit(repo, "a.txt", "2026-01-01T10:02:00Z")
    with Store(database) as store:
        store.set_git_enabled(True)
    run_cli(
        monkeypatch, capsys, "import", "--provider", "copilot", "--source-root", str(sessions), "--db", str(database)
    )
    with Store(database, read_only=True) as store:
        (row,) = session_quality(store, prices=PRICES, zone="UTC")["rows"]
    assert row["commits"] == 1
