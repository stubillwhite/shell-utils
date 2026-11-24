import json

import pytest

from copilot_metrics.collectors import collect


def write_records(tmp_path, records):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def event(kind, data, identity="one", timestamp="2026-01-01T10:00:00Z"):
    return {"id": identity, "type": kind, "data": data, "timestamp": timestamp}


def test_copilot_usage_deduplicates_api_calls_without_counting_checkpoint_totals(tmp_path):
    usage = {"apiCallId": "call", "model": "actual", "inputTokens": 100, "outputTokens": 12, "cacheReadTokens": 80}
    path = write_records(
        tmp_path,
        [
            event("session.start", {"sessionId": "session", "selectedModel": "auto"}),
            event("session.usage_record", {"usage": usage}, "usage1"),
            event("session.usage_record", {"usage": usage}, "usage2"),
            event("session.usage_checkpoint", {"inputTokens": 999}, "checkpoint"),
        ],
    )
    result = collect("copilot", path)
    usages = [item for item in result.events if item.kind == "usage"]
    assert len(usages) == 1
    assert usages[0].input_tokens == 100
    assert usages[0].cached_input_tokens == 80
    assert usages[0].model == "actual"


def test_interruption_requests_are_not_mistaken_for_visible_approval_prompts(tmp_path):
    result = collect(
        "copilot",
        write_records(
            tmp_path,
            [
                event("permission.requested", {"requestId": "approval"}),
                event("permission.completed", {"requestId": "approval", "decisionSource": "automatic"}, "completed"),
                event("tool.execution_start", {"toolName": "ask_user", "toolCallId": "ask"}, "question"),
                event("tool.execution_complete", {"toolCallId": "ask", "success": False}, "failure"),
            ],
        ),
    )
    assert [item.kind for item in result.events].count("approval_request") == 1
    assert not any(item.kind == "approval_prompt" for item in result.events)
    assert [item.kind for item in result.events].count("clarification_request") == 1
    assert [item.kind for item in result.events].count("tool_error") == 1


def test_claude_streaming_snapshots_merge_usage_and_tool_blocks(tmp_path):
    def message(output, blocks):
        return {
            "type": "assistant",
            "uuid": f"chunk-{output}",
            "sessionId": "session",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "id": "message",
                "model": "claude",
                "usage": {"input_tokens": 10, "output_tokens": output, "cache_read_input_tokens": 90},
                "content": blocks,
            },
        }

    block = {"type": "tool_use", "id": "ask", "name": "AskUserQuestion", "input": {"private": "DO NOT STORE"}}
    result = collect("claude", write_records(tmp_path, [message(1, [block]), message(8, [])]))
    usages = [item for item in result.events if item.kind == "usage"]
    assert len(usages) == 1
    assert usages[0].input_tokens == 10
    assert usages[0].output_tokens == 8
    assert usages[0].cached_input_tokens == 90
    assert len([item for item in result.events if item.kind == "tool_call"]) == 1
    assert "DO NOT STORE" not in repr(result)


def test_codex_cumulative_usage_becomes_deltas_and_retains_unknown_model(tmp_path):
    def record(total, timestamp):
        return {
            "type": "event_msg",
            "timestamp": timestamp,
            "payload": {"type": "token_count", "info": {"total_token_usage": total}},
        }

    result = collect(
        "codex",
        write_records(
            tmp_path,
            [
                record({"input_tokens": 100, "output_tokens": 10}, "2026-01-01T10:00:00Z"),
                record({"input_tokens": 100, "output_tokens": 10}, "2026-01-01T10:01:00Z"),
                record({"input_tokens": 140, "output_tokens": 20}, "2026-01-01T10:02:00Z"),
            ],
        ),
    )
    usages = [item for item in result.events if item.kind == "usage"]
    assert sum(item.input_tokens for item in usages) == 140
    assert sum(item.output_tokens for item in usages) == 20
    assert all(item.model is None for item in usages)


