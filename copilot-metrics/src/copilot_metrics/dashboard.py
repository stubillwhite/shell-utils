import argparse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import altair as alt
import duckdb
import streamlit as st

from copilot_metrics.dashboard_data import (
    ATTENTION_CATEGORIES,
    attention_rows,
    attention_sessions,
    coverage_rows,
    load_dashboard,
    load_work_patterns,
    ranking_rows,
)
from copilot_metrics.models import PROVIDERS, TOKEN_FIELDS
from copilot_metrics.prices import load_prices
from copilot_metrics.reports import daily_series, local_zone, render


def number(value: int | float | None) -> str:
    return "Unavailable" if value is None else f"{value:,}"


def report_table(result: dict[str, Any], *, label: str, key: str) -> None:
    st.dataframe(
        result["rows"] or {column: [] for column in result["columns"]},
        hide_index=True,
        key=key + "_table",
    )
    st.caption("Unavailable values are blank; observed zero remains zero. Downloads use these same filtered rows.")
    for format_name, mime in (("csv", "text/csv"), ("json", "application/json")):
        st.download_button(
            f"Download {label} {format_name.upper()}",
            render(result, format_name),
            file_name=f"{key}.{format_name}",
            mime=mime,
            key=f"{key}_{format_name}",
            on_click="ignore",
        )


def combined_daily(result: dict[str, Any]) -> None:
    st.subheader("Daily reported input and output")
    st.caption(
        "Combined providers, reported input/output only. Provider definitions differ. "
        "Input + output controls bar geometry only; cache/reasoning are not added."
    )
    rows = [
        {
            "day": day,
            "category": "Input" if category == "in" else "Output",
            "tokens": value,
            "reported": number(value),
            "coverage": "unavailable" if value is None else "partial" if partial else "observed",
        }
        for day, categories in daily_series(result)
        for category, value, partial in categories
    ]
    if not rows:
        st.info("No daily observations for the selected filters.")
        return
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar()
        .encode(
            x=alt.X("day:O", title="Observed day"),
            y=alt.Y("tokens:Q", title="Reported tokens", stack="zero"),
            color=alt.Color("category:N", scale=alt.Scale(domain=["Input", "Output"], range=["#6CB6FF", "#E3B341"])),
            order=alt.Order("category:N"),
            tooltip=["day:N", "category:N", "reported:N", "coverage:N"],
        )
    )
    st.altair_chart(chart)
    st.caption("Partial = some sessions lack observations. Missing days are not filled with zeroes.")
    st.dataframe(rows, hide_index=True)


def money(value: float | None) -> str:
    return "Unavailable" if value is None else f"${value:,.2f}"


def headline_cost(total: dict[str, Any]) -> None:
    (row,) = total["rows"]
    st.metric("Estimated cost (USD)", money(row["estimated_cost_usd"]))
    if row["unpriced_events"]:
        st.caption(
            f'Lower bound: {row["unpriced_events"]} usage records could not be priced '
            f'({row["unpriced_models"]}). Add rates to prices.toml.'
            if row["priced_events"]
            else f'No usage could be priced ({row["unpriced_models"]}). Add rates to prices.toml.'
        )
    st.caption("Estimate at your configured per-token rates, not a bill. Providers are combined.")


def daily_cost(result: dict[str, Any]) -> None:
    st.subheader("Daily estimated cost")
    rows = [
        {
            **row,
            "coverage": "partial" if row["unpriced_events"] else "complete",
            "reported": money(row["estimated_cost_usd"]),
        }
        for row in result["rows"]
        if row["estimated_cost_usd"] is not None
    ]
    if not rows:
        st.info("No priced usage for the selected filters.")
        return
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(color="#6CB6FF")
        .encode(
            x=alt.X("day:O", title="Observed day"),
            y=alt.Y("estimated_cost_usd:Q", title="Estimated cost (USD)"),
            tooltip=["day:N", "reported:N", "coverage:N", "unpriced_events:Q"],
        )
    )
    st.altair_chart(chart)
    st.caption("Partial = some usage that day could not be priced. Days with no priced usage are omitted, not zero.")


