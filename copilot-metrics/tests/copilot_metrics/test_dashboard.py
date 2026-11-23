import csv
import io
import json
from unittest.mock import Mock

import streamlit as st
from streamlit.testing.v1 import AppTest

from copilot_metrics.storage import Store
from tests.copilot_metrics.dashboard_harness import dashboard_app
from tests.copilot_metrics.test_collectors import event, write_records
from tests.copilot_metrics.test_dashboard_data import (
    attention_database,
    dashboard_database,
)


def start(database, prices=None):
    prices_path = prices or database.parent / "prices.toml"
    return AppTest.from_function(dashboard_app, args=(str(database), str(prices_path)), default_timeout=15).run()


def test_dashboard_views_filters_and_refresh_preserve_read_only_evidence(dashboard_database, tmp_path):
    before = dashboard_database.read_bytes()
    app = start(dashboard_database)
    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "Attention signals",
        "Models & Projects",
        "Work patterns",
        "Sessions",
        "Data Quality",
    ]
    assert app.sidebar.selectbox(key="provider").value == "All"
    assert app.sidebar.checkbox(key="all_history").value is True
    assert any(metric.value == "30" for metric in app.metric)
    assert any("Diagnostics cover all stored" in caption.value for caption in app.caption)
    assert dashboard_database.read_bytes() == before
    app.sidebar.selectbox(key="provider").select("copilot").run()
    assert not app.exception
    assert not any(metric.value == "40" for metric in app.metric)
    app.sidebar.text_input(key="timezone").set_value("America/New_York")
    app.sidebar.checkbox(key="all_history").uncheck()
    app.run()
    app.sidebar.date_input(key="start").set_value("2026-01-01")
    app.sidebar.date_input(key="end").set_value("2026-01-01").run()
    assert not app.exception and any(metric.value == "30" for metric in app.metric)
    with Store(dashboard_database) as store:
        store.import_file(
            "copilot",
            write_records(
                tmp_path,
                [event("session.usage_record", {"usage": {"apiCallId": "new", "inputTokens": 99}})],
            ),
        )
    app.sidebar.button(key="refresh").click().run()
    assert not app.exception and any(metric.value == "99" for metric in app.metric)


def test_breakdown_selectors_do_not_change_summary_and_downloads_match_tables(dashboard_database, monkeypatch):
    download = Mock(wraps=st.download_button)
    charts = Mock(wraps=st.altair_chart)
    monkeypatch.setattr("copilot_metrics.dashboard.st.download_button", download)
    monkeypatch.setattr("copilot_metrics.dashboard.st.altair_chart", charts)
    app = start(dashboard_database)
    summaries = [metric.value for metric in app.metric]
    download.reset_mock()
    charts.reset_mock()
    app.selectbox(key="model_filter").select("model-a").run()
    assert [metric.value for metric in app.metric] == summaries
    model_table = next(
        frame.value for frame in app.dataframe if "model" in frame.value and "project" not in frame.value
    )
    assert set(model_table["model"]) == {"model-a"}
    downloads = app.get("download_button")
    assert len(downloads) >= 2
    assert any(button.proto.label == "Download models JSON" for button in downloads)
    exported = json.loads(
        next(call.args[1] for call in download.call_args_list if call.args[0] == "Download models JSON")
    )
    assert [row["model"] for row in exported["rows"]] == ["model-a"]
    assert exported["rows"][0]["input_tokens"] == model_table["input_tokens"][0]
    assert exported["rows"][0]["cached_input_tokens"] is None
    assert exported["view_filter"] == {"model": "model-a"}
    spec = charts.call_args_list[0].args[0].to_dict()
    plotted = spec["data"]["values"]
    assert sum(row["tokens"] for row in plotted if row["category"] == "Input" and row["tokens"] is not None) == 70
    assert sum(row["tokens"] for row in plotted if row["category"] == "Output" and row["tokens"] is not None) == 5
    assert any(row["tokens"] is None and row["coverage"] == "unavailable" for row in plotted)
    assert not app.exception


def test_empty_missing_invalid_filters_and_database_failures_are_explicit(tmp_path, dashboard_database):
    missing = tmp_path / "missing" / "metrics.duckdb"
    app = start(missing)
    assert app.error and not app.exception
    assert not missing.parent.exists()
    empty = tmp_path / "empty.duckdb"
    with Store(empty):
        pass
    app = start(empty)
    assert app.info and not app.exception
    app = start(dashboard_database)
    app.sidebar.text_input(key="timezone").set_value("not/a-zone").run()
    assert app.error and not app.exception
    app.sidebar.text_input(key="timezone").set_value("").run()
    assert app.error and not app.exception
    app.sidebar.text_input(key="timezone").set_value("UTC")
    app.sidebar.checkbox(key="all_history").uncheck()
    app.run()
    app.sidebar.date_input(key="start").set_value("2027-01-02")
    app.sidebar.date_input(key="end").set_value("2027-01-01").run()
    assert app.error and not app.exception
    app.sidebar.date_input(key="end").set_value("2027-01-03").run()
    assert app.info and not app.exception
    assert any("invalid_json" in str(frame.value) for frame in app.dataframe)
    broken = tmp_path / "broken.duckdb"
    broken.write_text(json.dumps({"not": "a database"}))
    app = start(broken)
    assert app.error and not app.exception


