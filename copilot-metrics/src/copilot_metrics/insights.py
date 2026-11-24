from collections import Counter
from collections.abc import Callable
from itertools import groupby
from typing import Any
from zoneinfo import ZoneInfo

from copilot_metrics.models import PERMISSION_CAUSES, TOKEN_FIELDS
from copilot_metrics.prices import Prices, estimate_cost
from copilot_metrics.reports import local_zone, selected_events
from copilot_metrics.storage import Store

PER_HUNDRED = 100
SHORT_ID_LENGTH = 6
DEFAULT_IDLE_MINUTES = 5
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
EXCLUSIVE_INPUT_PROVIDERS = ("claude",)
STEERING_SIGNAL_PROVIDERS = {
    "user_message": ("copilot", "claude"),
    "abort": ("copilot",),
    "permission_denied": ("copilot", "claude"),
    "compaction": ("copilot",),
}
CAUSE_PROVIDERS = ("copilot", "claude")
TRIGGER_COLUMNS = {"abort": "after_abort", "permission_denied": "after_denial", "tool_error": "after_failure"}
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


def active_minutes(events: list[dict[str, Any]], idle_minutes: float) -> float:
    times = sorted(event["timestamp"] for event in events)
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(times, times[1:], strict=False)]
    return sum(gap for gap in gaps if gap <= idle_minutes * SECONDS_PER_MINUTE) / SECONDS_PER_MINUTE


def longest_streak(outcomes: list[bool]) -> int:
    return max((len(list(run)) for failed, run in groupby(outcomes) if failed), default=0)


def steering_count(provider: str, owned: list[dict[str, Any]], kind: str) -> int | None:
    if provider not in STEERING_SIGNAL_PROVIDERS[kind]:
        return None
    return sum(1 for event in owned if event["kind"] == kind)


def identical_retries(calls: list[dict[str, Any]], failed: set[str]) -> int:
    first_failure = {
        call["signature"]: index for index, call in reversed(list(enumerate(calls))) if call["identity"] in failed
    }
    return sum(1 for index, call in enumerate(calls) if first_failure.get(call["signature"], len(calls)) < index)


def loop_signals(calls: list[dict[str, Any]], failed: set[str]) -> dict[str, int | None]:
    signed = [call for call in calls if call["signature"]]
    if not signed:
        return {"identical_retries": None, "max_identical_calls": None}
    return {
        "identical_retries": identical_retries(signed, failed),
        "max_identical_calls": max(Counter(call["signature"] for call in signed).values()),
    }


def session_quality_row(
    key: tuple[str, str], owned: list[dict[str, Any]], *, project: str, prices: Prices, idle_minutes: float
) -> dict[str, Any]:
    errors = [event for event in owned if event["kind"] == "tool_error"]
    failed = {event["identity"] for event in errors if event["cause"] not in PERMISSION_CAUSES}
    calls = [event for event in owned if event["kind"] == "tool_call"]
    outcomes = [call["identity"] in failed for call in calls]
    minutes = active_minutes(owned, idle_minutes)
    cost = cost_cell([cost for _, cost in usage_costs(owned, prices)])["estimated_cost_usd"]
    hourly = None if cost is None else ratio(cost, minutes / MINUTES_PER_HOUR)
    return {
        "provider": key[0],
        "project": project,
        "session": key[1][:SHORT_ID_LENGTH],
        "active_minutes": minutes,
        "tool_calls": len(outcomes),
        "tool_errors": sum(outcomes),
        "tool_denials": (
            sum(1 for event in errors if event["cause"] in PERMISSION_CAUSES) if key[0] in CAUSE_PROVIDERS else None
        ),
        "longest_error_streak": longest_streak(outcomes) if outcomes else None,
        "ended_on_error": outcomes[-1] if outcomes else None,
        **loop_signals(calls, failed),
        "user_messages": steering_count(key[0], owned, "user_message"),
        "aborts": steering_count(key[0], owned, "abort"),
        "permission_denials": steering_count(key[0], owned, "permission_denied"),
        "compactions": steering_count(key[0], owned, "compaction"),
        "estimated_cost_usd": cost,
        "cost_per_active_hour_usd": hourly,
    }