def test_malformed_and_truncated_records_are_visible_without_content(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"private": broken}\n{"partial":')
    result = collect("copilot", path)
    assert [(item.line, item.code) for item in result.diagnostics] == [(1, "invalid_json"), (2, "truncated_tail")]
    assert "private" not in repr(result)


def test_invalid_usage_and_unknown_schemas_are_diagnosed(tmp_path):
    path = write_records(
        tmp_path,
        [event("session.usage_record", {"usage": {"inputTokens": -1}}), {"type": "future.schema"}],
    )
    result = collect("copilot", path)
    assert "invalid_usage" in {item.code for item in result.diagnostics}
    assert "unsupported_schema" in {item.code for item in result.diagnostics}


def test_usage_snapshots_keep_reported_categories_when_a_later_chunk_omits_them(tmp_path):
    path = write_records(
        tmp_path,
        [
            event("session.usage_record", {"usage": {"apiCallId": "a", "inputTokens": 10, "outputTokens": 8}}, "a"),
            event("session.usage_record", {"usage": {"apiCallId": "a", "outputTokens": 2}}, "b"),
        ],
    )
    usage = [item for item in collect("copilot", path).events if item.kind == "usage"][0]
    assert usage.input_tokens == 10
    assert usage.output_tokens == 8


def test_timestamp_free_usage_is_not_silently_discarded(tmp_path):
    result = collect(
        "copilot",
        write_records(tmp_path, [{"type": "session.usage_record", "data": {"usage": {"inputTokens": 10}}}]),
    )
    assert "missing_timestamp" in {item.code for item in result.diagnostics}


def test_cumulative_counter_reset_is_diagnosed_not_added_as_new_usage(tmp_path):
    records = [
        {
            "type": "event_msg",
            "timestamp": f"2026-01-01T10:0{index}:00Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": value}}},
        }
        for index, value in enumerate((100, 50, 140))
    ]
    result = collect("codex", write_records(tmp_path, records))
    assert sum(item.input_tokens for item in result.events if item.kind == "usage") == 140
    assert "cumulative_usage_decreased" in {item.code for item in result.diagnostics}


def test_claude_child_records_do_not_reclassify_subsequent_parent_records(tmp_path):
    records = [
        {
            "type": "assistant",
            "uuid": identity,
            "sessionId": "parent",
            "agentId": agent,
            "isSidechain": sidechain,
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"id": identity, "usage": {"input_tokens": 10}},
        }
        for identity, agent, sidechain in (("child", "agent", True), ("parent", "", False))
    ]
    usages = [item for item in collect("claude", write_records(tmp_path, records)).events if item.kind == "usage"]
    assert [item.child for item in usages] == [True, False]


def test_claude_subagent_files_without_agent_ids_remain_distinct(tmp_path):
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    record = {
        "type": "assistant",
        "sessionId": "parent",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {"id": "message", "usage": {"input_tokens": 10}},
    }
    sessions = []
    for name in ("agent-one.jsonl", "agent-two.jsonl"):
        path = subagents / name
        path.write_text(json.dumps(record) + "\n")
        sessions.append(collect("claude", path).events[0].session)
    assert sessions[0] != sessions[1]


def test_claude_file_history_metadata_is_not_an_unsupported_transcript(tmp_path):
    snapshot = {"type": "file-history-snapshot", "messageId": "m", "snapshot": {}, "isSnapshotUpdate": False}
    path = write_records(tmp_path, [snapshot])
    assert collect("claude", path).diagnostics == ()
    records = [
        snapshot,
        {
            "type": "assistant",
            "sessionId": "s",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"id": "m", "usage": {"input_tokens": 10}},
        },
        {"type": "future.schema"},
    ]
    result = collect("claude", write_records(tmp_path, records))
    assert [(item.line, item.code) for item in result.diagnostics] == [(3, "unsupported_schema")]
    assert sum(item.input_tokens for item in result.events if item.kind == "usage") == 10


def test_claude_file_history_delta_metadata_is_not_an_unsupported_transcript(tmp_path):
    delta = {
        "type": "file-history-delta",
        "messageId": "m",
        "snapshotMessageId": "s",
        "trackingPath": "file.py",
        "backup": {"backupFileName": "b@v1", "version": 1},
        "timestamp": "2026-01-01T10:00:00Z",
    }
    path = write_records(tmp_path, [delta])
    assert collect("claude", path).diagnostics == ()