def test_attention_tab_trends_session_downloads_and_provider_limits(attention_database, monkeypatch):
    download = Mock(wraps=st.download_button)
    charts = Mock(wraps=st.altair_chart)
    monkeypatch.setattr("copilot_metrics.dashboard.st.download_button", download)
    monkeypatch.setattr("copilot_metrics.dashboard.st.altair_chart", charts)
    before = attention_database.read_bytes()
    app = start(attention_database)
    assert not app.exception
    attention = app.tabs[1]
    assert attention.label == "Attention signals"
    assert app.selectbox(key="attention_category").value == "approval_notification"
    assert any(metric.label == "Approval notifications" and metric.value == "2" for metric in attention.metric)
    assert any(metric.label == "Guidance notifications" and metric.value == "1" for metric in attention.metric)
    specs = [call.args[0].to_dict() for call in charts.call_args_list]
    trend = next(
        spec for spec in specs if spec.get("encoding", {}).get("y", {}).get("field") == "approval_notifications"
    )
    assert trend["mark"]["type"] == "bar"
    assert {row["day"] for row in trend["data"]["values"]} == {"2026-01-02", "2026-01-04"}
    assert {row["provider"] for row in trend["data"]["values"]} == {"copilot"}
    exported = json.loads(
        next(call.args[1] for call in download.call_args_list if call.kwargs.get("key") == "attention_sessions_json")
    )
    table = next(frame.value for frame in attention.dataframe if "session" in frame.value)
    assert table.to_dict(orient="records") == exported["rows"]
    exported_csv = next(
        call.args[1] for call in download.call_args_list if call.kwargs.get("key") == "attention_sessions_csv"
    )
    assert list(csv.DictReader(io.StringIO(exported_csv))) == [
        {key: str(value) for key, value in row.items()} for row in exported["rows"]
    ]
    assert exported["rows"][0]["approval_notifications"] == 2
    assert exported["view_filter"] == {"attention_category": "approval_notification"}
    assert any("Human wait time is unavailable" in caption.value for caption in attention.caption)
    app.sidebar.selectbox(key="provider").select("codex").run()
    assert not app.exception
    assert any("No observations" in item.value for item in app.tabs[1].info)
    assert all(metric.value == "Unavailable" for metric in app.tabs[1].metric)
    app.selectbox(key="attention_category").select("permission_check").run()
    assert not app.exception
    assert any(
        "permission_checks" in frame.value and frame.value["permission_checks"].tolist() == [1]
        for frame in app.tabs[1].dataframe
    )
    assert attention_database.read_bytes() == before
    source = attention_database.with_name("codex-hooks.jsonl")
    observation = json.loads(source.read_text().splitlines()[0])
    refreshed = source.with_name("codex-refresh.jsonl")
    refreshed.write_text(json.dumps({**observation, "identity": "a" * 64}))
    with Store(attention_database) as store:
        store.import_file("codex", refreshed, interruptions=True)
    app.sidebar.button(key="refresh").click().run()
    assert not app.exception
    assert any(
        "permission_checks" in frame.value and frame.value["permission_checks"].tolist() == [2]
        for frame in app.tabs[1].dataframe
    )


def test_attention_historical_only_state(dashboard_database):
    source = dashboard_database.with_name("historical-requests.jsonl")
    source.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                event("session.start", {"sessionId": "one"}),
                event("permission.requested", {"requestId": "approval"}),
            )
        )
    )
    with Store(dashboard_database) as store:
        store.import_file("copilot", source)
    historical = start(dashboard_database)
    assert any("No imported hook observations" in item.value for item in historical.tabs[1].info)
    assert any("README" in item.value for item in historical.tabs[1].markdown)
    historical.selectbox(key="attention_category").select("approval_request").run()
    assert not historical.exception
    assert any(
        "approval_requests" in frame.value and frame.value["approval_requests"].tolist() == [1]
        for frame in historical.tabs[1].dataframe
    )


def test_attention_filtered_empty_states(attention_database):
    app = start(attention_database)
    app.sidebar.selectbox(key="provider").select("copilot")
    app.sidebar.text_input(key="timezone").set_value("America/New_York")
    app.sidebar.checkbox(key="all_history").uncheck()
    app.run()
    app.sidebar.date_input(key="start").set_value("2026-01-01")
    app.sidebar.date_input(key="end").set_value("2026-01-01").run()
    assert any(metric.label == "Approval notifications" and metric.value == "1" for metric in app.tabs[1].metric)
    app.sidebar.date_input(key="start").set_value("2027-01-01")
    app.sidebar.date_input(key="end").set_value("2027-01-01").run()
    assert not app.exception
    assert any("No recorded sessions" in item.value for item in app.tabs[1].info)


