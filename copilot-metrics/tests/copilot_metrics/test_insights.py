import pytest

from copilot_metrics.insights import (
    cache_efficiency,
    cost_by_day,
    cost_by_model,
    cost_by_project,
    cost_total,
    project_friction,
    session_quality,
    steering_follow_ups,
    tool_profile,
)
from copilot_metrics.prices import load_prices
from copilot_metrics.storage import Store
from tests.copilot_metrics.test_collectors import (
    claude_tool_use,
    claude_user,
    event,
    write_records,
)


def tool_start(identity, tool, timestamp="2026-01-01T10:00:00Z"):
    return event("tool.execution_start", {"toolName": tool, "toolCallId": identity}, "start-" + identity, timestamp)


def tool_failure(identity):
    return event("tool.execution_complete", {"toolCallId": identity, "success": False}, "done-" + identity)


def copilot_usage(identity, timestamp="2026-01-01T10:00:00Z", **fields):
    usage = {"apiCallId": identity, "model": "gpt-6-luna", **fields}
    return event("session.usage_record", {"usage": usage}, identity, timestamp)


def claude_usage(identity, **fields):
    return {
        "type": "assistant",
        "sessionId": "claude-session",
        "timestamp": "2026-01-01T12:00:00Z",
        "message": {"id": identity, "model": "claude-opus-5-5", "usage": fields},
    }


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "metrics.duckdb") as opened:
        yield opened


def import_copilot(store, tmp_path, records, *, session_id="s", cwd=None):
    folder = tmp_path / session_id
    folder.mkdir(exist_ok=True)
    context = {"context": {"cwd": cwd}} if cwd else {}
    start = event("session.start", {"sessionId": session_id, **context})
    store.import_file("copilot", write_records(folder, [start, *records]))


def import_claude(store, tmp_path, records):
    (tmp_path / "claude").mkdir()
    store.import_file("claude", write_records(tmp_path / "claude", records))


def test_tool_profile_attributes_each_error_to_the_tool_that_failed(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [tool_start("a", "bash"), tool_start("b", "bash"), tool_start("c", "view"), tool_failure("a")],
    )
    rows = {row["tool"]: row for row in tool_profile(store, zone="UTC")["rows"]}
    assert (rows["bash"]["calls"], rows["bash"]["errors"], rows["bash"]["error_rate"]) == (2, 1, 0.5)
    assert (rows["view"]["calls"], rows["view"]["errors"], rows["view"]["error_rate"]) == (1, 0, 0.0)


def test_tool_profile_separates_mcp_from_builtin_tools_so_integration_reliance_is_visible(store, tmp_path):
    import_copilot(store, tmp_path, [tool_start("a", "bash"), tool_start("b", "Atlassian-getJiraIssue")])
    rows = {row["tool"]: row for row in tool_profile(store, zone="UTC")["rows"]}
    assert rows["bash"]["origin"] == "builtin"
    assert rows["Atlassian-getJiraIssue"]["origin"] == "mcp"


def test_tool_profile_respects_date_filters_in_the_requested_timezone(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [tool_start("a", "bash", "2026-01-01T23:30:00Z"), tool_start("b", "bash", "2026-01-02T10:00:00Z")],
    )
    result = tool_profile(store, start="2026-01-02", end="2026-01-02", zone="UTC")
    assert [row["calls"] for row in result["rows"]] == [1]


def test_cache_hit_rate_treats_copilot_input_as_inclusive_of_cache(store, tmp_path):
    import_copilot(
        store, tmp_path, [copilot_usage("u", inputTokens=100, outputTokens=1, cacheReadTokens=80, cacheWriteTokens=0)]
    )
    (row,) = cache_efficiency(store, zone="UTC")["rows"]
    assert row["cache_hit_rate"] == pytest.approx(0.8)


def test_cache_hit_rate_treats_claude_input_as_exclusive_of_cache(store, tmp_path):
    import_claude(
        store,
        tmp_path,
        [
            claude_usage(
                "m",
                input_tokens=20,
                output_tokens=1,
                cache_read_input_tokens=80,
                cache_creation_input_tokens=100,
            )
        ],
    )
    (row,) = cache_efficiency(store, zone="UTC")["rows"]
    assert row["cache_hit_rate"] == pytest.approx(80 / 200)


