from pathlib import Path
from typing import Any

from copilot_metrics.insights import (
    cache_efficiency,
    cost_by_day,
    cost_by_model,
    cost_by_project,
    cost_total,
    project_friction,
    tool_profile,
)
from copilot_metrics.models import TOKEN_FIELDS
from copilot_metrics.prices import Prices
from copilot_metrics.reports import COUNT_KINDS, report
from copilot_metrics.storage import Store

ATTENTION_CATEGORIES = (
    ("approval_notification", "Approval notifications", "Hook checks and attention notifications"),
    ("guidance_notification", "Guidance notifications", "Hook checks and attention notifications"),
    ("permission_check", "Permission checks", "Hook checks and attention notifications"),
    ("clarification_check", "Clarification checks", "Hook checks and attention notifications"),
    ("approval_request", "Approval requests", "Historical inferred requests"),
    ("clarification_request", "Clarification requests", "Historical inferred requests"),
    ("elicitation_accept", "MCP elicitation accepts", "Claude MCP response outcomes"),
    ("elicitation_decline", "MCP elicitation declines", "Claude MCP response outcomes"),
    ("elicitation_cancel", "MCP elicitation cancels", "Claude MCP response outcomes"),
)


def attention_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "provider": row["provider"],
            "evidence": group,
            "category": label,
            "observations": row[kind + "s"],
            "observed_sessions": row[kind + "_sessions"],
            "recorded_sessions": row["sessions"],
        }
        for kind, label, group in ATTENTION_CATEGORIES
        for row in result["rows"]
    ]


def attention_sessions(result: dict[str, Any], *, category: str) -> dict[str, Any]:
    if category not in {kind for kind, _, _ in ATTENTION_CATEGORIES}:
        raise ValueError("invalid_attention_category")
    columns = ["provider", "session", "top_level_sessions", "child_sessions", category + "s"]
    return {
        **result,
        "columns": columns,
        "rows": [
            {column: row[column] for column in columns} for row in result["rows"] if row[category + "s"] is not None
        ],
        "view_filter": {"attention_category": category},
    }


def load_dashboard(
    path: Path,
    *,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, dict[str, Any]]:
    with Store(path, read_only=True) as store:
        return {
            group: report(store, start=start, end=end, provider=provider, zone=zone, group_by=group)
            for group in ("provider", "day", "model", "project", "session")
        }


def load_work_patterns(
    path: Path,
    *,
    prices: Prices,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, dict[str, Any]]:
    filters = {"start": start, "end": end, "provider": provider, "zone": zone}
    with Store(path, read_only=True) as store:
        return {
            "tools": tool_profile(store, **filters),
            "cache": cache_efficiency(store, **filters),
            "friction": project_friction(store, **filters),
            "cost": cost_by_model(store, prices=prices, **filters),
            "cost_total": cost_total(store, prices=prices, **filters),
            "cost_project": cost_by_project(store, prices=prices, **filters),
            "cost_day": cost_by_day(store, prices=prices, **filters),
        }


def coverage_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    categories = (
        ("usage", "usage_sessions"),
        *((field, field + "_sessions") for field in TOKEN_FIELDS),
        *((kind, kind + "_sessions") for kind in COUNT_KINDS),
    )
    return [
        {
            "provider": row["provider"],
            "category": category,
            "observed_sessions": row[column],
            "sessions": row["sessions"],
            "percent": 100 * row[column] / row["sessions"] if row["sessions"] else None,
        }
        for row in result["rows"]
        for category, column in categories
    ]


def ranking_rows(result: dict[str, Any], *, field: str) -> list[dict[str, Any]]:
    group = result["group_by"]
    if group not in ("model", "project") or field not in TOKEN_FIELDS:
        raise ValueError("invalid_ranking")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in result["rows"]:
        identity = row["project_id"] if group == "project" else row["model"]
        groups.setdefault((row["provider"], identity), []).append(row)
    return sorted(
        [
            {
                "provider": provider,
                "identity": identity,
                "label": rows[0][group],
                "value": sum(values) if values else None,
                "partial": any(row[field + "_sessions"] < row["sessions"] for row in rows),
            }
            for (provider, identity), rows in groups.items()
            for values in ([row[field] for row in rows if row[field] is not None],)
        ],
        key=lambda row: (row["provider"], -(row["value"] if row["value"] is not None else -1), row["identity"]),
    )
