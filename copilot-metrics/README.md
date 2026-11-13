# Local copilot metrics

A macOS-first, offline CLI for historical GitHub Copilot CLI, Claude Code, and
Codex CLI metrics. Reads local JSONL sessions into DuckDB; exports terminal,
JSON, and CSV reports. No API credentials, uploads, daemon, or copilot configuration
changes are needed.

## Install and run

Requires Python 3.12+ and Poetry:

```sh
poetry env use python3.12
poetry install
poetry run copilot-metrics doctor
poetry run copilot-metrics import
poetry run copilot-metrics report
poetry run copilot-metrics report --group-by model --format json
poetry run copilot-metrics report --group-by project
poetry run copilot-metrics report --from 2026-01-01 --to 2026-01-31 --group-by day --format csv > metrics.csv
```

Make shortcuts: `make run-import`, `make model-report` (replaces `run-report`),
`make project-report`, and `make run-daily-report`.

`make run-daily-report` displays one stacked bar per observed day: `█` for input,
followed by `░` for output. It combines providers using only reported
`input_tokens` and `output_tokens`.
Cache and reasoning fields are not added. Provider token definitions differ, so
these combined counts are not directly comparable provider usage measures.
Separate exact counts accompany bars. Bar lengths share a scale based on the
largest daily input + output sum; this sum is used only for visualization.
Missing values remain
`unavailable`; `partial` marks counts where some sessions lack observations.
Observation coverage does not guarantee complete history. No missing days are
filled with inferred zeroes. Known segments remain visible when another category
is unavailable. Bars are omitted when terminal width cannot accommodate positive
segments; numeric labels remain.

Run the chart directly with filters, or retain the detailed table:

```sh
poetry run copilot-metrics report --group-by day --format chart --timezone UTC
poetry run copilot-metrics report --group-by day --format table
```

## Dashboard

After importing history, start the dark Streamlit dashboard:

```sh
poetry run copilot-metrics import
poetry run copilot-metrics dashboard
# Or: make run-dashboard
poetry run copilot-metrics dashboard --db /path/to/metrics.duckdb
```

Open the displayed loopback URL (normally `http://127.0.0.1:8502`). The server
runs in the foreground; stop it with Ctrl-C. It binds to `127.0.0.1`, disables
Streamlit usage telemetry, and sets a dark theme independently of the working
directory. Charts use local assets, not remote services. There is no
authentication: do not expose or proxy the server publicly.

The five views are **Overview** (provider summaries and daily trends),
**Attention signals** (attention evidence, daily trends and session drill-down),
**Models & Projects** (reported-token rankings and attribution),
**Sessions** (sortable observations and recorded spans), and **Data Quality**
(coverage, diagnostic codes, and metric definitions). CSV/JSON downloads retain
the current report filters; model/project selections apply only to their own
breakdown and downloads. Same-named project folders retain distinct hashed IDs.

**Attention signals** keeps historical inferred requests, hook checks and
notifications, and Claude MCP response outcomes distinct. Separate notification
summaries show sessions with observations against recorded sessions, not
instrumentation coverage. Select a category for provider-separated daily bars
and a focused session table with matching CSV/JSON downloads. The selection
affects only this tab; global provider/date/timezone filters still apply.
Missing days are not filled with zeroes, and unavailable counts stay blank.

These are observed signals, not an exact interruption count: categories overlap
and must not be added together. Claude notifications are delayed and may miss
quick answers. Codex currently supplies hook checks only. MCP outcomes do not
cover general approvals or Claude's question tool. Exact prompt counts and
human-wait duration remain unavailable. Session IDs are hashed; project/model
attribution and elapsed spans are omitted from the focused table.

