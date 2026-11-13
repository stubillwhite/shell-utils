import fcntl
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot_metrics.collectors import opaque
from copilot_metrics.models import PROVIDERS
from copilot_metrics.storage import private_creation


def hook_kind(provider: str, event: str, payload: dict[str, Any]) -> str | None:
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    if event == "PermissionRequest":
        return "permission_check"
    if event == "PreToolUse":
        tool = payload.get("toolName") if provider == "copilot" else payload.get("tool_name")
        expected = {"copilot": "ask_user", "claude": "AskUserQuestion", "codex": "request_user_input"}[provider]
        return "clarification_check" if tool == expected else None
    if event == "Notification" and provider in ("copilot", "claude"):
        notification = payload.get("notification_type")
        if not isinstance(notification, str):
            raise ValueError("invalid_notification")
        return {
            "permission_prompt": "approval_notification",
            "elicitation_dialog": "guidance_notification",
            "elicitation_url_dialog": "guidance_notification",
        }.get(notification)
    if event == "ElicitationResult" and provider == "claude":
        action = payload.get("action")
        if action not in ("accept", "decline", "cancel"):
            raise ValueError("invalid_action")
        return f"elicitation_{action}"
    raise ValueError("unsupported_hook")


def record_hook(*, provider: str, event: str, payload: object, output: Path) -> None:
    if not isinstance(payload, dict):
        raise ValueError("invalid_hook")
    session = payload.get("session_id") or (payload.get("sessionId") if provider == "copilot" else None)
    if not isinstance(session, str) or not session:
        raise ValueError("missing_session")
    kind = hook_kind(provider, event, payload)
    if kind is None:
        return
    agent = payload.get("agent_id") or payload.get("agentId")
    if agent is not None and not isinstance(agent, str):
        raise ValueError("invalid_agent")
    item = {
        "version": 1,
        "provider": provider,
        "session": opaque(session + ":" + (agent or "") if provider == "claude" else session),
        "identity": opaque(str(uuid4())),
        "kind": kind,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "child": bool(agent),
    }
    with private_creation():
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as target:
        fcntl.flock(target, fcntl.LOCK_EX)
        os.fchmod(target.fileno(), 0o600)
        target.write(json.dumps(item, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())


def hook_config(*, provider: str, output: Path) -> dict[str, Any]:
    events: tuple[str, ...] = ("PermissionRequest", "PreToolUse")
    if provider in ("copilot", "claude"):
        events += ("Notification",)
    if provider == "claude":
        events += ("ElicitationResult",)
    hooks = {}
    for event in events:
        command = shlex.join(
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
                str(output.resolve()),
            ]
        )
        handler = {"type": "command", "command": command}
        name = event[0].lower() + event[1:] if provider == "copilot" else event
        matcher = (
            {"copilot": "ask_user", "claude": "AskUserQuestion", "codex": "request_user_input"}[provider]
            if event == "PreToolUse"
            else "permission_prompt|elicitation_dialog|elicitation_url_dialog" if event == "Notification" else None
        )
        entry: dict[str, Any] = handler if provider == "copilot" else {"hooks": [handler]}
        if matcher is not None:
            entry["matcher"] = matcher
        hooks[name] = [entry]
    return {"version": 1, "hooks": hooks} if provider == "copilot" else {"hooks": hooks}
