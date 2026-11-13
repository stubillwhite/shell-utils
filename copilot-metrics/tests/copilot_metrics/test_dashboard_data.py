import csv
import io
import json

import duckdb
import pytest

from copilot_metrics.dashboard_data import coverage_rows, load_dashboard, ranking_rows
from copilot_metrics.reports import daily_series, render
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import event, write_records


@pytest.fixture
def dashboard_database(tmp_path):
    database = tmp_path / "metrics.duckdb"
    records = [
        event("session.start", {"sessionId": "one", "context": {"cwd": "/work/a/app"}}),
        event(
            "session.usage_record",
            {"usage": {"apiCallId": "a", "model": "model-a", "inputTokens": 10, "outputTokens": 0}},
            "a",
            "2026-01-02T01:00:00Z",
        ),
        event(
            "session.usage_record",
            {"usage": {"apiCallId": "b", "model": "model-b", "inputTokens": 20}},
            "b",
            "2026-01-02T02:00:00Z",
        ),
        event("session.start", {"sessionId": "two", "context": {"cwd": "/work/b/app"}}, "two"),
    ]
    with Store(database) as store:
        source = write_records(tmp_path, records)
        source.write_text(source.read_text() + "malformed\n")
        store.import_file("copilot", source)
        store.import_file(
            "claude",
            write_records(
                tmp_path,
                [
                    {
                        "type": "assistant",
                        "sessionId": "claude",
                        "timestamp": "2026-01-02T01:00:00Z",
                        "message": {
                            "id": "m",
                            "usage": {"input_tokens": 40, "output_tokens": 5, "cache_read_input_tokens": 999},
                        },
                    }
                ],
            ),
        )
    return database


def test_loader_preserves_provider_totals_nulls_and_releases_read_connection(dashboard_database):
    before = dashboard_database.read_bytes()
    results = load_dashboard(dashboard_database, zone="UTC")
    copilot, claude = results["provider"]["rows"][1], results["provider"]["rows"][0]
    assert (copilot["provider"], copilot["sessions"], copilot["input_tokens"], copilot["output_tokens"]) == (
        "copilot",
        2,
        30,
        0,
    )
    assert claude["input_tokens"] == 40
    assert copilot["cached_input_tokens"] is None
    assert len(results["model"]["rows"]) > copilot["sessions"]
    assert dashboard_database.read_bytes() == before
    with Store(dashboard_database) as store:
        assert store.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 2
    exported = json.loads(render(results["provider"], "json"))
    assert exported["rows"][1]["cached_input_tokens"] is None
    assert list(csv.DictReader(io.StringIO(render(results["provider"], "csv"))))[1]["cached_input_tokens"] == ""


def test_loader_uses_inclusive_local_days_and_keeps_diagnostics_date_independent(dashboard_database):
    selected = load_dashboard(
        dashboard_database, provider="copilot", start="2026-01-01", end="2026-01-01", zone="America/New_York"
    )
    assert selected["provider"]["rows"][0]["input_tokens"] == 30
    assert {row["day"] for row in selected["day"]["rows"]} == {"2026-01-01"}
    empty = load_dashboard(dashboard_database, start="2027-01-01", zone="UTC")
    assert empty["provider"]["rows"] == []
    assert empty["provider"]["diagnostics"] == [{"provider": "copilot", "code": "invalid_json", "count": 1}]


def test_shared_daily_series_preserves_known_zero_partial_and_missing_categories(dashboard_database):
    results = load_dashboard(dashboard_database, zone="UTC")
    series = dict(daily_series(results["day"]))
    assert series["2026-01-01"] == [("in", None, True), ("out", None, True)]
    assert series["2026-01-02"] == [("in", 70, False), ("out", 5, False)]
    assert "2026-01-03" not in series
    copilot = load_dashboard(dashboard_database, provider="copilot", zone="UTC")
    assert dict(daily_series(copilot["day"]))["2026-01-02"][1][1] == 0


def test_coverage_and_rankings_do_not_add_session_counts_across_models(dashboard_database):
    results = load_dashboard(dashboard_database, zone="UTC")
    rows = coverage_rows(results["provider"])
    incoming = next(row for row in rows if row["provider"] == "copilot" and row["category"] == "input_tokens")
    assert (incoming["observed_sessions"], incoming["sessions"], incoming["percent"]) == (1, 2, 50)
    projects = ranking_rows(results["project"], field="input_tokens")
    assert len(projects) == 3
    same_names = [row for row in projects if row["provider"] == "copilot"]
    assert len({row["identity"] for row in same_names}) == 2
    assert {row["label"] for row in same_names} == {"app"}
    assert sorted((row["value"] for row in same_names), key=lambda value: value is None) == [30, None]
    assert all("/work" not in str(row) for row in projects)
    empty_denominator = {"rows": [{**results["provider"]["rows"][0], "sessions": 0}]}
    assert all(row["percent"] is None for row in coverage_rows(empty_denominator))