If there are no imported hook observations within the filters, the tab retains
historical evidence and points to [hook setup and imports](#opt-in-interruption-observations).
It cannot determine whether hooks are installed or capture is complete.
Enable recording and import logs separately; the dashboard never accesses
transcripts, installs hooks, or changes assistant settings.

Sidebar filters default to all imported history, all providers, and the same
local timezone as CLI reports. Turn off **All imported history** to choose
inclusive From/To dates (initially today), or enter an IANA timezone such as
`America/New_York`. **Refresh imported data** rereads the database; it never
imports transcripts. The successful-read timestamp does not imply source logs
are current.

The dashboard opens short-lived read-only connections, never creates or migrates
a database, and does not read provider logs. Run imports separately: DuckDB may
reject access while another process writes. Let the importer finish and Refresh.
Missing, incompatible, locked, or unreadable databases produce a visible error.
Older schemas must be migrated by a CLI import, not by opening the dashboard.

Unavailable is not zero; partial coverage does not guarantee complete history.
Only the explicitly labeled combined daily input/output chart combines
providers. Other views and downloads remain provider-separated. No grand token
total, estimated billing, active-time estimate, or productivity score is added.
Cache/reasoning categories are never added to input/output. Session spans and
model/project session counts are not additive. Diagnostics cover all stored
imports for the chosen provider, independent of date filters.

## CLI reference

`run-app` and `python -m copilot_metrics` expose the same commands.
Every command accepts `--db PATH`. `doctor`, `import`, and `report` also accept
`--provider copilot|claude|codex`; dashboard provider filters are in its sidebar.
`doctor` only checks discovery; it does not read transcripts or create a database.
`import` explicitly reads transcripts locally to extract allowlisted metadata.

Default sources:

| Provider | Locations | Supported evidence |
|---|---|---|
| Copilot | `~/.copilot/session-state/**/events.jsonl` | `session.usage_record.data.usage`, tool execution events, permission requests |
| Claude | `~/.claude/projects/**/*.jsonl` | Assistant message usage, structured tool-use/result blocks |
| Codex | `$CODEX_HOME/{sessions,archived_sessions}/**/*.jsonl` (default `~/.codex`) | Rollout `event_msg/token_count` cumulative usage and `response_item/function_call` |

Use `--provider NAME --source-root PATH` on `doctor` or `import` to override a
root, or supply a single JSONL file. Paths are not shell-expanded by the CLI;
your shell expands an unquoted `~`. Missing or empty roots are visible diagnostics,
not silently successful imports.

Copilot and Claude field shapes were checked against bounded local samples.
**Codex support is experimental and synthetic-fixture validated only:** no local
Codex sessions were available, and upstream schema verification was blocked.
None of these on-disk schemas is assumed to be a stable vendor API. Fields outside
the supported shapes are not mined from free text.

## Metrics and limitations

- Table, JSON, and CSV reports separate providers, including when grouping by
  day, model, project, or session. The daily chart explicitly combines reported
  input and output counts across providers, separately. Only stacked-bar geometry
  uses their sum; no combined "total tokens" metric is stored or exported.
- Project reports break usage down by project, provider, and model, with the same
  token categories and coverage columns as model reports. Projects come from
  Copilot session-start `context.cwd`, Claude record `cwd`, and Codex session/turn
  `cwd`. These are working directories, not inferred Git repositories: subfolders
  and worktrees remain separate projects. Missing project evidence is `unknown`.
  The label is the directory basename; `project_id` is a hash of the normalized
  absolute directory, keeping same-named folders distinct without storing full
  paths. Labels reveal project names, so keep reports private. Reimport existing
  history with `make run-import` to populate project attribution. Old databases
  migrate on import; old records remain readable as `unknown` before reimport.
- Copilot usage is counted once per API call ID. Checkpoints/shutdown totals are
  not added again. Claude streaming snapshots merge by message ID using the largest
  reported value in each token category; tool blocks deduplicate by tool-use ID.
  Model attribution uses usage/message records, never the configured default.
- Codex cumulative counters become deltas; repeated snapshots add nothing.
  Counter decreases generate diagnostics rather than inventing usage. Codex models,
  permissions, and tool errors are currently unavailable. Turn-context model
  settings are not treated as proof of the model that actually served a request.
- Token categories are source-reported: Copilot and Codex cached input can be a
  subset of input, and reasoning can be a subset of output. Claude input, cache
  reads, and cache creation are separate categories. No categories are added into
  a potentially misleading grand total.
- `approval_requests` counts Copilot permission-request records; auto-approved
  requests may be included. `clarification_requests` counts structured `ask_user`,
  `AskUserQuestion`, or Codex `request_user_input` calls. These are **inferred
  interruptions**, not verified user-visible prompts or answered questions.
  `approval_prompts` is unavailable. Plain-text questions are never guessed.
- Tool errors count explicit Copilot `success=false` and Claude
  `tool_result.is_error=true`, not arbitrary text or shell exit-code parsing.
  Tool names are stored but reports currently aggregate tool counts.
- Null (`unavailable` in terminal output; empty CSV cell) means unavailable or
  unobserved, **not zero**. Observed counts may be incomplete. `*_sessions`
  coverage columns identify sessions with an observation for that category,
  not proof that the rest of their history was captured. Usage/model breakdowns
  are only as complete as the source logs.
- Session identifiers are hashed. Records with no session ID fall back to a
  hashed file path, so renamed files without session IDs cannot be deduplicated.
  Where evidence exists, child sessions are counted separately from top-level
  sessions; unrecognized child relationships cannot be inferred. Identified
  copied/resumed records deduplicate by provider, session, event identity, and kind.
  Separate exports lacking stable IDs or historical cumulative baselines may
  still overlap; import the original complete session files when possible.
- Elapsed duration is first-to-last recorded event, **not active work time**.
  Date filters clip to selected events. Daily groups use their own observed
  spans; model groups share session spans and are not additive. Concurrent
  session spans are summed, not merged into wall-clock working time.
- `--from` and `--to` are inclusive calendar dates, implemented with an exclusive
  next-day boundary. Timestamps are normalized to UTC on import. Reports use
  `TZ` or macOS's `/etc/localtime` zone (UTC fallback) and show the zone.
  Override with `--timezone America/New_York` or `--timezone UTC`.

JSON includes definitions and import diagnostics. CSV sends those notices to
stderr so stdout remains machine-readable. Diagnostics in reports cover all
stored history for the selected provider, independent of date filters.

## Opt-in interruption observations

The local hook recorder supports Copilot CLI, Claude Code, and Codex CLI.
Nothing is installed or enabled automatically. To explicitly install global
hooks for all three providers:

```sh
make install-interruption-hooks
# Or select one provider:
make install-interruption-hooks PROVIDER=copilot
# Equivalent CLI, with an optional custom log directory:
poetry run copilot-metrics install-interruption-hooks --provider copilot \
  --log-dir "$HOME/.local/state/copilot-metrics"
```

The installer merges into `~/.copilot/hooks/metrics.json`,
`~/.claude/settings.json`, and `~/.codex/hooks.json`, preserving unrelated
settings and hook handlers. `COPILOT_HOME` and `CODEX_HOME` override their
respective configuration roots. The default log directory is
`~/.local/state/copilot-metrics`, with one `<provider>.jsonl` file per provider.
No logs or database are created during installation.

Rerunning updates this recorder's generated handlers (including interpreter
and output paths) without duplicating them; unchanged configs are not rewritten.
All selected configs are validated before any writes. Malformed JSON, duplicate
keys, unsupported hook structures, symlink files, and incompatible Copilot
versions produce errors without replacing configs. Changed configs are replaced
atomically per file with `0600` permissions; new directories are private.
Existing parent-directory permissions are preserved. A later write failure can
leave earlier providers installed; the error says so and rerunning is safe.
The installer cannot deduplicate hooks in other files or inline TOML settings,
so remove duplicate recorder configurations there yourself.

