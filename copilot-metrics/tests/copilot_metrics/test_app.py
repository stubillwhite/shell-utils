import json
import sys

import pytest

from copilot_metrics.app import main
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import event, write_records


def test_add_five():
    assert 3 + 5 == 8


def run_cli(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", ["copilot-metrics", *arguments])
    with pytest.raises(SystemExit) as exit_info:
        main()
    return exit_info.value.code, capsys.readouterr().out


def test_insights_sessions_honours_the_idle_threshold_option(tmp_path, monkeypatch, capsys):
    database = tmp_path / "metrics.duckdb"
    records = [
        event("session.start", {"sessionId": "s"}, "start", "2026-01-01T10:00:00Z"),
        event("tool.execution_start", {"toolName": "bash", "toolCallId": "a"}, "a", "2026-01-01T10:31:00Z"),
    ]
    with Store(database) as store:
        store.import_file("copilot", write_records(tmp_path, records))
    common = ["insights", "sessions", "--db", str(database), "--timezone", "UTC", "--format", "json"]
    code, default_output = run_cli(monkeypatch, capsys, *common)
    _, patient_output = run_cli(monkeypatch, capsys, *common, "--idle-minutes", "60")
    assert code == 0
    assert json.loads(default_output)["rows"][0]["active_minutes"] == 0.0
    assert json.loads(patient_output)["rows"][0]["active_minutes"] == 31.0
