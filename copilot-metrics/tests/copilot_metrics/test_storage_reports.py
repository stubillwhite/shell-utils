import csv
import hashlib
import io
import json
import os

import duckdb
import pytest

from copilot_metrics.collectors import opaque
from copilot_metrics.reports import render, report
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import event, write_records


def usage(identity, tokens, timestamp="2026-01-01T10:00:00Z", model="model"):
    return event(
        "session.usage_record",
        {"usage": {"apiCallId": identity, "inputTokens": tokens, "model": model}},
        identity,
        timestamp,
    )


def test_daily_chart_combines_only_reported_input_output_and_preserves_partial_observations(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as store:
        source = write_records(
            tmp_path,
            [
                event("session.start", {"sessionId": "s"}),
                event(
                    "session.usage_record",
                    {"usage": {"inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 999}},
                    "usage",
                ),
                event("session.start", {"sessionId": "missing"}, "missing"),
                event(
                    "session.usage_record",
                    {"usage": {"inputTokens": 0}},
                    "zero",
                    "2026-01-02T10:00:00Z",
                ),
            ],
        )
        store.import_file("copilot", source)
        source = write_records(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "sessionId": "claude",
                    "timestamp": "2026-01-01T12:00:00Z",
                    "message": {"id": "m", "usage": {"input_tokens": 100, "output_tokens": 50}},
                }
            ],
        )
        store.import_file("claude", source)
        result = report(store, group_by="day", zone="UTC")
        chart = render(result, "chart", width=80)
        assert "Timezone: UTC" in chart
        assert "combined" in chart.lower()
        assert chart.index("2026-01-01") < chart.index("2026-01-02")
        assert "200 (partial)" in chart
        assert "100 (partial)" in chart
        assert "999" not in chart
        assert "█ input" in chart and "░ output" in chart
        days = [line for line in chart.splitlines() if line.startswith("2026-")]
        assert len(days) == 2
        bar = days[0].split(" | ")[1]
        assert abs(bar.count("█") - bar.count("░") * 2) <= 2
        assert bar == "█" * bar.count("█") + "░" * bar.count("░")
        assert "unavailable" in chart
        assert "0" in chart
        assert all(len(line) <= 80 for line in chart.splitlines())
        assert " | " not in render(result, "chart", width=10)
        assert "in 0" in days[1] and "out unavailable" in days[1]
        assert " | " not in days[1]
        assert "2026-01-03" not in chart


def test_daily_chart_handles_empty_results_and_diagnostics(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as store:
        result = report(store, group_by="day", zone="UTC")
        assert "No data" in render(result, "chart")
        source = write_records(tmp_path, [event("session.start", {"sessionId": "s"})])
        source.write_text(source.read_text() + "malformed\n")
        store.import_file("copilot", source)
        chart = render(report(store, group_by="day", zone="UTC"), "chart")
        assert "invalid_json (1)" in chart
        assert "unavailable" in chart
        with pytest.raises(ValueError, match="day"):
            render(report(store), "chart")


def test_stacked_chart_scales_daily_sums_and_keeps_small_segments_within_width():
    rows = [
        {
            "day": day,
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "sessions": 1,
            "input_tokens_sessions": int(incoming is not None),
            "output_tokens_sessions": int(outgoing is not None),
        }
        for day, incoming, outgoing in (
            ("2026-01-01", 100, 100),
            ("2026-01-02", 100, 0),
            ("2026-01-03", 0, 0),
            ("2026-01-04", None, 50),
            ("2026-01-05", 199, 1),
        )
    ]
    result = {"group_by": "day", "timezone": "UTC", "rows": rows, "diagnostics": []}
    chart = render(result, "chart", width=80)
    lines = [line for line in chart.splitlines() if line.startswith("2026-")]
    first, second = (line.split(" | ")[1] for line in lines[:2])
    assert abs(len(first) - len(second) * 2) <= 1
    assert abs(first.count("█") - first.count("░")) <= 1
    assert " | " not in lines[2]
    assert lines[3].split(" | ")[1].count("█") == 0
    assert "in unavailable" in lines[3]
    assert lines[4].endswith("░")
    assert len(lines[4].split(" | ")[1]) == len(first)
    for width in (10, 40, 80):
        rendered = render(result, "chart", width=width)
        data_lines = [line for line in rendered.splitlines() if line.startswith("2026-")]
        assert all(len(line) <= width for line in data_lines if " | " in line)
    narrow = render(result, "chart", width=38)
    assert " | " not in next(line for line in narrow.splitlines() if line.startswith("2026-01-01"))
    assert " | " not in render({**result, "rows": rows[2:3]}, "chart")


def test_chart_formats_large_counts_without_changing_machine_readable_values(tmp_path):
    source = write_records(
        tmp_path,
        [event("session.usage_record", {"usage": {"inputTokens": 1234567, "outputTokens": 12345}})],
    )
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        result = report(store, group_by="day", zone="UTC")
        chart = render(result, "chart", width=80)
        line = next(line for line in chart.splitlines() if line.startswith("2026-"))
        assert "in 1,234,567" in line
        assert "out 12,345" in line
        assert len(line) <= 80
        assert json.loads(render(result, "json"))["rows"][0]["input_tokens"] == 1234567
        assert next(csv.DictReader(io.StringIO(render(result, "csv"))))["output_tokens"] == "12345"


def test_parser_upgrade_reprocesses_unchanged_sources_and_clears_old_diagnostics(tmp_path):
    source = write_records(tmp_path, [{"type": "file-history-snapshot", "snapshot": {}}])
    identity = opaque("claude:" + str(source.resolve()))
    with Store(tmp_path / "metrics.duckdb") as store:
        store.connection.execute(
            "INSERT INTO sources VALUES (?, ?, current_timestamp)",
            [identity, hashlib.sha256(source.read_bytes()).hexdigest()],
        )
        store.connection.execute("INSERT INTO diagnostics VALUES (?, 'claude', 1, 'unsupported_schema')", [identity])
        assert store.import_file("claude", source) is True
        assert report(store)["diagnostics"] == []
        assert store.import_file("claude", source) is False


def test_import_is_persistent_idempotent_and_reconciles_growing_files(tmp_path):
    database = tmp_path / "metrics.duckdb"
    records = [event("session.start", {"sessionId": "session"}), usage("a", 100)]
    source = write_records(tmp_path, records)
    with Store(database) as store:
        assert store.import_file("copilot", source) is True
        assert store.import_file("copilot", source) is False
        wal = database.with_suffix(database.suffix + ".wal")
        if wal.exists():
            assert os.stat(wal).st_mode & 0o077 == 0
    with Store(database) as store:
        assert report(store)["rows"][0]["input_tokens"] == 100
        write_records(tmp_path, records + [usage("b", 50)])
        assert store.import_file("copilot", source) is True
        row = report(store)["rows"][0]
        assert row["input_tokens"] == 150
        assert row["sessions"] == 1
        assert row["output_tokens"] is None
        assert row["approval_prompts"] is None
    assert os.stat(database).st_mode & 0o777 == 0o600


def test_duplicate_session_exports_are_not_counted_twice_or_removed_with_missing_source(tmp_path):
    source = write_records(tmp_path, [event("session.start", {"sessionId": "session"}), usage("a", 10)])
    copy = tmp_path / "copy.jsonl"
    copy.write_bytes(source.read_bytes())
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        store.import_file("copilot", copy)
        source.unlink()
        assert report(store)["rows"][0]["input_tokens"] == 10


def test_report_preserves_unknowns_and_coverage_and_never_stores_transcripts(tmp_path):
    records = [
        event("session.start", {"sessionId": "one"}),
        event("user.message", {"content": "SECRET TRANSCRIPT"}, "user"),
        usage("a", 10),
        event("session.start", {"sessionId": "two"}, "second"),
    ]
    source = write_records(tmp_path, records)
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        result = report(store)
        row = result["rows"][0]
        assert row["sessions"] == 2
        assert row["usage_sessions"] == 1
        assert row["input_tokens"] == 10
        assert "SECRET TRANSCRIPT" not in repr(store.connection.execute("SELECT * FROM events").fetchall())
        assert row["clarification_requests"] is None
        source.write_text("invalid\n")
        store.import_file("copilot", source)
        assert report(store)["diagnostics"] == [{"provider": "copilot", "code": "invalid_json", "count": 1}]


def test_failed_transaction_restores_previous_import(tmp_path, monkeypatch):
    source = write_records(tmp_path, [usage("a", 10)])
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        source.write_text(source.read_text() + json.dumps(usage("b", 20)) + "\n")

        def fail(*args):
            raise duckdb.Error("synthetic insertion failure")

        monkeypatch.setattr(store, "_insert", fail)
        with pytest.raises(duckdb.Error):
            store.import_file("copilot", source)
        assert report(store)["rows"][0]["input_tokens"] == 10


def test_date_filters_and_exports_are_deterministic(tmp_path):
    source = write_records(
        tmp_path,
        [usage("a", 10, "2026-01-01T23:59:59Z", 'model,"quoted"'), usage("b", 20, "2026-01-02T00:00:00Z")],
    )
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        result = report(store, start="2026-01-01", end="2026-01-01", zone="UTC", group_by="model")
        assert len(result["rows"]) == 1
        assert result["rows"][0]["input_tokens"] == 10
        assert json.loads(render(result, "json")) == result
        exported = list(csv.DictReader(io.StringIO(render(result, "csv"))))
        assert exported[0]["model"] == 'model,"quoted"'
        assert exported[0]["output_tokens"] == ""
        assert "unavailable" in render(result, "table")
        with pytest.raises(ValueError):
            report(store, start="2026-02-02", end="2026-01-01")


def test_dst_local_day_and_child_session_counts(tmp_path):
    source = write_records(
        tmp_path,
        [
            {
                "type": "assistant",
                "uuid": "a",
                "sessionId": "parent",
                "agentId": "child",
                "isSidechain": True,
                "timestamp": "2026-03-09T03:59:59Z",
                "message": {"id": "m", "usage": {"input_tokens": 10}},
            }
        ],
    )
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("claude", source)
        result = report(store, start="2026-03-08", end="2026-03-08", zone="America/New_York", group_by="day")
        assert result["rows"][0]["day"] == "2026-03-08"
        assert result["rows"][0]["child_sessions"] == 1
        assert result["rows"][0]["top_level_sessions"] == 0


def test_daily_elapsed_spans_do_not_repeat_the_whole_session_span(tmp_path):
    source = write_records(
        tmp_path,
        [
            event("session.start", {"sessionId": "s"}, "start", "2026-01-01T23:00:00Z"),
            usage("a", 10, "2026-01-01T23:30:00Z"),
            usage("b", 20, "2026-01-02T01:00:00Z"),
            usage("c", 30, "2026-01-02T01:30:00Z"),
        ],
    )
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", source)
        assert [row["elapsed_seconds"] for row in report(store, group_by="day", zone="UTC")["rows"]] == [1800, 1800]


def test_empty_csv_still_exposes_its_schema(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as store:
        exported = render(report(store, group_by="model"), "csv")
        reader = csv.DictReader(io.StringIO(exported))
        assert reader.fieldnames is not None
        assert "model" in reader.fieldnames
        assert "input_tokens" in reader.fieldnames
        assert list(reader) == []


def test_project_report_breaks_down_models_and_separates_same_named_projects(tmp_path):
    records = []
    for session, cwd, tokens, model in [
        ("one", "/work/a/app", 10, "model-a"),
        ("two", "/work/a/app", 20, "model-b"),
        ("three", "/work/b/app", 30, "model-a"),
        ("four", None, 5, "model-a"),
    ]:
        records.extend(
            [
                event("session.start", {"sessionId": session, "context": {"cwd": cwd}}, session),
                usage(session + "-usage", tokens, model=model),
            ]
        )
    with Store(tmp_path / "metrics.duckdb") as store:
        store.import_file("copilot", write_records(tmp_path, records))
        result = report(store, group_by="project")
        rows = result["rows"]
        assert len(rows) == 4
        assert sorted(row["input_tokens"] for row in rows) == [5, 10, 20, 30]
        assert len({row["project_id"] for row in rows if row["project"] == "app"}) == 2
        assert {row["model"] for row in rows} == {"model-a", "model-b"}
        assert any(row["project"] == "unknown" for row in rows)
        assert "/work/" not in render(result, "json")
        assert "project_id" in next(csv.reader(io.StringIO(render(result, "csv"))))
        filtered = report(store, group_by="project", start="2026-01-02", zone="UTC")
        assert filtered["rows"] == []


def test_existing_database_migrates_without_losing_usage(tmp_path):
    database = tmp_path / "legacy.duckdb"
    with Store(database) as store:
        store.import_file("copilot", write_records(tmp_path, [usage("a", 10)]))
        store.connection.execute("DROP VIEW normalized_events")
        store.connection.execute("ALTER TABLE events DROP COLUMN project")
        store.connection.execute("ALTER TABLE events DROP COLUMN project_id")
    with Store(database) as store:
        row = report(store, group_by="project")["rows"][0]
        assert row["input_tokens"] == 10
        assert row["project"] == "unknown"