def overview(results: dict[str, dict[str, Any]], patterns: dict[str, dict[str, Any]]) -> None:
    headline_cost(patterns["cost_total"])
    for row in results["provider"]["rows"]:
        st.subheader(row["provider"].title())
        columns = st.columns(4)
        columns[0].metric("Recorded sessions", number(row["sessions"]))
        columns[1].metric("Reported input tokens", number(row["input_tokens"]))
        columns[2].metric("Reported output tokens", number(row["output_tokens"]))
        columns[3].metric("Sessions with usage", f'{row["usage_sessions"]} / {row["sessions"]}')
        st.caption(
            f'Top-level: {row["top_level_sessions"]}; child: {row["child_sessions"]}. '
            f'Input observed in {row["input_tokens_sessions"]}/{row["sessions"]} sessions; '
            f'output in {row["output_tokens_sessions"]}/{row["sessions"]}.'
        )
    combined_daily(results["day"])
    daily_cost(patterns["cost_day"])
    field = st.selectbox("Daily token category", TOKEN_FIELDS, key="daily_metric")
    for provider in sorted({row["provider"] for row in results["day"]["rows"]}):
        rows = [row for row in results["day"]["rows"] if row["provider"] == provider]
        st.subheader(f"{provider.title()} daily observations")
        for column, metric in zip(st.columns(2), (field, "sessions"), strict=True):
            chart = (
                alt.Chart(alt.Data(values=rows))
                .mark_bar(color="#6CB6FF")
                .encode(
                    x=alt.X("day:O", title="Observed day"),
                    y=alt.Y(f"{metric}:Q", title=metric),
                    tooltip=["day:N", alt.Tooltip(f"{metric}:Q", format=","), "sessions:Q"],
                )
            )
            column.altair_chart(chart)
    st.caption(
        "Daily sessions are not additive unique sessions across days. Trends show observations, not complete history."
    )
    with st.expander("Provider report and downloads"):
        report_table(results["provider"], label="providers", key="providers")
    with st.expander("Daily report and downloads (provider-separated)"):
        report_table(results["day"], label="daily", key="daily")


def attention_signals(results: dict[str, dict[str, Any]]) -> None:
    st.subheader("Attention signals")
    st.caption(
        "Notifications are observed attention signals, not complete prompt counts. "
        "Historical requests and hook checks can resolve automatically. Categories overlap; never add them together."
    )
    st.caption(
        "Human wait time is unavailable: these observations have no correlated prompt-display/resolution timestamps. "
        "Claude notifications are delayed and can miss quick responses. Codex currently supplies hook checks only. "
        "Claude MCP outcomes do not cover general approvals or AskUserQuestion."
    )
    providers = results["provider"]["rows"]
    labels = {kind: label for kind, label, _ in ATTENTION_CATEGORIES}
    if not providers:
        st.info("No recorded sessions for the selected filters.")
    for row in providers:
        st.subheader(row["provider"].title())
        for column, kind in zip(
            st.columns(2),
            ("approval_notification", "guidance_notification"),
            strict=True,
        ):
            column.metric(labels[kind], number(row[kind + "s"]))
            column.caption(f'{row[kind + "_sessions"]} / {row["sessions"]} recorded sessions with observations.')
    evidence = attention_rows(results["provider"])
    if providers and not any(
        row["observations"] is not None and row["evidence"] != "Historical inferred requests" for row in evidence
    ):
        st.info("No imported hook observations in the selected filters. Historical request evidence is retained.")
        st.markdown(
            "See **README.md: Opt-in interruption observations** for hook setup and explicit log imports. "
            "Refresh only rereads the database; it cannot determine whether hooks are installed."
        )
    st.subheader("Evidence by category")
    st.dataframe(evidence, hide_index=True)
    st.caption(
        "Blank counts are unavailable, not zero. Sessions with observations / recorded sessions are not "
        "instrumentation coverage or proof of complete history."
    )
    category = st.selectbox(
        "Attention category",
        list(labels),
        format_func=labels.__getitem__,
        key="attention_category",
    )
    st.subheader(f"Daily {labels[category].lower()}")
    st.caption(f'Report timezone: {results["day"]["timezone"]}.')
    for provider in providers:
        name = provider["provider"]
        rows = [row for row in results["day"]["rows"] if row["provider"] == name and row[category + "s"] is not None]
        if not rows:
            st.info(f"{name.title()}: No observations for {labels[category].lower()} in the selected filters.")
            continue
        st.subheader(name.title())
        chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_bar(color="#6CB6FF")
            .encode(
                x=alt.X("day:O", title="Observed day"),
                y=alt.Y(f"{category}s:Q", title=labels[category]),
                tooltip=[
                    "provider:N",
                    "day:N",
                    alt.Tooltip(f"{category}s:Q", title="Observations", format=","),
                    alt.Tooltip(f"{category}_sessions:Q", title="Sessions with observations"),
                    alt.Tooltip("sessions:Q", title="Recorded sessions"),
                ],
            )
        )
        st.altair_chart(chart)
    st.caption(
        "Missing days are not filled with zeroes. Daily session counts are not additive unique sessions. "
        "Hook timestamps are receipt times; repeated deliveries may count separately."
    )
    st.subheader("Sessions with selected evidence")
    selected = attention_sessions(results["session"], category=category)
    if not selected["rows"]:
        st.info("No sessions with observations for the selected category and filters.")
    st.caption(
        "Hashed session IDs identify recorded sessions, not tasks. Child flags are source-reported evidence. "
        "No model/project attribution or human-wait duration is inferred."
    )
    report_table(selected, label="attention sessions", key="attention_sessions")