def test_cache_hit_rate_is_unavailable_when_cache_reads_were_never_reported(store, tmp_path):
    import_copilot(store, tmp_path, [copilot_usage("u", inputTokens=100, outputTokens=1)])
    (row,) = cache_efficiency(store, zone="UTC")["rows"]
    assert row["cache_hit_rate"] is None


def calls(count, prefix):
    return [tool_start(f"{prefix}{index}", "bash") for index in range(count)]


def test_friction_is_a_per_call_rate_with_each_signal_kept_separate(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            *calls(4, "c"),
            tool_failure("c0"),
            event("permission.requested", {"requestId": "p"}, "perm"),
            event("tool.execution_start", {"toolName": "ask_user", "toolCallId": "q"}, "ask"),
        ],
        cwd="/work/app",
    )
    (row,) = project_friction(store, zone="UTC")["rows"]
    assert row["tool_calls"] == 5
    assert row["tool_errors_per_100_calls"] == pytest.approx(20.0)
    assert row["approval_requests_per_100_calls"] == pytest.approx(20.0)
    assert row["clarification_requests_per_100_calls"] == pytest.approx(20.0)
    assert "friction_score" not in row


def test_friction_rows_identify_projects_not_sessions(store, tmp_path):
    import_copilot(store, tmp_path, calls(1, "c"), cwd="/work/app")
    (row,) = project_friction(store, zone="UTC")["rows"]
    assert row["project"].startswith("app (")
    assert "session" not in row and "session" not in project_friction(store, zone="UTC")["columns"]


def test_friction_pools_calls_across_a_projects_sessions_instead_of_averaging_session_rates(store, tmp_path):
    import_copilot(store, tmp_path, [*calls(4, "a"), tool_failure("a0")], session_id="one", cwd="/work/app")
    import_copilot(store, tmp_path, calls(6, "b"), session_id="two", cwd="/work/app")
    (row,) = project_friction(store, zone="UTC")["rows"]
    assert (row["sessions"], row["tool_calls"]) == (2, 10)
    assert row["tool_errors_per_100_calls"] == pytest.approx(10.0)


def test_same_named_folders_stay_distinct_projects(store, tmp_path):
    import_copilot(store, tmp_path, calls(1, "a"), session_id="one", cwd="/work/a/app")
    import_copilot(store, tmp_path, calls(1, "b"), session_id="two", cwd="/work/b/app")
    labels = [row["project"] for row in project_friction(store, zone="UTC")["rows"]]
    assert len(set(labels)) == 2 and all(label.startswith("app (") for label in labels)


def test_sessions_without_a_recorded_directory_are_grouped_as_unknown(store, tmp_path):
    import_copilot(store, tmp_path, calls(2, "c"))
    (row,) = project_friction(store, zone="UTC")["rows"]
    assert row["project"] == "unknown"


def test_friction_is_unavailable_for_projects_without_tool_calls_instead_of_zero(store, tmp_path):
    import_copilot(store, tmp_path, [event("permission.requested", {"requestId": "p"}, "perm")], cwd="/work/app")
    (row,) = project_friction(store, zone="UTC")["rows"]
    assert row["tool_errors_per_100_calls"] is None


def test_cost_by_model_sums_only_priced_usage_and_reports_unpriced_models_as_unavailable(store, tmp_path):
    prices_path = tmp_path / "prices.toml"
    prices_path.write_text('[models."gpt-6-luna"]\ninput = 2.0\noutput = 8.0\ncache_read = 0.5\ncache_write = 2.0\n')
    import_copilot(
        store,
        tmp_path,
        [
            copilot_usage("u1", inputTokens=1_000_000, outputTokens=1_000_000, cacheReadTokens=0, cacheWriteTokens=0),
            copilot_usage("u2", inputTokens=1_000_000, outputTokens=1_000_000, cacheReadTokens=0, cacheWriteTokens=0),
        ],
    )
    import_claude(
        store,
        tmp_path,
        [
            claude_usage(
                "m",
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )
        ],
    )
    result = cost_by_model(store, prices=load_prices(prices_path), zone="UTC")
    costs = {(row["provider"], row["model"]): row["estimated_cost_usd"] for row in result["rows"]}
    assert costs[("copilot", "gpt-6-luna")] == pytest.approx(2 * (2.0 + 8.0))
    assert costs[("claude", "claude-opus-5-5")] is None
    assert any("estimate" in note.lower() for note in result["notes"])


