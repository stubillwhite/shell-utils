import csv
import io
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from copilot_metrics.collectors.interruptions import OBSERVATION_KINDS
from copilot_metrics.models import TOKEN_FIELDS
from copilot_metrics.storage import Store

COUNT_KINDS = ("tool_call", "tool_error", "approval_request", "clarification_request", *OBSERVATION_KINDS)


def local_zone() -> str:
    location = str(Path("/etc/localtime").resolve())
    return os.environ.get("TZ") or (location.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in location else "UTC")


def selected_events(
    store: Store,
    *,
    start: str | None,
    end: str | None,
    provider: str | None,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    lower = datetime.combine(date.fromisoformat(start), time.min, tz) if start else None
    upper = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, tz) if end else None
    if lower and upper and lower >= upper:
        raise ValueError("invalid_date_range")
    cursor = store.connection.execute("SELECT * FROM normalized_events ORDER BY timestamp, identity")
    columns = [column[0] for column in cursor.description]
    all_events = [
        {**dict(zip(columns, row, strict=True)), "timestamp": datetime.fromisoformat(row[columns.index("timestamp")])}
        for row in cursor.fetchall()
    ]
    return [
        event
        for event in all_events
        if (provider is None or event["provider"] == provider)
        and (lower is None or event["timestamp"] >= lower)
        and (upper is None or event["timestamp"] < upper)
    ]


def report(
    store: Store,
    *,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    group_by: str = "provider",
    zone: str | None = None,
) -> dict[str, Any]:
    if group_by not in ("provider", "day", "model", "session", "project"):
        raise ValueError("invalid_group")
    zone = zone or local_zone()
    tz = ZoneInfo(zone)
    selected = selected_events(store, start=start, end=end, provider=provider, tz=tz)
    modeled_sessions = {event["session"] for event in selected if event["kind"] != "session"}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in selected:
        if group_by in ("model", "project") and event["kind"] == "session" and event["session"] in modeled_sessions:
            continue
        value = {
            "provider": event["provider"],
            "model": event["model"] or "unknown",
            "session": event["session"],
            "day": event["timestamp"].astimezone(tz).date().isoformat(),
            "project": event.get("project_id") or "unknown",
        }[group_by]
        model = (event["model"] or "unknown") if group_by == "project" else ""
        groups.setdefault((event["provider"], value, model), []).append(event)
    rows = []
    for (name, value, model), events in sorted(groups.items()):
        sessions = {event["session"] for event in events}
        children = {event["session"] for event in events if event["child"]}
        usage = [event for event in events if event["kind"] == "usage"]
        row: dict[str, Any] = {
            "provider": name,
            group_by: value,
            "sessions": len(sessions),
            "top_level_sessions": len(sessions - children),
            "child_sessions": len(children),
            "usage_sessions": len({event["session"] for event in usage}),
        }
        if group_by == "project":
            row.update(project=events[0].get("project") or "unknown", project_id=value, model=model)
        duration_events = (
            events
            if group_by in ("day", "project")
            else [event for event in selected if event["session"] in sessions and event["provider"] == name]
        )
        row["elapsed_seconds"] = sum(
            (
                max(event["timestamp"] for event in duration_events if event["session"] == session)
                - min(event["timestamp"] for event in duration_events if event["session"] == session)
            ).total_seconds()
            for session in sessions
        )
        for field in TOKEN_FIELDS:
            values = [event[field] for event in usage if event[field] is not None]
            row[field] = sum(values) if values else None
            row[field + "_sessions"] = len({event["session"] for event in usage if event[field] is not None})
        for metric in COUNT_KINDS:
            observed = [event for event in events if event["kind"] == metric]
            row[metric + "s"] = len(observed) if observed else None
            row[metric + "_sessions"] = len({event["session"] for event in observed})
        row["approval_prompts"] = None
        row["human_wait_seconds"] = None
        rows.append(row)
    diagnostics = store.connection.execute(
        "SELECT provider, code, count(*) FROM diagnostics "
        "WHERE (? IS NULL OR provider = ?) GROUP BY provider, code ORDER BY provider, code",
        [provider, provider],
    ).fetchall()
    return {
        "timezone": zone,
        "from": start,
        "to": end,
        "group_by": group_by,
        "columns": list(
            dict.fromkeys(
                [
                    "provider",
                    group_by,
                    *(["project_id", "model"] if group_by == "project" else []),
                    "sessions",
                    "top_level_sessions",
                    "child_sessions",
                    "usage_sessions",
                    "elapsed_seconds",
                    *[column for field in TOKEN_FIELDS for column in (field, field + "_sessions")],
                    *[column for field in COUNT_KINDS for column in (field + "s", field + "_sessions")],
                    "approval_prompts",
                    "human_wait_seconds",
                ]
            )
        ),
        "notes": [
            "Provider token categories differ; cache and reasoning fields may be subsets. No combined token total.",
            "Request counts are inferred interruptions, not confirmed user-visible prompts.",
            "Null means unavailable/unobserved, not zero. Coverage counts show sessions with observations.",
            "Elapsed seconds sum recorded session spans, not active time; spans are clipped to selected events.",
            "Diagnostics cover all stored imports for the selected provider, irrespective of date filters.",
            "Codex schemas are fixture-validated only; Codex model, approval and tool-error metrics are unavailable.",
            "Projects use recorded working directories, not inferred Git roots; reimport to populate older records.",
            "Hook checks can resolve automatically; notifications are attention signals, not complete prompt counts. "
            "Claude notifications are delayed and may miss quick responses. "
            "Elicitation outcomes cover Claude MCP responses only, not all questions or approvals. "
            "Human wait time is unavailable without correlated prompt-display and resolution events. "
            "Recorder timestamps are hook receipt times; repeated deliveries without stable IDs count separately. "
            "Hook observations have no project attribution and are never added to historical request counts.",
        ],
        "diagnostics": [{"provider": row[0], "code": row[1], "count": row[2]} for row in diagnostics],
        "rows": rows,
    }