def work_pattern_database(tmp_path):
    database = tmp_path / "work.duckdb"
    records = [
        event("session.start", {"sessionId": "s"}),
        event("tool.execution_start", {"toolName": "bash", "toolCallId": "a"}, "start"),
        event("tool.execution_complete", {"toolCallId": "a", "success": False}, "done"),
        event(
            "session.usage_record",
            {
                "usage": {
                    "apiCallId": "u",
                    "model": "m",
                    "inputTokens": 1_000_000,
                    "outputTokens": 0,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                }
            },
            "usage",
        ),
    ]
    with Store(database) as store:
        store.import_file("copilot", write_records(tmp_path, records))
    return database


def frame_with(app, column):
    return next(frame.value for frame in app.dataframe if column in frame.value)


def test_work_patterns_tab_shows_tool_errors_and_leaves_cost_unavailable_without_prices(tmp_path):
    app = start(work_pattern_database(tmp_path))
    assert not app.exception
    tools = frame_with(app, "error_rate")
    assert list(tools["tool"]) == ["bash"] and list(tools["error_rate"]) == [1.0]
    assert frame_with(app, "estimated_cost_usd")["estimated_cost_usd"].isna().all()
    assert any("estimate" in caption.value.lower() for caption in app.caption)


def test_work_patterns_tab_prices_usage_from_the_prices_file_and_downloads_match(tmp_path):
    database = work_pattern_database(tmp_path)
    prices = tmp_path / "custom-prices.toml"
    prices.write_text('[models."m"]\ninput = 3.0\noutput = 0.0\ncache_read = 0.0\ncache_write = 0.0\n')
    app = start(database, prices)
    assert not app.exception
    assert list(frame_with(app, "estimated_cost_usd")["estimated_cost_usd"]) == [3.0]
    assert {button.proto.label for button in app.get("download_button")} >= {
        "Download tool profile CSV",
        "Download estimated cost JSON",
    }


def test_invalid_prices_file_is_reported_without_hiding_other_tabs(tmp_path):
    database = work_pattern_database(tmp_path)
    prices = tmp_path / "bad.toml"
    prices.write_text('[models."m"]\ninput = -1\n')
    app = start(database, prices)
    assert not app.exception
    assert any("prices" in error.value.lower() for error in app.error)
    assert frame_with(app, "error_rate") is not None


def priced_app(tmp_path):
    prices = tmp_path / "custom-prices.toml"
    prices.write_text('[models."m"]\ninput = 3.0\noutput = 0.0\ncache_read = 0.0\ncache_write = 0.0\n')
    return start(work_pattern_database(tmp_path), prices)


def test_overview_headline_total_is_unavailable_without_prices_and_a_dollar_amount_with_them(tmp_path):
    unpriced = start(work_pattern_database(tmp_path))
    assert any(metric.label == "Estimated cost (USD)" and metric.value == "Unavailable" for metric in unpriced.metric)
    priced = priced_app(tmp_path)
    assert not priced.exception
    assert any(metric.label == "Estimated cost (USD)" and metric.value == "$3.00" for metric in priced.metric)


def test_headline_total_says_when_some_usage_could_not_be_priced(tmp_path):
    database = tmp_path / "partial.duckdb"
    usage = {"inputTokens": 1_000_000, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0}
    records = [
        event("session.start", {"sessionId": "s"}),
        event("session.usage_record", {"usage": {"apiCallId": "a", "model": "m", **usage}}, "a"),
        event("session.usage_record", {"usage": {"apiCallId": "b", "model": "mystery", **usage}}, "b"),
    ]
    with Store(database) as store:
        store.import_file("copilot", write_records(tmp_path, records))
    prices = tmp_path / "prices.toml"
    prices.write_text('[models."m"]\ninput = 3.0\noutput = 0.0\ncache_read = 0.0\ncache_write = 0.0\n')
    app = start(database, prices)
    assert any("lower bound" in caption.value.lower() and "mystery" in caption.value for caption in app.caption)


def test_daily_cost_chart_plots_priced_days_and_cost_by_project_has_downloads(tmp_path, monkeypatch):
    charts = Mock(wraps=st.altair_chart)
    monkeypatch.setattr("copilot_metrics.dashboard.st.altair_chart", charts)
    app = priced_app(tmp_path)
    assert not app.exception
    assert any('"estimated_cost_usd"' in json.dumps(call.args[0].to_dict()) for call in charts.call_args_list)
    labels = {button.proto.label for button in app.get("download_button")}
    assert {"Download cost by project CSV", "Download cost by project JSON"} <= labels