MILLION = 1_000_000
PRICES = {"gpt-6-luna": {"input": 2.0, "output": 8.0, "cache_read": 0.5, "cache_write": 2.0}}


def priced_usage(identity, *, tokens=MILLION, timestamp="2026-01-01T10:00:00Z", model="gpt-6-luna"):
    return event(
        "session.usage_record",
        {
            "usage": {
                "apiCallId": identity,
                "model": model,
                "inputTokens": tokens,
                "outputTokens": 0,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
            }
        },
        identity,
        timestamp,
    )


def rows_by(result, key):
    return {row[key]: row for row in result["rows"]}


def test_cost_by_project_attributes_each_sessions_usage_to_its_project(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a")], session_id="one", cwd="/work/app")
    import_copilot(store, tmp_path, [priced_usage("b", tokens=2 * MILLION)], session_id="two", cwd="/work/other")
    import_copilot(store, tmp_path, [priced_usage("c")], session_id="three")
    rows = rows_by(cost_by_project(store, prices=PRICES, zone="UTC"), "project")
    costs = {label.split(" (")[0]: row["estimated_cost_usd"] for label, row in rows.items()}
    assert costs == {"app": pytest.approx(2.0), "other": pytest.approx(4.0), "unknown": pytest.approx(2.0)}


def test_costs_report_unpriced_usage_so_a_partial_total_is_never_mistaken_for_complete(store, tmp_path):
    import_copilot(
        store, tmp_path, [priced_usage("a"), priced_usage("b", model="mystery")], session_id="one", cwd="/work/app"
    )
    (row,) = cost_by_project(store, prices=PRICES, zone="UTC")["rows"]
    assert row["estimated_cost_usd"] == pytest.approx(2.0)
    assert (row["priced_events"], row["unpriced_events"]) == (1, 1)


def test_cost_is_unavailable_not_zero_when_nothing_in_a_group_could_be_priced(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a", model="mystery")], cwd="/work/app")
    (row,) = cost_by_project(store, prices=PRICES, zone="UTC")["rows"]
    assert row["estimated_cost_usd"] is None and row["unpriced_events"] == 1


def test_cost_by_day_uses_the_requested_timezone_and_combines_providers(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [priced_usage("a", timestamp="2026-01-01T20:00:00Z"), priced_usage("b", timestamp="2026-01-02T10:00:00Z")],
    )
    utc = rows_by(cost_by_day(store, prices=PRICES, zone="UTC"), "day")
    tokyo = rows_by(cost_by_day(store, prices=PRICES, zone="Asia/Tokyo"), "day")
    assert set(utc) == {"2026-01-01", "2026-01-02"}
    assert set(tokyo) == {"2026-01-02"}
    assert tokyo["2026-01-02"]["estimated_cost_usd"] == pytest.approx(4.0)


def test_cost_total_sums_priced_usage_and_names_the_models_it_could_not_price(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a"), priced_usage("b", model="mystery")])
    import_claude(
        store,
        tmp_path,
        [claude_usage("m", input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)],
    )
    (row,) = cost_total(store, prices=PRICES, zone="UTC")["rows"]
    assert row["estimated_cost_usd"] == pytest.approx(2.0)
    assert (row["priced_events"], row["unpriced_events"]) == (1, 2)
    assert row["unpriced_models"] == "claude/claude-opus-5-5, copilot/mystery"


def test_cost_total_is_unavailable_when_no_prices_are_configured(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a")])
    (row,) = cost_total(store, prices={}, zone="UTC")["rows"]
    assert row["estimated_cost_usd"] is None


def quality_rows(store, **options):
    return session_quality(store, prices=options.pop("prices", PRICES), zone="UTC", **options)["rows"]


def test_active_time_excludes_idle_gaps_so_a_session_left_open_overnight_is_not_counted_as_work(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            tool_start("a", "bash", "2026-01-01T10:00:00Z"),
            tool_start("b", "bash", "2026-01-01T10:02:00Z"),
            tool_start("c", "bash", "2026-01-01T10:30:00Z"),
            tool_start("d", "bash", "2026-01-01T10:31:00Z"),
        ],
    )
    (row,) = quality_rows(store)
    assert row["active_minutes"] == pytest.approx(3.0)


def test_idle_threshold_is_adjustable_because_the_right_cut_off_depends_on_how_you_work(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [tool_start("a", "bash", "2026-01-01T10:00:00Z"), tool_start("b", "bash", "2026-01-01T10:31:00Z")],
    )
    (row,) = quality_rows(store, idle_minutes=60)
    assert row["active_minutes"] == pytest.approx(31.0)


def test_a_run_of_consecutive_failures_is_a_stuck_signal_that_a_pooled_error_rate_hides(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            *[tool_start(name, "bash", f"2026-01-01T10:0{index}:00Z") for index, name in enumerate("abcde")],
            tool_failure("a"),
            tool_failure("c"),
            tool_failure("d"),
            tool_failure("e"),
        ],
    )
    (row,) = quality_rows(store)
    assert (row["tool_calls"], row["tool_errors"], row["longest_error_streak"]) == (5, 4, 3)


def test_streaks_follow_time_order_not_import_order(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            tool_start("c", "bash", "2026-01-01T10:03:00Z"),
            tool_start("a", "bash", "2026-01-01T10:01:00Z"),
            tool_start("b", "bash", "2026-01-01T10:02:00Z"),
            tool_failure("a"),
            tool_failure("c"),
        ],
    )
    (row,) = quality_rows(store)
    assert row["longest_error_streak"] == 1


def test_ended_on_error_flags_sessions_abandoned_while_failing(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            tool_start("a", "bash", "2026-01-01T10:01:00Z"),
            tool_start("b", "bash", "2026-01-01T10:02:00Z"),
            tool_failure("b"),
        ],
        session_id="failing",
    )
    import_copilot(
        store,
        tmp_path,
        [
            tool_start("a", "bash", "2026-01-01T10:01:00Z"),
            tool_start("b", "bash", "2026-01-01T10:02:00Z"),
            tool_failure("a"),
        ],
        session_id="recovered",
    )
    rows = quality_rows(store)
    assert sorted(row["ended_on_error"] for row in rows) == [False, True]


