import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from copilot_metrics.reports import report
from copilot_metrics.storage import Store
from tests.test_cli import run


def record(path, provider, event, payload):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "copilot_metrics",
            "record-interruption",
            "--provider",
            provider,
            "--event",
            event,
            "--output",
            str(path),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("provider", "event", "fields", "kind"),
    [
        ("copilot", "Notification", {"notification_type": "permission_prompt"}, "approval_notification"),
        ("copilot", "Notification", {"notification_type": "elicitation_dialog"}, "guidance_notification"),
        ("claude", "Notification", {"notification_type": "permission_prompt"}, "approval_notification"),
        ("claude", "Notification", {"notification_type": "elicitation_url_dialog"}, "guidance_notification"),
        ("codex", "PermissionRequest", {}, "permission_check"),
        ("copilot", "PermissionRequest", {}, "permission_check"),
        ("claude", "PermissionRequest", {}, "permission_check"),
        ("copilot", "PreToolUse", {"toolName": "ask_user"}, "clarification_check"),
        ("claude", "PreToolUse", {"tool_name": "AskUserQuestion"}, "clarification_check"),
        ("codex", "PreToolUse", {"tool_name": "request_user_input"}, "clarification_check"),
        ("claude", "ElicitationResult", {"action": "accept"}, "elicitation_accept"),
        ("claude", "ElicitationResult", {"action": "decline"}, "elicitation_decline"),
        ("claude", "ElicitationResult", {"action": "cancel"}, "elicitation_cancel"),
    ],
)
def test_hooks_persist_only_allowlisted_observations(tmp_path, provider, event, fields, kind):
    path = tmp_path / "private" / "observations.jsonl"
    payload = {
        "session_id": "private-session",
        "sessionId": "private-session",
        "cwd": "/private/source",
        "message": "PRIVATE QUESTION",
        "tool_input": {"command": "PRIVATE COMMAND"},
        "content": {"answer": "PRIVATE ANSWER"},
        **fields,
    }
    result = record(path, provider, event, payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    saved = json.loads(path.read_text())
    assert set(saved) == {"version", "provider", "session", "identity", "kind", "timestamp", "child"}
    assert saved["kind"] == kind
    assert saved["provider"] == provider
    assert len(saved["session"]) == 64
    assert "private" not in path.read_text().lower()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700


def test_automatic_checks_and_idle_notifications_are_not_counted_as_prompts(tmp_path):
    path = tmp_path / "observations.jsonl"
    for provider in ("copilot", "claude", "codex"):
        assert record(path, provider, "PermissionRequest", {"session_id": "s"}).returncode == 0
    assert (
        record(path, "claude", "Notification", {"session_id": "s", "notification_type": "idle_prompt"}).returncode == 0
    )
    assert [json.loads(line)["kind"] for line in path.read_text().splitlines()] == ["permission_check"] * 3


def test_invalid_hooks_fail_without_persisting_or_echoing_payload(tmp_path):
    path = tmp_path / "observations.jsonl"
    for provider, event, payload in (
        ("claude", "Notification", {"message": "SECRET"}),
        ("codex", "Notification", {"session_id": "SECRET"}),
        ("claude", "ElicitationResult", {"session_id": "SECRET", "action": "unknown"}),
    ):
        result = record(path, provider, event, payload)
        assert result.returncode == 1
        assert "SECRET" not in result.stderr
    assert not path.exists()


@pytest.mark.parametrize("provider", ["copilot", "claude", "codex"])
def test_generated_configs_are_explicit_and_do_not_install_themselves(tmp_path, provider):
    destination = tmp_path / "observations with spaces.jsonl"
    result = run("interruption-hooks", "--provider", provider, "--output", destination)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    hooks = config["hooks"]
    if provider != "codex":
        assert ("notification" if provider == "copilot" else "Notification") in hooks
    assert ("permissionRequest" if provider == "copilot" else "PermissionRequest") in hooks
    assert not destination.exists()
    event = "permissionRequest" if provider == "copilot" else "PermissionRequest"
    entry = hooks[event][0]
    command = entry["command"] if provider == "copilot" else entry["hooks"][0]["command"]
    completed = subprocess.run(
        command, shell=True, input='{"session_id":"s"}', text=True, capture_output=True, cwd=tmp_path
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(destination.read_text())["kind"] == "permission_check"


@pytest.mark.parametrize(
    ("provider", "event", "fields", "column"),
    [
        ("copilot", "Notification", {"notification_type": "permission_prompt"}, "approval_notifications"),
        ("claude", "ElicitationResult", {"action": "cancel"}, "elicitation_cancels"),
        ("codex", "PermissionRequest", {}, "permission_checks"),
    ],
)
def test_recorded_events_import_repeatably_without_merging_with_historical_requests(
    tmp_path, provider, event, fields, column
):
    path = tmp_path / "observations.jsonl"
    assert record(path, provider, event, {"session_id": "s", **fields}).returncode == 0
    first = path.read_text()
    path.write_text(first + first + "malformed\n")
    database = tmp_path / "metrics.duckdb"
    arguments = ("import", "--provider", provider, "--source-root", path, "--interruptions", "--db", database)
    result = run(*arguments)
    assert result.returncode == 2, result.stderr
    assert "invalid_json" in result.stderr
    with Store(database, read_only=True) as store:
        row = report(store)["rows"][0]
        assert row[column] == 1
        assert row["approval_requests"] is None
        assert row["approval_prompts"] is None
        assert row["human_wait_seconds"] is None
    path.write_text(first)
    assert run(*arguments).returncode == 0
    unchanged = run(*arguments)
    assert unchanged.returncode == 0
    assert "0 changed" in unchanged.stdout
    exported = run("report", "--db", database, "--format", "json")
    assert json.loads(exported.stdout)["rows"][0][column] == 1


def test_import_rejects_non_normalized_records_and_other_providers(tmp_path):
    path = tmp_path / "observations.jsonl"
    assert record(path, "codex", "PermissionRequest", {"session_id": "s"}).returncode == 0
    valid = json.loads(path.read_text())
    path.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {**valid, "session": "unhashed"},
                {**valid, "timestamp": "2026-01-01"},
                {**valid, "kind": "approval_prompt"},
                {**valid, "child": "false"},
                valid,
            )
        )
    )
    result = run("import", "--provider", "copilot", "--interruptions", "--source-root", path, "--db", tmp_path / "db")
    assert result.returncode == 2
    with Store(tmp_path / "db", read_only=True) as store:
        assert report(store)["rows"] == []
    matching = run("import", "--provider", "codex", "--interruptions", "--source-root", path, "--db", tmp_path / "db")
    assert matching.returncode == 2
    with Store(tmp_path / "db", read_only=True) as store:
        assert report(store)["rows"][0]["permission_checks"] == 1
        assert store.connection.execute("SELECT count(*) FROM diagnostics").fetchone()[0] == 9


def test_parallel_hooks_append_complete_private_records_without_changing_parent_permissions(tmp_path):
    path = tmp_path / "observations.jsonl"
    os.chmod(tmp_path, 0o750)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: record(path, "codex", "PermissionRequest", {"session_id": "s"}), range(8))
        )
    assert all(result.returncode == 0 for result in results)
    observations = [json.loads(line) for line in path.read_text().splitlines()]
    assert len({item["identity"] for item in observations}) == 8
    assert os.stat(tmp_path).st_mode & 0o777 == 0o750
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_recorder_refuses_symlink_destinations(tmp_path):
    target = tmp_path / "target"
    target.write_text("unchanged")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert record(link, "codex", "PermissionRequest", {"session_id": "s"}).returncode == 1
    assert target.read_text() == "unchanged"