def stacked_bar(*, incoming: int, outgoing: int, width: int, maximum: int) -> str:
    positive = int(incoming > 0) + int(outgoing > 0)
    if not positive or width < positive:
        return ""
    total = incoming + outgoing
    length = max(positive, total * width // maximum)
    input_length = min(length - int(outgoing > 0), max(int(incoming > 0), incoming * length // total))
    return "█" * input_length + "░" * (length - input_length)


def daily_series(result: dict[str, Any]) -> list[tuple[str, list[tuple[str, int | None, bool]]]]:
    if result["group_by"] != "day":
        raise ValueError("chart requires --group-by day")
    rows = result["rows"]
    return [
        (
            day,
            [
                (
                    label,
                    sum(values) if values else None,
                    any(row[field + "_sessions"] < row["sessions"] for row in daily),
                )
                for label, field in (("in", "input_tokens"), ("out", "output_tokens"))
                for values in ([row[field] for row in daily if row[field] is not None],)
            ],
        )
        for day in sorted({row["day"] for row in rows})
        for daily in ([row for row in rows if row["day"] == day],)
    ]


def daily_chart(result: dict[str, Any], *, width: int) -> str:
    entries = daily_series(result)
    labels = [
        day
        + "  "
        + "  ".join(
            f"{label} {format(value, ',') if value is not None else 'unavailable'}"
            + (" (partial)" if value is not None and partial else "")
            for label, value, partial in categories
        )
        for day, categories in entries
    ]
    label_width = max(map(len, labels), default=0)
    bar_width = max(0, width - label_width - 3)
    totals = [(categories[0][1] or 0, categories[1][1] or 0) for _, categories in entries]
    maximum = max((sum(values) for values in totals), default=0)
    lines = [
        "Timezone: " + result["timezone"],
        "Daily tokens: combined providers (reported input/output only).",
        "Legend: █ input  ░ output; bar length scales by input + output.",
        "Cache/reasoning fields are not added; provider categories differ.",
        "Partial = some sessions lack observations; unavailable is not zero.",
        "Observation coverage does not guarantee complete history.",
    ]
    lines.extend(
        (
            label.ljust(label_width) + " | " + bar
            if (bar := stacked_bar(incoming=incoming, outgoing=outgoing, width=bar_width, maximum=maximum))
            else label
        )
        for label, (incoming, outgoing) in zip(labels, totals, strict=True)
    )
    if not entries:
        lines.append("No data for the selected filters.")
    lines.extend(f'{item["provider"]}: {item["code"]} ({item["count"]})' for item in result["diagnostics"])
    return "\n".join(lines)


def render(result: dict[str, Any], format_name: str, *, width: int = 80) -> str:
    if format_name == "chart":
        return daily_chart(result, width=width)
    if format_name == "json":
        return json.dumps(result, indent=2, allow_nan=False)
    rows = result["rows"]
    if format_name == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=result["columns"])
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
    if format_name != "table":
        raise ValueError("invalid_format")
    columns = result["columns"]
    lines = ["Timezone: " + result["timezone"], "\t".join(columns)]
    lines.extend("\t".join("unavailable" if row[key] is None else str(row[key]) for key in columns) for row in rows)
    lines.extend(result["notes"])
    lines.extend(f'{item["provider"]}: {item["code"]} ({item["count"]})' for item in result.get("diagnostics", []))
    return "\n".join(lines)