def test_error_fields_are_unavailable_not_zero_for_sessions_with_no_tool_calls(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a")])
    (row,) = quality_rows(store)
    assert (row["tool_calls"], row["longest_error_streak"], row["ended_on_error"]) == (0, None, None)


def test_cost_per_active_hour_relates_spend_to_the_time_the_agent_was_actually_working(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [priced_usage("a", timestamp="2026-01-01T10:00:00Z"), tool_start("b", "bash", "2026-01-01T10:30:00Z")],
    )
    (row,) = quality_rows(store, idle_minutes=60)
    assert row["estimated_cost_usd"] == pytest.approx(2.0)
    assert row["cost_per_active_hour_usd"] == pytest.approx(4.0)


def test_cost_per_active_hour_is_unavailable_when_the_session_could_not_be_priced(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            priced_usage("a", model="mystery", timestamp="2026-01-01T10:00:00Z"),
            tool_start("b", "bash", "2026-01-01T10:30:00Z"),
        ],
    )
    (row,) = quality_rows(store, idle_minutes=60)
    assert (row["estimated_cost_usd"], row["cost_per_active_hour_usd"]) == (None, None)


def test_cost_per_active_hour_is_unavailable_when_no_active_time_was_observed(store, tmp_path):
    import_copilot(store, tmp_path, [priced_usage("a")])
    (row,) = quality_rows(store)
    assert row["estimated_cost_usd"] == pytest.approx(2.0)
    assert row["cost_per_active_hour_usd"] is None


def test_rows_name_the_project_and_a_short_session_id_and_never_a_quality_score(store, tmp_path):
    import_copilot(store, tmp_path, [tool_start("a", "bash")], cwd="/work/app")
    result = session_quality(store, prices=PRICES, zone="UTC")
    (row,) = result["rows"]
    assert row["project"].startswith("app (") and len(row["session"]) == 6
    assert not [column for column in result["columns"] if "score" in column]
    assert any("estimate" in note.lower() for note in result["notes"])


def test_session_quality_counts_user_steering_signals_per_session(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            event("user.message", {"source": "user"}, "u1"),
            event("user.message", {"source": "user"}, "u2"),
            event("abort", {"reason": "user initiated"}, "x"),
            event("permission.completed", {"requestId": "r", "result": {"kind": "denied-interactively-by-user"}}, "r"),
            event("session.compaction_complete", {"success": True}, "c"),
        ],
    )
    (row,) = quality_rows(store)
    assert (row["user_messages"], row["aborts"], row["permission_denials"], row["compactions"]) == (2, 1, 1, 1)