def breakdown(result: dict[str, Any], *, label: str, field: str) -> None:
    group = result["group_by"]
    identity = "project_id" if group == "project" else "model"
    options = sorted({row[identity] for row in result["rows"]})
    labels = {
        row[identity]: f'{row["project"]} [{row["project_id"]}]' if group == "project" else row["model"]
        for row in result["rows"]
    }
    chosen = st.selectbox(
        f"{group.title()} (this breakdown only)",
        [None, *options],
        format_func=lambda value: "All" if value is None else labels[value],
        key=f"{group}_filter",
    )
    selected = {
        **result,
        "rows": [row for row in result["rows"] if chosen is None or row[identity] == chosen],
        "view_filter": {group: chosen},
    }
    ranked = ranking_rows(selected, field=field)
    for provider in sorted({row["provider"] for row in ranked}):
        rows = [
            {
                **row,
                "display": f'{row["label"]} [{row["identity"][:12]}]' if group == "project" else row["label"],
                "reported": number(row["value"]),
            }
            for row in ranked
            if row["provider"] == provider
        ]
        chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_bar(color="#6CB6FF")
            .encode(
                x=alt.X("value:Q", title=field),
                y=alt.Y("display:N", sort="-x", title=group),
                tooltip=["label:N", "identity:N", "reported:N", "partial:N"],
            )
        )
        st.subheader(provider.title())
        st.altair_chart(chart)
    st.caption(
        f"{group.title()} selection applies only here, including downloads. "
        "Unknown attribution is retained; unavailable bars are omitted, not counted as zero. "
        "Rankings show reported usage, not model quality or productivity."
    )
    report_table(selected, label=label, key=label)


def data_quality(result: dict[str, Any]) -> None:
    st.subheader("Observation coverage")
    st.caption(
        "Coverage counts sessions with observations, not proof of complete history. Blank percentages are unavailable."
    )
    st.dataframe(coverage_rows(result), hide_index=True)
    st.subheader("Import diagnostics")
    st.caption("Diagnostics cover all stored imports for the selected provider, irrespective of date filters.")
    if result["diagnostics"]:
        st.dataframe(result["diagnostics"], hide_index=True)
    else:
        st.info("No stored diagnostic codes for the selected provider.")
    with st.expander("Metric definitions and limitations"):
        for note in result["notes"]:
            st.write(note)
        st.write(
            "Model/project session counts and recorded spans are not additive. "
            "Project labels are working-directory basenames; IDs distinguish same-named folders. "
            "Keep this database and its exports private."
        )


WORK_PATTERN_SECTIONS = (
    ("tools", "Tool profile", "tool profile", "work_tools"),
    ("cache", "Prompt-cache efficiency", "cache efficiency", "work_cache"),
    ("friction", "Project friction", "project friction", "work_friction"),
    ("cost", "Estimated cost", "estimated cost", "work_cost"),
)


