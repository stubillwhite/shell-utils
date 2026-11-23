from collections import Counter
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from copilot_metrics.models import TOKEN_FIELDS
from copilot_metrics.prices import Prices, estimate_cost
from copilot_metrics.reports import local_zone, selected_events
from copilot_metrics.storage import Store

PER_HUNDRED = 100
SHORT_ID_LENGTH = 6
EXCLUSIVE_INPUT_PROVIDERS = ("claude",)
FRICTION_SIGNALS = ("tool_error", "approval_request", "clarification_request")


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def events_of(
    store: Store, *, start: str | None, end: str | None, provider: str | None, zone: str | None
) -> tuple[str, list[dict[str, Any]]]:
    zone = zone or local_zone()
    return zone, selected_events(store, start=start, end=end, provider=provider, tz=ZoneInfo(zone))


def tool_origin(name: str) -> str:
    return "mcp" if "-" in name else "builtin"


def tool_profile(
    store: Store,
    *,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    calls = [event for event in events if event["kind"] == "tool_call" and event["tool"]]
    failed = {
        (event["provider"], event["session"], event["identity"]) for event in events if event["kind"] == "tool_error"
    }
    totals = Counter((call["provider"], call["tool"]) for call in calls)
    errors = Counter(
        (call["provider"], call["tool"])
        for call in calls
        if (call["provider"], call["session"], call["identity"]) in failed
    )
    sessions = {key: len({c["session"] for c in calls if (c["provider"], c["tool"]) == key}) for key in totals}
    rows = [
        {
            "provider": name,
            "tool": tool,
            "origin": tool_origin(tool),
            "calls": count,
            "errors": errors[(name, tool)],
            "error_rate": errors[(name, tool)] / count,
            "sessions": sessions[(name, tool)],
        }
        for (name, tool), count in sorted(totals.items(), key=lambda item: (item[0][0], -item[1], item[0][1]))
    ]
    return {
        "timezone": zone,
        "columns": ["provider", "tool", "origin", "calls", "errors", "error_rate", "sessions"],
        "notes": ["MCP tools are identified by a hyphenated server-tool name; Codex tool errors are unavailable."],
        "rows": rows,
    }


def cache_hit_rate(provider: str, usage: dict[str, int | None]) -> float | None:
    exclusive = provider in EXCLUSIVE_INPUT_PROVIDERS
    needed = ("input_tokens", "cached_input_tokens", *(("cache_creation_tokens",) if exclusive else ()))
    if any(usage[field] is None for field in needed):
        return None
    cached = int(usage["cached_input_tokens"] or 0)
    input_tokens = int(usage["input_tokens"] or 0)
    if exclusive:
        return ratio(cached, input_tokens + cached + int(usage["cache_creation_tokens"] or 0))
    return ratio(cached, input_tokens)


def summed_usage(events: list[dict[str, Any]]) -> dict[str, int | None]:
    return {
        field: sum(values) if (values := [e[field] for e in events if e[field] is not None]) else None
        for field in TOKEN_FIELDS
    }


def cache_efficiency(
    store: Store,
    *,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    usage = [event for event in events if event["kind"] == "usage"]
    keys = sorted({(event["provider"], event["model"] or "unknown") for event in usage})
    rows = []
    for name, model in keys:
        totals = summed_usage([e for e in usage if e["provider"] == name and (e["model"] or "unknown") == model])
        rows.append(
            {
                "provider": name,
                "model": model,
                **totals,
                "cache_hit_rate": cache_hit_rate(name, totals),
            }
        )
    return {
        "timezone": zone,
        "columns": ["provider", "model", *TOKEN_FIELDS, "cache_hit_rate"],
        "notes": [
            "Claude input excludes cache: rate = read / (input + read + write). "
            "Copilot and Codex input includes cache: rate = read / input.",
            "Unavailable when a field the formula needs was never reported.",
        ],
        "rows": rows,
    }


def project_label(event: dict[str, Any]) -> str:
    return f'{event["project"]} ({event["project_id"][:SHORT_ID_LENGTH]})' if event["project_id"] else "unknown"


def events_by_session(events: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault((event["provider"], event["session"]), []).append(event)
    return grouped


def session_projects(events: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {
        key: project_label(located) if (located := next((e for e in owned if e["project_id"]), None)) else "unknown"
        for key, owned in events_by_session(events).items()
    }


def project_friction(
    store: Store,
    *,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    by_session = events_by_session(events)
    projects = session_projects(events)
    by_project: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in by_session:
        by_project.setdefault((key[0], projects[key]), []).append(key)
    rows = []
    for (name, label), keys in sorted(by_project.items()):
        kinds = Counter(event["kind"] for key in keys for event in by_session[key])
        calls = kinds["tool_call"]
        rows.append(
            {
                "provider": name,
                "project": label,
                "sessions": len(keys),
                "tool_calls": calls,
                **{
                    signal
                    + "s_per_100_calls": (None if (rate := ratio(kinds[signal], calls)) is None else rate * PER_HUNDRED)
                    for signal in FRICTION_SIGNALS
                },
            }
        )
    return {
        "timezone": zone,
        "columns": [
            "provider",
            "project",
            "sessions",
            "tool_calls",
            *[signal + "s_per_100_calls" for signal in FRICTION_SIGNALS],
        ],
        "notes": [
            "Rates per 100 tool calls pooled across a project's sessions; signals overlap and are not combined. "
            "Unavailable when a project has no tool calls. Projects are recorded working directories, "
            "labelled name (short ID) because same-named folders are distinct; sessions with no directory are unknown."
        ],
        "rows": rows,
    }


COST_COLUMNS = ["estimated_cost_usd", "priced_events", "unpriced_events"]
COST_NOTES = [
    "Estimate at configured per-token rates, not your actual bill; Copilot bills by premium request.",
    "Each usage record is priced on its own; records whose model has no configured price, or with any token "
    "field never reported, are counted as unpriced and left out, so a total with unpriced records is a lower bound.",
]


def usage_costs(events: list[dict[str, Any]], prices: Prices) -> list[tuple[dict[str, Any], float | None]]:
    return [
        (
            event,
            estimate_cost(prices, provider=event["provider"], model=event["model"] or "unknown", usage=event),
        )
        for event in events
        if event["kind"] == "usage"
    ]


def cost_cell(costs: list[float | None]) -> dict[str, Any]:
    priced = [cost for cost in costs if cost is not None]
    return {
        "estimated_cost_usd": sum(priced) if priced else None,
        "priced_events": len(priced),
        "unpriced_events": len(costs) - len(priced),
    }


def cost_report(
    zone: str, columns: list[str], rows: list[dict[str, Any]], notes: list[str] | None = None
) -> dict[str, Any]:
    return {"timezone": zone, "columns": [*columns, *COST_COLUMNS], "notes": COST_NOTES + (notes or []), "rows": rows}


def grouped_cost_rows(
    costs: list[tuple[dict[str, Any], float | None]],
    *,
    keys: list[str],
    key_of: Callable[[dict[str, Any]], tuple[str, ...]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[float | None]] = {}
    for event, cost in costs:
        groups.setdefault(key_of(event), []).append(cost)
    return [{**dict(zip(keys, key, strict=True)), **cost_cell(values)} for key, values in sorted(groups.items())]


def cost_by_model(
    store: Store,
    *,
    prices: Prices,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    rows = grouped_cost_rows(
        usage_costs(events, prices),
        keys=["provider", "model"],
        key_of=lambda event: (event["provider"], event["model"] or "unknown"),
    )
    return cost_report(zone, ["provider", "model"], rows)


def cost_by_project(
    store: Store,
    *,
    prices: Prices,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    projects = session_projects(events)
    rows = grouped_cost_rows(
        usage_costs(events, prices),
        keys=["provider", "project"],
        key_of=lambda event: (event["provider"], projects[(event["provider"], event["session"])]),
    )
    return cost_report(
        zone,
        ["provider", "project"],
        rows,
        ["Projects are recorded working directories labelled name (short ID); sessions with no directory are unknown."],
    )


def cost_by_day(
    store: Store,
    *,
    prices: Prices,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    tz = ZoneInfo(zone)
    rows = grouped_cost_rows(
        usage_costs(events, prices),
        keys=["day"],
        key_of=lambda event: (event["timestamp"].astimezone(tz).date().isoformat(),),
    )
    return cost_report(zone, ["day"], rows, ["Providers are combined; days with no usage are not filled with zero."])


def cost_total(
    store: Store,
    *,
    prices: Prices,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    costs = usage_costs(events, prices)
    unpriced = sorted({f'{event["provider"]}/{event["model"] or "unknown"}' for event, cost in costs if cost is None})
    row = {**cost_cell([cost for _, cost in costs]), "unpriced_models": ", ".join(unpriced)}
    return cost_report(zone, [], [row]) | {"columns": [*COST_COLUMNS, "unpriced_models"]}
