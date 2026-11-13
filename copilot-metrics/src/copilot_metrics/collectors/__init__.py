import hashlib
import json
import posixpath
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot_metrics.models import (
    PROVIDERS,
    TOKEN_FIELDS,
    Collection,
    Diagnostic,
    Metric,
)

COLLECTOR_REVISION = "3"


def object_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def usage_values(usage: dict[str, Any], names: tuple[str, ...]) -> dict[str, int | None]:
    values = tuple(usage.get(name) for name in names)
    if any(
        value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0) for value in values
    ):
        raise ValueError("invalid_usage")
    if not any(value is not None for value in values):
        raise ValueError("usage_unavailable")
    return dict(zip(TOKEN_FIELDS, values, strict=True))


def collect(provider: str, path: Path) -> Collection:
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    events: dict[tuple[str, str, str], Metric] = {}
    diagnostics: list[Diagnostic] = []
    ignored_metadata = False
    session = opaque(str(path.resolve()))
    child = "subagents" in path.parts
    previous: dict[str, int | None] = {}
    projects: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8") as source:
        lines = source.readlines()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            code = "truncated_tail" if number == len(lines) and not line.endswith("\n") else "invalid_json"
            diagnostics.append(Diagnostic(number, code))
            continue
        if not isinstance(record, dict):
            diagnostics.append(Diagnostic(number, "unsupported_schema"))
            continue
        kind = record.get("type")
        if provider == "claude" and kind == "file-history-snapshot" and isinstance(record.get("snapshot"), dict):
            ignored_metadata = True
            continue
        data = object_value(record.get("data"))
        payload = object_value(record.get("payload"))
        message = object_value(record.get("message"))
        if provider == "copilot" and kind == "session.start" and isinstance(data.get("sessionId"), str):
            session = opaque(data["sessionId"])
        if provider == "codex" and kind == "session_meta":
            session = opaque(str(payload.get("id", session)))
            child = isinstance(payload.get("source"), dict) and "subagent" in payload["source"]
        if provider == "claude" and isinstance(record.get("sessionId"), str):
            child = "subagents" in path.parts or bool(record.get("isSidechain"))
            agent = str(record.get("agentId") or path.stem) if child else ""
            session = opaque(record["sessionId"] + ":" + agent)
        valid_shape = (
            (provider == "copilot" and isinstance(record.get("data"), dict))
            or (provider == "codex" and isinstance(record.get("payload"), dict))
            or (provider == "claude" and isinstance(kind, str) and ("sessionId" in record or message))
        )
        if not valid_shape:
            diagnostics.append(Diagnostic(number, "unsupported_schema"))
            continue
        cwd = (
            object_value(data.get("context")).get("cwd")
            if provider == "copilot" and kind == "session.start"
            else (
                payload.get("cwd")
                if provider == "codex" and kind in ("session_meta", "turn_context")
                else record.get("cwd") if provider == "claude" else None
            )
        )
        if isinstance(cwd, str) and posixpath.isabs(cwd):
            normalized = posixpath.normpath(cwd)
            projects[session] = (opaque(normalized), posixpath.basename(normalized) or "/")
        elif provider == "copilot" and kind == "session.start":
            projects.pop(session, None)
        project_id, project = projects.get(session, (None, None))
        raw_timestamp = record.get("timestamp")
        if raw_timestamp is None:
            if kind in ("session.usage_record", "assistant", "event_msg", "response_item"):
                diagnostics.append(Diagnostic(number, "missing_timestamp"))
            continue
        try:
            instant = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError
            timestamp = instant.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except ValueError:
            diagnostics.append(Diagnostic(number, "invalid_timestamp"))
            continue
        identity = str(record.get("id") or record.get("uuid") or opaque(line))

        def emit(
            metric_kind: str,
            key: str = identity,
            model: object = None,
            tool: object = None,
            **tokens: int | None,
        ) -> None:
            event_key = (session, opaque(key), metric_kind)
            existing = events.get(event_key)
            if existing is not None and metric_kind == "usage":
                tokens = {
                    field: (
                        max(values)
                        if (
                            values := [
                                value for value in (getattr(existing, field), tokens.get(field)) if value is not None
                            ]
                        )
                        else None
                    )
                    for field in TOKEN_FIELDS
                }
                model = model if isinstance(model, str) else existing.model
            metric = Metric(
                session=session,
                identity=opaque(key),
                kind=metric_kind,
                timestamp=timestamp,
                model=model if isinstance(model, str) else None,
                tool=tool if isinstance(tool, str) else None,
                child=child,
                project_id=project_id,
                project=project,
                **tokens,
            )
            events[event_key] = metric

        emit("session")
        try:
            if provider == "copilot":
                if kind == "session.usage_record":
                    usage = object_value(data.get("usage"))
                    emit(
                        "usage",
                        str(usage.get("apiCallId") or identity),
                        model=usage.get("model"),
                        **usage_values(
                            usage,
                            ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "reasoningTokens"),
                        ),
                    )
                if kind == "permission.requested":
                    emit("approval_request", str(data.get("requestId") or identity))
                if kind == "tool.execution_start":
                    tool = data.get("toolName")
                    key = str(data.get("toolCallId") or identity)
                    emit("tool_call", key, model=data.get("model"), tool=tool)
                    if tool == "ask_user":
                        emit("clarification_request", key)
                if kind == "tool.execution_complete" and data.get("success") is False:
                    emit("tool_error", str(data.get("toolCallId") or identity))
            elif provider == "claude":
                key = str(message.get("id") or identity)
                if kind == "assistant" and isinstance(message.get("usage"), dict):
                    emit(
                        "usage",
                        key,
                        model=message.get("model"),
                        **usage_values(
                            message["usage"],
                            (
                                "input_tokens",
                                "output_tokens",
                                "cache_read_input_tokens",
                                "cache_creation_input_tokens",
                                "reasoning_tokens",
                            ),
                        ),
                    )
                blocks = message.get("content")
                for block in blocks if isinstance(blocks, list) else []:
                    if not isinstance(block, dict):
                        continue
                    tool_key = str(block.get("id") or block.get("tool_use_id") or identity)
                    if block.get("type") == "tool_use":
                        emit("tool_call", tool_key, model=message.get("model"), tool=block.get("name"))
                        if block.get("name") == "AskUserQuestion":
                            emit("clarification_request", tool_key)
                    if block.get("type") == "tool_result" and block.get("is_error") is True:
                        emit("tool_error", tool_key)
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = object_value(payload.get("info"))
                total = object_value(info.get("total_token_usage"))
                if not total:
                    if info:
                        raise ValueError("usage_unavailable")
                    continue
                current = usage_values(
                    total,
                    (
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "cache_creation_tokens",
                        "reasoning_output_tokens",
                    ),
                )
                if any(value is not None and value < (previous.get(field) or 0) for field, value in current.items()):
                    raise ValueError("cumulative_usage_decreased")
                delta = {
                    field: None if value is None else value - (previous.get(field) or 0)
                    for field, value in current.items()
                }
                previous = current
                if any(value for value in delta.values()):
                    emit("usage", identity, **delta)
            elif kind == "response_item" and payload.get("type") == "function_call":
                key = str(payload.get("call_id") or identity)
                emit("tool_call", key, tool=payload.get("name"))
                if payload.get("name") == "request_user_input":
                    emit("clarification_request", key)
        except ValueError as error:
            diagnostics.append(Diagnostic(number, str(error)))
    if not events and not diagnostics and not ignored_metadata:
        diagnostics.append(Diagnostic(0, "no_timestamped_events"))
    return Collection(tuple(events.values()), tuple(diagnostics))
