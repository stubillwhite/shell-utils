import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from copilot_metrics.app import execute, parser
from copilot_metrics.dashboard_launcher import launch


def test_dashboard_launch_is_packaged_local_dark_and_propagates_exit_status(tmp_path, monkeypatch):
    runner = Mock(return_value=subprocess.CompletedProcess([], 7))
    monkeypatch.setattr("copilot_metrics.dashboard_launcher.subprocess.run", runner)
    monkeypatch.chdir(tmp_path)
    assert launch(Path("chosen.duckdb")) == 7
    arguments = runner.call_args.args[0]
    assert arguments[:3] == [sys.executable, "-m", "streamlit"]
    assert arguments[3] == "run"
    assert Path(arguments[4]).is_absolute() and Path(arguments[4]).name == "dashboard.py"
    assert "--server.address=127.0.0.1" in arguments
    assert "--server.port=8502" in arguments
    assert "--browser.gatherUsageStats=false" in arguments
    assert "--theme.base=dark" in arguments
    assert "--client.toolbarMode=viewer" in arguments
    assert arguments[-3:] == ["--", "--db", str(tmp_path / "chosen.duckdb")]
    assert not runner.call_args.kwargs.get("shell", False)


def test_dashboard_cli_dispatch_does_not_change_existing_defaults(monkeypatch, tmp_path):
    launcher = Mock(return_value=4)
    monkeypatch.setattr("copilot_metrics.app.launch", launcher)
    args = parser().parse_args(["dashboard", "--db", str(tmp_path / "chosen.duckdb")])
    assert execute(args) == 4
    launcher.assert_called_once_with(args.db)
    for command in ("dashboard", "doctor", "import", "report"):
        assert parser().parse_args([command]).db == Path("metrics.duckdb")


def test_normal_cli_does_not_import_streamlit():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from copilot_metrics.app import parser; parser(); "
            "assert not any(name.startswith('streamlit') for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_interrupt_exits_without_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr("copilot_metrics.dashboard_launcher.subprocess.run", Mock(side_effect=KeyboardInterrupt))
    assert launch(tmp_path / "metrics.duckdb") == 130
