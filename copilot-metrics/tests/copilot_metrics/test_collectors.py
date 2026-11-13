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