def session_quality(
    store: Store,
    *,
    prices: Prices,
    idle_minutes: float = DEFAULT_IDLE_MINUTES,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    projects = session_projects(events)
    rows = [
        session_quality_row(key, owned, project=projects[key], prices=prices, idle_minutes=idle_minutes)
        for key, owned in sorted(events_by_session(events).items())
    ]
    return {
        "timezone": zone,
        "columns": [
            "provider",
            "project",
            "session",
            "active_minutes",
            "tool_calls",
            "tool_errors",
            "tool_denials",
            "longest_error_streak",
            "ended_on_error",
            "identical_retries",
            "max_identical_calls",
            "user_messages",
            "aborts",
            "permission_denials",
            "compactions",
            "estimated_cost_usd",
            "cost_per_active_hour_usd",
        ],
        "notes": [
            f"Active minutes is an estimate: gaps between recorded events longer than {idle_minutes:g} minutes "
            "are treated as idle and excluded. It is not time spent by you or by the agent working.",
            "Tool errors, longest error streak and ended on error count genuine tool failures only; calls refused by "
            "permission rules or by you are counted separately as tool denials. Streak is the most consecutive "
            "failed calls in time order and is unavailable for sessions with no tool calls.",
            "Identical retries are calls repeating the same tool and arguments as an earlier call that failed; "
            "max identical calls is the most times one such call appeared. Arguments are compared by one-way hash "
            "and never stored. Unavailable where arguments are not recorded (Codex).",
            "User messages are prompts you typed, aborts are user interruptions, denials are permission requests you "
            "refused yourself (automatic or rule-based denials are excluded), compactions are successful context compactions. A signal is unavailable for providers that "
            "do not record it (Claude aborts and compactions; all four for Codex), never zero. Reimport after "
            "upgrading so earlier sessions gain these counts.",
            "Cost is the per-token estimate from prices.toml; it and cost per active hour are unavailable when "
            "nothing could be priced or no active time was observed. No combined quality score is computed.",
        ],
        "rows": rows,
    }


def reacting_to(owned: list[dict[str, Any]], idle_minutes: float) -> list[str | None]:
    ordered = sorted(owned, key=lambda event: event["timestamp"])
    prompts = [index for index, event in enumerate(ordered) if event["kind"] == "user_message"]
    triggers = {
        index
        for index, event in enumerate(ordered)
        if event["kind"] in TRIGGER_COLUMNS
        and not (event["kind"] == "tool_error" and event["cause"] in PERMISSION_CAUSES)
    }
    window = idle_minutes * SECONDS_PER_MINUTE

    def trigger_before(prompt_index: int, previous_prompt_index: int) -> str | None:
        candidates = [
            index
            for index in range(prompt_index - 1, previous_prompt_index, -1)
            if index in triggers
            and (ordered[prompt_index]["timestamp"] - ordered[index]["timestamp"]).total_seconds() <= window
        ]
        return ordered[candidates[0]]["kind"] if candidates else None

    return [trigger_before(current, previous) for previous, current in zip(prompts, prompts[1:], strict=False)]


def steering_row(
    key: tuple[str, str], owned: list[dict[str, Any]], *, project: str, idle_minutes: float
) -> dict[str, Any]:
    provider = key[0]
    reactions = reacting_to(owned, idle_minutes)
    observable = provider in STEERING_SIGNAL_PROVIDERS["user_message"]
    counts = {
        column: (sum(1 for kind in reactions if kind == trigger) if provider in trigger_providers(trigger) else None)
        for trigger, column in TRIGGER_COLUMNS.items()
    }
    reactive = sum(count for count in counts.values() if count)
    return {
        "provider": provider,
        "project": project,
        "session": key[1][:SHORT_ID_LENGTH],
        "follow_up_prompts": len(reactions) if observable else None,
        **counts,
        "reactive_share_percent": ratio(PER_HUNDRED * reactive, len(reactions)) if observable else None,
    }


def trigger_providers(trigger: str) -> tuple[str, ...]:
    return CAUSE_PROVIDERS if trigger == "tool_error" else STEERING_SIGNAL_PROVIDERS[trigger]


def steering_follow_ups(
    store: Store,
    *,
    idle_minutes: float = DEFAULT_IDLE_MINUTES,
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    zone, events = events_of(store, start=start, end=end, provider=provider, zone=zone)
    projects = session_projects(events)
    rows = [
        steering_row(key, owned, project=projects[key], idle_minutes=idle_minutes)
        for key, owned in sorted(events_by_session(events).items())
    ]
    return {
        "timezone": zone,
        "columns": [
            "provider",
            "project",
            "session",
            "follow_up_prompts",
            *TRIGGER_COLUMNS.values(),
            "reactive_share_percent",
        ],
        "notes": [
            "A follow-up prompt is any prompt after the first in a session. It is attributed to the nearest earlier "
            f"abort, denial you made, or genuine tool failure within {idle_minutes:g} minutes with no prompt between. "
            "This is inferred from event order; prompt text is not read, so a reaction is not confirmed.",
            "Each trigger explains at most one prompt, and permission refusals are not counted as failures. "
            "Reactive share is the percent of follow-up prompts attributed to a trigger; no combined score is computed.",
            "A signal is unavailable for providers that do not record it (Claude aborts; all signals for Codex), "
            "never zero. Reimport after upgrading so earlier sessions gain these counts.",
        ],
        "rows": rows,
    }