@pytest.mark.parametrize("provider", ["copilot", "claude", "codex"])
def test_projects_use_recorded_cwd_without_persisting_full_paths(tmp_path, provider):
    cwd = "/private/work/customer/app"
    records = {
        "copilot": [
            event("session.start", {"sessionId": "s", "context": {"cwd": cwd}}),
            event("session.usage_record", {"usage": {"apiCallId": "a", "inputTokens": 10}}, "usage"),
        ],
        "claude": [
            {
                "type": "assistant",
                "sessionId": "s",
                "cwd": cwd,
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {"id": "m", "usage": {"input_tokens": 10}},
            }
        ],
        "codex": [
            {"type": "session_meta", "timestamp": "2026-01-01T10:00:00Z", "payload": {"id": "s", "cwd": cwd}},
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T10:01:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 10}},
                },
            },
        ],
    }
    result = collect(provider, write_records(tmp_path, records[provider]))
    usage = next(item for item in result.events if item.kind == "usage")
    assert usage.project == "app"
    assert usage.project_id
    assert cwd not in repr(result)


def kinds_of(result, kind):
    return [item for item in result.events if item.kind == kind]


def copilot_session(tmp_path, records):
    return collect("copilot", write_records(tmp_path, [event("session.start", {"sessionId": "s"}, "start"), *records]))


def test_copilot_user_messages_count_only_prompts_the_user_typed_and_never_store_their_text(tmp_path):
    result = copilot_session(
        tmp_path,
        [
            event("user.message", {"source": "user", "content": "DO NOT STORE"}, "u1"),
            event("user.message", {"source": "user", "content": "DO NOT STORE"}, "u2"),
            event("user.message", {"source": "memory", "content": "DO NOT STORE"}, "m"),
            event("user.message", {"source": "autopilot", "content": "DO NOT STORE"}, "a"),
            event("user.message", {"source": "system", "content": "DO NOT STORE"}, "y"),
        ],
    )
    assert len(kinds_of(result, "user_message")) == 2
    assert "DO NOT STORE" not in repr(result)


def test_copilot_aborts_are_recorded_so_interrupted_work_is_visible(tmp_path):
    result = copilot_session(
        tmp_path, [event("abort", {"reason": "user initiated"}, "x1"), event("abort", {"reason": "user_abort"}, "x2")]
    )
    assert len(kinds_of(result, "abort")) == 2


def test_copilot_permission_denials_count_only_refusals_by_the_user_and_never_store_feedback(tmp_path):
    def completed(identity, kind, **result):
        return event("permission.completed", {"requestId": identity, "result": {"kind": kind, **result}}, identity)

    result = copilot_session(
        tmp_path,
        [
            completed("r1", "approved"),
            completed("r2", "approved-for-location"),
            completed("r3", "cancelled", reason="Session aborted"),
            completed("r4", "denied-interactively-by-user", feedback="DO NOT STORE"),
            completed("r5", "denied-no-approval-rule-and-could-not-request-from-user"),
            completed("r6", "denied-by-permission-request-hook"),
        ],
    )
    assert len(kinds_of(result, "permission_denied")) == 1
    assert "DO NOT STORE" not in repr(result)


def test_copilot_compactions_count_successful_completions_not_starts_or_failures(tmp_path):
    result = copilot_session(
        tmp_path,
        [
            event("session.compaction_start", {"trigger": "auto"}, "c0"),
            event("session.compaction_complete", {"success": True}, "c1"),
            event("session.compaction_complete", {"success": False}, "c2"),
            event("session.compaction_complete", {"outcome": "applied"}, "c3"),
        ],
    )
    assert len(kinds_of(result, "compaction")) == 2


def claude_user(identity, content, **fields):
    return {
        "type": "user",
        "uuid": identity,
        "sessionId": "session",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {"role": "user", "content": content},
        **fields,
    }


def test_claude_user_messages_count_typed_main_session_prompts_only(tmp_path):
    result = collect(
        "claude",
        write_records(
            tmp_path,
            [
                claude_user("typed", "DO NOT STORE", origin={"kind": "human"}),
                claude_user("legacy", "DO NOT STORE"),
                claude_user("meta", "DO NOT STORE", isMeta=True),
                claude_user("injected", "DO NOT STORE", origin={"kind": "system"}),
                claude_user("sidechain", "DO NOT STORE", isSidechain=True),
                claude_user("tool", [{"type": "tool_result", "tool_use_id": "t", "content": "DO NOT STORE"}]),
            ],
        ),
    )
    assert len(kinds_of(result, "user_message")) == 2
    assert "DO NOT STORE" not in repr(result)