def test_session_quality_reports_zero_when_a_provider_can_observe_the_signal_and_none_occurred(store, tmp_path):
    import_copilot(store, tmp_path, [tool_start("a", "bash")])
    (row,) = quality_rows(store)
    assert (row["user_messages"], row["aborts"], row["permission_denials"], row["compactions"]) == (0, 0, 0, 0)


def test_session_quality_marks_signals_unavailable_for_providers_that_cannot_observe_them(store, tmp_path):
    import_claude(
        store,
        tmp_path,
        [
            {
                "type": "user",
                "uuid": "u",
                "sessionId": "claude-session",
                "timestamp": "2026-01-01T12:00:00Z",
                "message": {"role": "user", "content": "hello"},
            }
        ],
    )
    (row,) = quality_rows(store)
    assert row["user_messages"] == 1 and row["permission_denials"] == 0
    assert row["aborts"] is None and row["compactions"] is None


def call(identity, tool="bash", arguments=None, minute=0):
    return event(
        "tool.execution_start",
        {"toolName": tool, "toolCallId": identity, "arguments": arguments or {"command": "x"}},
        "start-" + identity,
        f"2026-01-01T10:{minute:02d}:00Z",
    )


def failed_with(identity, code):
    return event(
        "tool.execution_complete",
        {"toolCallId": identity, "success": False, "error": {"code": code}},
        "done-" + identity,
    )


def test_refused_permissions_are_counted_as_denials_not_as_tool_failures(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            call("a", minute=1),
            call("b", minute=2),
            call("c", minute=3),
            failed_with("a", "failure"),
            failed_with("b", "denied"),
            failed_with("c", "rejected"),
        ],
    )
    (row,) = quality_rows(store)
    assert (row["tool_calls"], row["tool_errors"], row["tool_denials"]) == (3, 1, 2)
    assert (row["longest_error_streak"], row["ended_on_error"]) == (1, False)


def test_identical_retries_count_repeats_of_a_call_that_already_failed(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            call("a", arguments={"command": "make test"}, minute=1),
            failed_with("a", "failure"),
            call("b", arguments={"command": "make test"}, minute=2),
            call("c", arguments={"command": "make lint"}, minute=3),
        ],
    )
    (row,) = quality_rows(store)
    assert (row["identical_retries"], row["max_identical_calls"]) == (1, 2)


def test_repeating_a_call_that_succeeded_is_not_a_retry_because_rereading_files_is_normal(store, tmp_path):
    import_copilot(store, tmp_path, [call("a", minute=1), call("b", minute=2), call("c", minute=3)])
    (row,) = quality_rows(store)
    assert (row["identical_retries"], row["max_identical_calls"]) == (0, 3)


def test_identical_calls_are_distinguished_by_tool_as_well_as_arguments(store, tmp_path):
    import_copilot(store, tmp_path, [call("a", tool="bash", minute=1), call("b", tool="view", minute=2)])
    (row,) = quality_rows(store)
    assert row["max_identical_calls"] == 1


def test_loop_and_denial_columns_are_unavailable_for_providers_without_the_evidence(store, tmp_path):
    record = {
        "type": "response_item",
        "timestamp": "2026-01-01T10:00:00Z",
        "payload": {"type": "function_call", "call_id": "c", "name": "shell", "arguments": "{}"},
    }
    (tmp_path / "codex").mkdir()
    store.import_file("codex", write_records(tmp_path / "codex", [record]))
    (row,) = quality_rows(store)
    assert (row["tool_denials"], row["identical_retries"], row["max_identical_calls"]) == (None, None, None)


def at(minute, second=0):
    return f"2026-01-01T10:{minute:02d}:{second:02d}Z"


def prompt(identity, minute):
    return event("user.message", {"source": "user"}, identity, at(minute))


def abort_at(minute):
    return event("abort", {"reason": "user initiated"}, f"abort-{minute}", at(minute))


def denial_at(minute):
    result = {"kind": "denied-interactively-by-user"}
    return event("permission.completed", {"requestId": f"r{minute}", "result": result}, f"r{minute}", at(minute))