def work_patterns(patterns: dict[str, dict[str, Any]]) -> None:
    for name, heading, label, key in WORK_PATTERN_SECTIONS:
        st.subheader(heading)
        for note in patterns[name]["notes"]:
            st.caption(note)
        report_table(patterns[name], label=label, key=key)


def show_dashboard(path: Path, *, prices_path: Path = Path("prices.toml")) -> None:
    st.set_page_config(page_title="Local coding-assistant metrics", layout="wide")
    st.title("Coding-assistant metrics")
    st.caption(
        "Local, read-only imported evidence. No transcript access or productivity scores. "
        "Cost figures are estimates from your own price file."
    )
    with st.sidebar:
        st.header("Filters")
        provider = st.selectbox("Provider", ("All", *PROVIDERS), key="provider")
        all_history = st.checkbox("All imported history", value=True, key="all_history")
        start = st.date_input("From (inclusive)", value=date.today(), disabled=all_history, key="start")
        end = st.date_input("To (inclusive)", value=date.today(), disabled=all_history, key="end")
        zone = st.text_input("Timezone", value=local_zone(), key="timezone")
        st.button("Refresh imported data", key="refresh")
        st.caption("Refresh rereads the database; it never imports transcripts. Run the CLI import separately.")
    if not all_history and (not isinstance(start, date) or not isinstance(end, date)):
        st.error("Choose complete From and To dates.")
        return
    try:
        prices = load_prices(prices_path)
    except ValueError:
        st.error("Invalid prices file: costs are unavailable. Fix prices.toml and Refresh.")
        prices = {}
    try:
        if not zone:
            raise ValueError("invalid_timezone")
        filters = {
            "provider": None if provider == "All" else provider,
            "start": start.isoformat() if not all_history and isinstance(start, date) else None,
            "end": end.isoformat() if not all_history and isinstance(end, date) else None,
            "zone": zone,
        }
        patterns = load_work_patterns(path, prices=prices, **filters)
        results = load_dashboard(path, **filters)
    except (ValueError, ZoneInfoNotFoundError):
        st.error("Invalid filters. Choose a valid IANA timezone and a From date no later than To.")
        return
    except (OSError, duckdb.Error):
        st.error(
            "Database unavailable. Check --db and permissions; import with the CLI first for a missing or outdated "
            "database. Stop any active importer, then Refresh. No database was created or migrated."
        )
        return
    st.caption(
        f'Report timezone: {results["provider"]["timezone"]}. '
        f'Last successful database read: {datetime.now(timezone.utc).isoformat(timespec="seconds")}. '
        "This is not a source-history freshness guarantee."
    )
    if not results["provider"]["rows"]:
        st.info("No data for the selected filters. Import history with the CLI or adjust the filters.")
    summary, attention, models, patterns_tab, sessions, quality = st.tabs(
        ["Overview", "Attention signals", "Models & Projects", "Work patterns", "Sessions", "Data Quality"]
    )
    with summary:
        overview(results, patterns)
    with attention:
        attention_signals(results)
    with models:
        st.caption(
            "Token categories differ by provider. Cache/reasoning fields may overlap input/output; "
            "never add them into a grand total."
        )
        field = st.selectbox("Ranking token category", TOKEN_FIELDS, key="ranking_metric")
        st.subheader("Models")
        breakdown(results["model"], label="models", field=field)
        st.subheader("Projects")
        breakdown(results["project"], label="projects", field=field)
        st.subheader("Estimated cost by project")
        for note in patterns["cost_project"]["notes"]:
            st.caption(note)
        report_table(patterns["cost_project"], label="cost by project", key="cost_projects")
    with patterns_tab:
        work_patterns(patterns)
    with sessions:
        st.caption(
            "Sort columns to explore recorded sessions. Elapsed seconds are recorded event spans, not active time; "
            "filters clip spans. Tool errors require explicit evidence. "
            "Approval/clarification requests are inferred interruptions, not confirmed visible prompts."
        )
        report_table(results["session"], label="sessions", key="sessions")
    with quality:
        data_quality(results["provider"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only metrics dashboard.")
    parser.add_argument("--db", type=Path, default=Path("metrics.duckdb"))
    parser.add_argument("--prices", type=Path, default=Path("prices.toml"))
    arguments = parser.parse_args()
    show_dashboard(arguments.db, prices_path=arguments.prices)


if __name__ == "__main__":
    main()