Restart the selected assistants and complete any required hook trust review.
The installer does not override disabled-hook settings or bypass trust, and
imports remain manual.

Alternatively, generate configuration for manual review and installation,
choosing a separate absolute log filename per provider:

```sh
poetry run copilot-metrics interruption-hooks --provider copilot \
  --output "$HOME/.local/state/copilot-metrics/copilot.jsonl"
poetry run copilot-metrics interruption-hooks --provider claude \
  --output "$HOME/.local/state/copilot-metrics/claude.jsonl"
poetry run copilot-metrics interruption-hooks --provider codex \
  --output "$HOME/.local/state/copilot-metrics/codex.jsonl"
```

Each generation command prints JSON without writing any files. Review it before enabling:
save the Copilot JSON as a dedicated `~/.copilot/hooks/metrics.json` file; merge
the Claude `hooks` entries into `~/.claude/settings.json`; merge the Codex
entries into `~/.codex/hooks.json`. Preserve existing hooks and settings.
Use user-level configurations, not shared repository configurations: Copilot
can also load repository-level Claude hooks, causing duplicate observations.
Restart the relevant assistant and complete any required hook trust review.
Codex hooks must be enabled. These configurations target the current documented
hook formats; older installed versions may not support all events.

Generated commands use this Poetry environment's absolute Python executable and
an absolute output path, so they also work outside this repository. Regenerate
them if you move or remove the environment. Logs are created on the first
relevant event. New directories are private and log permissions are `0600`.
Writes are locked and flushed for concurrent hooks on macOS/Linux.
The recorder emits no stdout or approval decisions. Invalid input and write
failures exit nonzero with a content-free error; a failing `PreToolUse` hook
can block the assistant's tool call. Remove the generated entries to disable
recording. No daemon, exporter, network service, or content capture is required.