def failure_at(identity, minute, second=0, **fields):
    data = {"toolCallId": identity, "success": False, **fields}
    return event("tool.execution_complete", data, "done-" + identity, at(minute, second))


def steering_rows(store, **options):
    return steering_follow_ups(store, zone="UTC", **options)["rows"]


def follow_up_counts(row):
    return (row["follow_up_prompts"], row["after_abort"], row["after_denial"], row["after_failure"])


def test_steering_attributes_each_follow_up_prompt_to_the_trigger_that_preceded_it(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            prompt("p0", 0),
            abort_at(1),
            prompt("p1", 2),
            denial_at(3),
            prompt("p2", 4),
            tool_start("a", "bash", at(5)),
            failure_at("a", 5),
            prompt("p3", 6),
        ],
    )
    (row,) = steering_rows(store)
    assert follow_up_counts(row) == (3, 1, 1, 1)
    assert row["reactive_share_percent"] == 100


def test_steering_ignores_the_first_prompt_and_prompts_that_follow_ordinary_work(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [prompt("p0", 0), tool_start("a", "bash", at(1)), prompt("p1", 2), prompt("p2", 3)],
    )
    (row,) = steering_rows(store)
    assert follow_up_counts(row) == (2, 0, 0, 0)
    assert row["reactive_share_percent"] == 0


def test_steering_does_not_blame_a_trigger_for_a_prompt_sent_after_the_idle_window(store, tmp_path):
    import_copilot(store, tmp_path, [prompt("p0", 0), abort_at(1), prompt("p1", 20)])
    assert follow_up_counts(steering_rows(store)[0]) == (1, 0, 0, 0)
    assert follow_up_counts(steering_rows(store, idle_minutes=30)[0]) == (1, 1, 0, 0)


def test_steering_counts_a_trigger_once_even_when_several_prompts_follow_it(store, tmp_path):
    import_copilot(store, tmp_path, [prompt("p0", 0), abort_at(1), prompt("p1", 2), prompt("p2", 3)])
    assert follow_up_counts(steering_rows(store)[0]) == (2, 1, 0, 0)


def test_steering_still_attributes_a_prompt_to_a_trigger_when_other_events_came_between(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [prompt("p0", 0), abort_at(1), tool_start("a", "bash", at(2)), copilot_usage("u", at(3)), prompt("p1", 4)],
    )
    assert follow_up_counts(steering_rows(store)[0]) == (1, 1, 0, 0)


def test_steering_does_not_treat_a_user_refusal_as_a_tool_failure(store, tmp_path):
    import_copilot(
        store,
        tmp_path,
        [
            prompt("p0", 0),
            tool_start("a", "bash", at(1)),
            denial_at(1),
            failure_at("a", 1, 30, error={"code": "rejected"}),
            prompt("p1", 2),
        ],
    )
    assert follow_up_counts(steering_rows(store)[0]) == (1, 0, 1, 0)


def test_steering_marks_signals_unavailable_for_providers_that_cannot_observe_them(store, tmp_path):
    def stamped(record, minute):
        return {**record, "sessionId": "claude-session", "timestamp": at(minute)}

    import_claude(
        store,
        tmp_path,
        [
            stamped(claude_user("p0", "hello"), 0),
            stamped(claude_tool_use("a", "Bash", {"command": "x"}), 1),
            stamped(
                claude_user("e", [{"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "x"}]), 2
            ),
            stamped(claude_user("p1", "try again"), 3),
        ],
    )
    (row,) = steering_rows(store)
    assert follow_up_counts(row) == (1, None, 0, 1)
    assert row["reactive_share_percent"] == 100


def test_steering_reports_no_share_when_a_session_has_no_follow_up_prompts(store, tmp_path):
    import_copilot(store, tmp_path, [tool_start("a", "bash", at(1))])
    (row,) = steering_rows(store)
    assert follow_up_counts(row) == (0, 0, 0, 0)
    assert row["reactive_share_percent"] is None


def test_steering_explains_its_inference_and_computes_no_score(store, tmp_path):
    import_copilot(store, tmp_path, [prompt("p0", 0)])
    result = steering_follow_ups(store, zone="UTC")
    assert not [column for column in result["columns"] if "score" in column]
    assert any("not read" in note.lower() for note in result["notes"])
