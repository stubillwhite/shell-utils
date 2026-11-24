import sys

import pytest

from copilot_metrics.app import main
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_app import run_cli
from tests.copilot_metrics.test_collectors import event, write_records


@pytest.fixture
def database(tmp_path):
    return tmp_path / "metrics.duckdb"


def table_names(store):
    return {row[0] for row in store.connection.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def test_git_access_is_off_until_the_user_explicitly_enables_it(database):
    with Store(database) as store:
        assert store.git_enabled() is False


def test_the_git_switch_persists_across_connections_so_imports_in_other_runs_honour_it(database):
    with Store(database) as store:
        store.set_git_enabled(True)
    with Store(database) as store:
        assert store.git_enabled() is True
    with Store(database, read_only=True) as store:
        assert store.git_enabled() is True


def test_a_database_created_before_the_switch_existed_reads_as_disabled_without_being_migrated(database):
    import duckdb

    duckdb.connect(str(database)).close()
    with Store(database, read_only=True) as store:
        assert store.git_enabled() is False
        assert "settings" not in table_names(store)


def test_disabling_purges_every_git_table_but_leaves_usage_data_untouched(database, tmp_path):
    records = [event("session.start", {"sessionId": "s"}, "start"), event("abort", {}, "x")]
    with Store(database) as store:
        store.import_file("copilot", write_records(tmp_path, records))
        store.set_git_enabled(True)
        store.connection.execute("CREATE TABLE git_commits (sha VARCHAR)")
        store.connection.execute("INSERT INTO git_commits VALUES ('abc123')")
        events_before = store.connection.execute("SELECT count(*) FROM events").fetchone()
        store.set_git_enabled(False)
        assert not [name for name in table_names(store) if name.startswith("git_")]
        assert store.connection.execute("SELECT count(*) FROM events").fetchone() == events_before
        assert store.git_enabled() is False


def test_enabling_never_removes_existing_git_data(database):
    with Store(database) as store:
        store.connection.execute("CREATE TABLE git_commits (sha VARCHAR)")
        store.set_git_enabled(True)
        assert "git_commits" in table_names(store)


def test_cli_git_enable_and_disable_toggle_the_stored_switch_and_say_what_disable_deletes(
    database, monkeypatch, capsys
):
    code, enabled_output = run_cli(monkeypatch, capsys, "git", "enable", "--db", str(database))
    assert code == 0 and "enabled" in enabled_output
    with Store(database, read_only=True) as store:
        assert store.git_enabled() is True
    code, disabled_output = run_cli(monkeypatch, capsys, "git", "disable", "--db", str(database))
    assert code == 0 and "disabled" in disabled_output and "purged" in disabled_output
    with Store(database, read_only=True) as store:
        assert store.git_enabled() is False


def test_cli_git_rejects_an_unknown_action(database, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["copilot-metrics", "git", "maybe", "--db", str(database)])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