def test_missing_database_does_not_create_paths_and_read_only_does_not_change_umask(tmp_path, monkeypatch):
    database = tmp_path / "existing.duckdb"
    with Store(database):
        pass

    def forbidden(*args):
        raise AssertionError("read-only access must not mutate process-wide permissions")

    monkeypatch.setattr("copilot_metrics.storage.os.umask", forbidden)
    assert load_dashboard(database)["provider"]["rows"] == []
    missing = tmp_path / "absent" / "missing.duckdb"
    with pytest.raises(duckdb.Error):
        load_dashboard(missing)
    assert not missing.parent.exists()


@pytest.fixture
def attention_database(dashboard_database, tmp_path):
    from copilot_metrics.collectors import opaque

    observations = (
        ("copilot", "one", "approval_notification", "2026-01-02T01:00:00Z", False),
        ("copilot", "one", "approval_notification", "2026-01-04T01:00:00Z", False),
        ("copilot", "one", "permission_check", "2026-01-02T01:00:00Z", False),
        ("claude", "claude:", "guidance_notification", "2026-01-02T01:00:00Z", False),
        ("claude", "claude:", "elicitation_accept", "2026-01-02T01:00:00Z", False),
        ("claude", "claude:child", "elicitation_cancel", "2026-01-02T02:00:00Z", True),
        ("codex", "codex", "permission_check", "2026-01-02T01:00:00Z", False),
        ("codex", "codex", "clarification_check", "2026-01-02T01:00:00Z", False),
    )
    with Store(dashboard_database) as store:
        for provider in ("copilot", "claude", "codex"):
            path = tmp_path / f"{provider}-hooks.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "version": 1,
                            "provider": name,
                            "session": opaque(session),
                            "identity": opaque(str(index)),
                            "kind": kind,
                            "timestamp": timestamp,
                            "child": child,
                        }
                    )
                    for index, (name, session, kind, timestamp, child) in enumerate(observations)
                    if name == provider
                )
            )
            store.import_file(provider, path, interruptions=True)
    return dashboard_database


def test_attention_evidence_preserves_categories_nulls_and_provider_session_denominators(attention_database):
    from copilot_metrics.dashboard_data import attention_rows

    result = load_dashboard(attention_database, zone="UTC")["provider"]
    before = json.dumps(result)
    rows = attention_rows(result)
    approval = next(row for row in rows if row["provider"] == "copilot" and row["category"] == "Approval notifications")
    assert (approval["observations"], approval["observed_sessions"], approval["recorded_sessions"]) == (2, 1, 2)
    assert approval["evidence"] == "Hook checks and attention notifications"
    codex = [row for row in rows if row["provider"] == "codex"]
    assert next(row for row in codex if row["category"] == "Approval notifications")["observations"] is None
    assert next(row for row in codex if row["category"] == "Permission checks")["observations"] == 1
    historical = next(row for row in rows if row["provider"] == "copilot" and row["category"] == "Approval requests")
    assert historical["evidence"] == "Historical inferred requests"
    assert historical["observations"] is None
    assert len(rows) == 27
    assert json.dumps(result) == before


def test_attention_session_exports_are_focused_filtered_and_preserve_report_metadata(attention_database):
    from copilot_metrics.collectors import opaque
    from copilot_metrics.dashboard_data import attention_sessions

    result = load_dashboard(attention_database, zone="America/New_York", start="2026-01-01", end="2026-01-01")[
        "session"
    ]
    selected = attention_sessions(result, category="approval_notification")
    assert selected["view_filter"] == {"attention_category": "approval_notification"}
    assert selected["timezone"] == "America/New_York"
    assert selected["from"] == selected["to"] == "2026-01-01"
    assert selected["notes"] == result["notes"]
    assert selected["diagnostics"] == result["diagnostics"]
    assert len(selected["rows"]) == 1
    row = selected["rows"][0]
    assert (row["provider"], row["session"], row["approval_notifications"]) == ("copilot", opaque("one"), 1)
    assert row["top_level_sessions"] == 1 and row["child_sessions"] == 0
    assert set(row) == set(selected["columns"])
    assert not {"elapsed_seconds", "model", "project", "input_tokens"} & set(row)
    assert json.loads(render(selected, "json"))["rows"] == [row]
    assert list(csv.DictReader(io.StringIO(render(selected, "csv"))))[0]["approval_notifications"] == "1"
    child = attention_sessions(result, category="elicitation_cancel")["rows"][0]
    assert child["child_sessions"] == 1
    assert attention_sessions(result, category="approval_request")["rows"] == []
    with pytest.raises(ValueError, match="invalid_attention_category"):
        attention_sessions(result, category="input_tokens")
