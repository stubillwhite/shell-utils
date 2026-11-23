import json
import os
import subprocess
import sys
from pathlib import Path

from copilot_metrics.app import parser
from tests.copilot_metrics.test_collectors import event, write_records


def test_default_database_is_in_working_directory(tmp_path):
    for command in ("doctor", "import", "report"):
        assert parser().parse_args([command]).db == Path("metrics.duckdb")
    source = write_records(tmp_path, [event("session.start", {"sessionId": "s"})])
    imported = subprocess.run(
        [sys.executable, "-m", "copilot_metrics", "import", "--provider", "copilot", "--source-root", str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert (tmp_path / "metrics.duckdb").is_file()
    exported = subprocess.run(
        [sys.executable, "-m", "copilot_metrics", "report", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["rows"][0]["sessions"] == 1


def run(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "copilot_metrics", *map(str, arguments)], capture_output=True, text=True
    )


def test_cli_import_export_across_processes_and_no_transcripts(tmp_path):
    source = write_records(
        tmp_path,
        [
            event("session.start", {"sessionId": "s", "context": {"cwd": "/work/example"}}),
            event("session.usage_record", {"usage": {"apiCallId": "a", "inputTokens": 12, "model": "actual"}}, "usage"),
        ],
    )
    database = tmp_path / "metrics.duckdb"
    imported = run("import", "--provider", "copilot", "--source-root", tmp_path, "--db", database)
    assert imported.returncode == 0, imported.stderr
    exported = run("report", "--db", database, "--format", "json")
    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["rows"][0]["input_tokens"] == 12
    projects = run("report", "--db", database, "--group-by", "project", "--format", "json")
    assert projects.returncode == 0, projects.stderr
    project = json.loads(projects.stdout)["rows"][0]
    assert (project["project"], project["model"], project["input_tokens"]) == ("example", "actual", 12)
    source.write_text(source.read_text() + "malformed\n")
    partial = run("import", "--provider", "copilot", "--source-root", source, "--db", database)
    assert partial.returncode == 2
    assert "invalid_json" in partial.stderr
    assert "malformed" not in partial.stderr
    assert run("report", "--db", database, "--from", "not-a-date").returncode == 1


def test_missing_roots_database_and_invalid_cli_are_explicit(tmp_path):
    missing = tmp_path / "missing"
    assert run("report", "--db", missing).returncode == 1
    assert not missing.exists()
    result = run("doctor", "--provider", "codex", "--source-root", missing)
    assert result.returncode == 2
    assert "missing" in result.stdout
    assert run("import", "--source-root", tmp_path).returncode == 2
    assert run("--help").returncode == 0


def test_daily_chart_cli_filters_local_days_and_rejects_other_groupings(tmp_path):
    source = write_records(
        tmp_path,
        [
            event(
                "session.usage_record",
                {"usage": {"inputTokens": 120, "outputTokens": 30}},
                "a",
                "2026-01-02T01:00:00Z",
            ),
            event(
                "session.usage_record",
                {"usage": {"inputTokens": 999}},
                "b",
                "2026-01-03T01:00:00Z",
            ),
        ],
    )
    database = tmp_path / "chart.duckdb"
    assert run("import", "--provider", "copilot", "--source-root", source, "--db", database).returncode == 0
    claude = write_records(
        tmp_path,
        [
            {
                "type": "assistant",
                "sessionId": "claude",
                "timestamp": "2026-01-02T01:00:00Z",
                "message": {"id": "m", "usage": {"input_tokens": 777}},
            }
        ],
    )
    assert run("import", "--provider", "claude", "--source-root", claude, "--db", database).returncode == 0
    result = run(
        "report",
        "--db",
        database,
        "--group-by",
        "day",
        "--format",
        "chart",
        "--provider",
        "copilot",
        "--from",
        "2026-01-01",
        "--to",
        "2026-01-01",
        "--timezone",
        "America/New_York",
    )
    assert result.returncode == 0, result.stderr
    assert "2026-01-01" in result.stdout
    assert "120" in result.stdout and "30" in result.stdout
    assert "999" not in result.stdout
    assert "777" not in result.stdout
    assert "America/New_York" in result.stdout
    assert "█ input" in result.stdout and "░ output" in result.stdout
    days = [line for line in result.stdout.splitlines() if line.startswith("2026-")]
    assert len(days) == 1
    assert "in 120" in days[0] and "out 30" in days[0]
    assert "█" in days[0] and "░" in days[0]
    invalid = run("report", "--db", tmp_path / "nonexistent", "--format", "chart")
    assert invalid.returncode == 2
    assert "--group-by day" in invalid.stderr


def test_existing_parent_directory_permissions_are_preserved(tmp_path):
    os.chmod(tmp_path, 0o750)
    database = tmp_path / "metrics.duckdb"
    source = write_records(tmp_path, [event("session.start", {"sessionId": "s"})])
    assert run("import", "--provider", "copilot", "--source-root", source, "--db", database).returncode == 0
    assert os.stat(tmp_path).st_mode & 0o777 == 0o750


def import_tool_session(tmp_path):
    source = write_records(
        tmp_path,
        [
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
        ],
    )
    database = tmp_path / "metrics.duckdb"
    imported = run("import", "--db", database, "--provider", "copilot", "--source-root", source)
    assert imported.returncode == 0, imported.stderr
    return database


def test_insights_tools_json_reports_error_rate(tmp_path):
    database = import_tool_session(tmp_path)
    result = run("insights", "tools", "--db", database, "--format", "json", "--timezone", "UTC")
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)["rows"]
    assert (row["tool"], row["calls"], row["error_rate"]) == ("bash", 1, 1.0)


def test_insights_cost_uses_prices_file_and_leaves_unpriced_models_unavailable(tmp_path):
    database = import_tool_session(tmp_path)
    prices = tmp_path / "prices.toml"
    unpriced = run("insights", "cost", "--db", database, "--prices", prices, "--format", "json")
    assert json.loads(unpriced.stdout)["rows"][0]["estimated_cost_usd"] is None
    prices.write_text('[models."m"]\ninput = 3.0\noutput = 0.0\ncache_read = 0.0\ncache_write = 0.0\n')
    priced = run("insights", "cost", "--db", database, "--prices", prices, "--format", "json")
    assert json.loads(priced.stdout)["rows"][0]["estimated_cost_usd"] == 3.0


def test_insights_csv_renders_unavailable_as_empty_cells(tmp_path):
    database = import_tool_session(tmp_path)
    result = run("insights", "cache", "--db", database, "--format", "csv")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith("provider,model,")