| Report columns | Meaning |
|---|---|
| `permission_checks` | Permission hooks, including requests handled automatically; not displayed prompts |
| `clarification_checks` | Structured question-tool pre-execution hooks; not proof of display or answer |
| `approval_notifications` | Copilot/Claude permission attention notifications |
| `guidance_notifications` | Copilot/Claude elicitation attention notifications |
| `elicitation_accepts`, `elicitation_declines`, `elicitation_cancels` | Claude MCP elicitation response-hook outcomes; not general question or approval outcomes |

Notifications are observed attention signals, **not an exact interruption
count**. Claude permission and MCP elicitation notifications are delayed by
about six seconds, so quick answers may be missed. Codex currently contributes
checks only, not notifications or confirmed human outcomes. Claude
`AskUserQuestion` contributes a clarification check, not an MCP outcome.
Idle/completion notifications and plain-text questions are ignored.
No categories above should be added together: they can describe the same
interaction. `approval_prompts` and `human_wait_seconds` remain unavailable:
these hooks do not provide correlated prompt-display/resolution timestamps.

The recorder immediately discards hook content. It stores only a schema version,
provider, hashed session/event IDs, observation category, UTC receipt timestamp,
and child-agent flag when supplied. It never writes questions, answers, commands,
tool arguments/results, or paths. No project attribution is inferred.
Each invocation gets its own ID because these hook payloads do not consistently
provide stable notification IDs. Copied log records deduplicate on import;
repeated hook deliveries cannot be identified and count separately.

Import each log explicitly, then use existing CLI reports or refresh the
dashboard:

```sh
poetry run copilot-metrics import --provider copilot --interruptions \
  --source-root "$HOME/.local/state/copilot-metrics/copilot.jsonl"
poetry run copilot-metrics report --provider copilot --group-by day --format json
```

Repeat with `claude`/`codex` and their log paths. Ordinary transcript imports
never discover recorder logs. Observations remain separate from historical
request counts and retain the same date filters, coverage columns, and
provider-separated exports. Missing observations mean unavailable, not zero.
Imports do not delete or rotate logs; keep them private and retain them for
repeatable imports. Synthetic tests validate payload classification and the
record/import/report pipeline, not live UI delivery or complete capture.

Provider hook references:
[Copilot](https://docs.github.com/en/copilot/reference/hooks-reference),
[Claude](https://code.claude.com/docs/en/hooks),
[Codex](https://developers.openai.com/codex/hooks).

## Persistence, errors, and privacy

Default database: `metrics.duckdb` in the current working directory (the project
folder when running Make here). Override with `--db PATH`. The project's
`.gitignore` excludes this database and its `.wal`/`.tmp` sidecars. It contains only
hashed identities, project folder labels, timestamps, model/tool names, numeric usage, event categories,
coverage evidence, and diagnostic codes. It does **not** contain prompts, responses,
tool arguments/results, commands, code, or full source paths. Provider logs remain
untouched. Reports can still reveal usage patterns: treat database and exports
as private.

Database/journal creation uses owner-only permissions; new database directories
are private, and existing parent-directory permissions are preserved. The CLI
is single-threaded; the storage helper temporarily changes the process umask
while creating files. DuckDB supports one writing process at a time; locking
errors are surfaced, not retried silently.

Imports hash and rescan changed files, replace their normalized records in a
transaction, and leave unchanged imports alone. Missing source files do not
delete stored history. Parser revisions also trigger a rescan of unchanged files
to refresh metrics and diagnostics. Claude file-history snapshots are recognized
non-metric metadata, not unsupported transcripts. A source changing during a scan is reported as a read
failure; run the import again when the session is idle. Malformed lines and
truncated final lines produce diagnostics with line numbers, without raw content.
Valid records in the same file remain importable; later complete rescans replace
those diagnostics. Fatal database errors roll back the file transaction.

Exit codes: `0` success, `1` operational/database/date/timezone failure, `2`
partial import/missing source or argparse usage error. An import can persist
valid data and still return `2`; inspect stderr. Imports display source paths and
line numbers for diagnostics in selected files, including unchanged files. Remove the database and any
associated `.wal`/`.tmp` files only when no process is using it to erase imported
metrics; provider histories are independent.

Live hooks/telemetry, editor extensions, pricing, billing, and
productivity scoring are outside this version.

## Development

```sh
poetry run pytest --cov
poetry run isort --check-only src tests
poetry run black --check src tests
poetry run ruff check
poetry run mypy src tests
poetry build
```

Tests use synthetic records, not private transcripts. They cover collector
semantics, malformed/partial inputs, transaction rollback, repeat/growing imports,
cross-process CLI persistence, date boundaries/DST, privacy, and exports.
`make` defaults to Python 3.12; override `PYTHON_VERSION` for another interpreter.