def test_claude_permission_denials_count_only_rejections_by_the_user_not_by_rules(tmp_path):
    def decided(identity, decision, source="user_reject"):
        return claude_user(
            identity,
            [{"type": "tool_result", "tool_use_id": identity, "content": "DO NOT STORE"}],
            permissionDecision={"decision": decision, "source": source},
        )

    result = collect(
        "claude",
        write_records(
            tmp_path,
            [decided("a", "accept"), decided("r", "reject"), decided("c", "reject", source="config")],
        ),
    )
    assert len(kinds_of(result, "permission_denied")) == 1
    assert "DO NOT STORE" not in repr(result)


def copilot_call(identity, tool, arguments):
    return event("tool.execution_start", {"toolName": tool, "toolCallId": identity, "arguments": arguments}, identity)


def test_copilot_call_signatures_match_identical_calls_and_never_expose_arguments(tmp_path):
    secret = {"command": "DO NOT STORE"}
    result = copilot_session(
        tmp_path,
        [
            copilot_call("a", "bash", secret),
            copilot_call("b", "bash", dict(reversed(list(secret.items())))),
            copilot_call("c", "bash", {"command": "other"}),
            copilot_call("d", "view", secret),
        ],
    )
    signatures = {item.identity: item.signature for item in kinds_of(result, "tool_call")}
    first, second, different_args, different_tool = signatures.values()
    assert first == second and len({first, different_args, different_tool}) == 3
    assert "DO NOT STORE" not in repr(result)


def test_copilot_error_causes_separate_real_failures_from_permission_outcomes(tmp_path):
    def failed(identity, error):
        return event("tool.execution_complete", {"toolCallId": identity, "success": False, "error": error}, identity)

    result = copilot_session(
        tmp_path,
        [
            failed("f", {"code": "failure", "message": "DO NOT STORE"}),
            failed("d", {"code": "denied"}),
            failed("r", {"code": "rejected"}),
            failed("n", {"message": "no code"}),
        ],
    )
    causes = {item.identity: item.cause for item in kinds_of(result, "tool_error")}
    assert sorted(causes.values()) == ["denied", "failure", "failure", "rejected"]
    assert "DO NOT STORE" not in repr(result)


def claude_tool_use(identity, name, tool_input):
    return {
        "type": "assistant",
        "uuid": "msg-" + identity,
        "sessionId": "session",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {
            "id": "m-" + identity,
            "content": [{"type": "tool_use", "id": identity, "name": name, "input": tool_input}],
        },
    }


def test_claude_call_signatures_hash_the_tool_and_input_without_storing_the_input(tmp_path):
    result = collect(
        "claude",
        write_records(
            tmp_path,
            [
                claude_tool_use("a", "Bash", {"command": "DO NOT STORE"}),
                claude_tool_use("b", "Bash", {"command": "DO NOT STORE"}),
                claude_tool_use("c", "Bash", {"command": "other"}),
            ],
        ),
    )
    first, second, third = (item.signature for item in kinds_of(result, "tool_call"))
    assert first == second != third
    assert "DO NOT STORE" not in repr(result)


def test_claude_error_causes_follow_the_permission_decision_on_the_same_record(tmp_path):
    def errored(identity, **fields):
        return claude_user(
            identity, [{"type": "tool_result", "tool_use_id": identity, "is_error": True, "content": "x"}], **fields
        )

    result = collect(
        "claude",
        write_records(
            tmp_path,
            [
                errored("plain"),
                errored("user", permissionDecision={"decision": "reject", "source": "user_reject"}),
                errored("rule", permissionDecision={"decision": "reject", "source": "config"}),
            ],
        ),
    )
    causes = {item.identity: item.cause for item in kinds_of(result, "tool_error")}
    assert sorted(causes.values()) == ["denied", "failure", "rejected"]
