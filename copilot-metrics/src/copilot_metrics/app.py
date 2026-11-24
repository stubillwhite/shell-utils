import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import duckdb

from copilot_metrics.collectors import opaque
from copilot_metrics.dashboard_launcher import launch
from copilot_metrics.hook_installation import HookInstallError, install_hooks
from copilot_metrics.insights import (
    DEFAULT_IDLE_MINUTES,
    cache_efficiency,
    cost_by_day,
    cost_by_model,
    cost_by_project,
    cost_total,
    project_friction,
    session_quality,
    tool_profile,
)
from copilot_metrics.interruptions import hook_config, record_hook
from copilot_metrics.models import PROVIDERS
from copilot_metrics.prices import load_prices
from copilot_metrics.reports import render, report
from copilot_metrics.storage import Store


def roots(provider: str) -> tuple[Path, ...]:
    home = Path.home()
    codex = Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
    return {
        "copilot": (home / ".copilot/session-state",),
        "claude": (home / ".claude/projects",),
        "codex": (codex / "sessions", codex / "archived_sessions"),
    }[provider]


def files(provider: str, root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,)
    pattern = "events.jsonl" if provider == "copilot" else "*.jsonl"
    return tuple(sorted(root.rglob(pattern)))


INSIGHT_TOPICS: dict[str, Callable[..., dict[str, Any]]] = {
    "tools": tool_profile,
    "cache": cache_efficiency,
    "friction": project_friction,
    "sessions": session_quality,
    "cost": cost_by_model,
    "cost-project": cost_by_project,
    "cost-day": cost_by_day,
    "cost-total": cost_total,
}


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Local-only historical coding-assistant metrics.")
    commands = cli.add_subparsers(dest="command", required=True)
    for command in ("doctor", "import", "report", "dashboard"):
        sub = commands.add_parser(command)
        sub.add_argument("--db", type=Path, default=Path("metrics.duckdb"))
        if command == "dashboard":
            continue
        sub.add_argument("--provider", choices=PROVIDERS)
        if command in ("doctor", "import"):
            sub.add_argument("--source-root", type=Path)
            if command == "import":
                sub.add_argument(
                    "--interruptions", action="store_true", help="Import a normalized hook log explicitly."
                )
        else:
            sub.add_argument("--from", dest="start")
            sub.add_argument("--to", dest="end")
            sub.add_argument("--timezone", dest="zone")
            sub.add_argument(
                "--group-by", choices=("day", "provider", "model", "session", "project"), default="provider"
            )
            sub.add_argument("--format", choices=("table", "json", "csv", "chart"), default="table")
    insights = commands.add_parser("insights", help="Work-pattern reports derived from imported history.")
    insights.add_argument("topic", choices=tuple(INSIGHT_TOPICS))
    insights.add_argument("--db", type=Path, default=Path("metrics.duckdb"))
    insights.add_argument("--provider", choices=PROVIDERS)
    insights.add_argument("--from", dest="start")
    insights.add_argument("--to", dest="end")
    insights.add_argument("--timezone", dest="zone")
    insights.add_argument("--prices", type=Path, default=Path("prices.toml"), help="Per-million-token rates (cost).")
    insights.add_argument(
        "--idle-minutes",
        type=float,
        default=DEFAULT_IDLE_MINUTES,
        help="Sessions topic: gaps longer than this are idle, not active time.",
    )
    insights.add_argument("--format", choices=("table", "json", "csv"), default="table")
    for command in ("record-interruption", "interruption-hooks"):
        sub = commands.add_parser(command)
        sub.add_argument("--provider", choices=PROVIDERS, required=True)
        sub.add_argument("--output", type=Path, required=True)
        if command == "record-interruption":
            sub.add_argument(
                "--event",
                choices=("Notification", "PermissionRequest", "PreToolUse", "ElicitationResult"),
                required=True,
            )
    installer = commands.add_parser("install-interruption-hooks", help="Merge recorder hooks into global configs.")
    installer.add_argument("--provider", choices=PROVIDERS, help="Defaults to all three providers.")
    installer.add_argument("--log-dir", type=Path, help="Defaults to ~/.local/state/copilot-metrics.")
    return cli


def execute(args: argparse.Namespace) -> int:
    if args.command == "install-interruption-hooks":
        for provider, path, installed in install_hooks(provider=args.provider, log_dir=args.log_dir):
            print(f"{provider}: {'installed' if installed else 'unchanged'} ({path})")
        print("Restart the selected assistants and review hook trust if requested. Log imports remain manual.")
        return 0
    if args.command == "interruption-hooks":
        print(json.dumps(hook_config(provider=args.provider, output=args.output), indent=2))
        return 0
    if args.command == "record-interruption":
        content = sys.stdin.read(1_048_577)
        if len(content) > 1_048_576:
            raise ValueError("hook_input_too_large")
        record_hook(provider=args.provider, event=args.event, payload=json.loads(content), output=args.output)
        return 0
    if args.command == "dashboard":
        return launch(args.db)
    if args.command == "insights":
        extra = {
            **(
                {"prices": load_prices(args.prices)}
                if args.topic.startswith("cost") or args.topic == "sessions"
                else {}
            ),
            **({"idle_minutes": args.idle_minutes} if args.topic == "sessions" else {}),
        }
        with Store(args.db, read_only=True) as store:
            result = INSIGHT_TOPICS[args.topic](
                store, start=args.start, end=args.end, provider=args.provider, zone=args.zone, **extra
            )
        print(render(result, args.format))
        return 0
    if args.command == "report":
        with Store(args.db, read_only=True) as store:
            result = report(
                store,
                start=args.start,
                end=args.end,
                provider=args.provider,
                group_by=args.group_by,
                zone=args.zone,
            )
        print(render(result, args.format, width=shutil.get_terminal_size(fallback=(80, 24)).columns))
        if args.format == "csv":
            print("CSV nulls are empty cells. " + " ".join(result["notes"]), file=sys.stderr)
            for diagnostic in result["diagnostics"]:
                print(f'{diagnostic["provider"]}: {diagnostic["code"]} ({diagnostic["count"]})', file=sys.stderr)
        return 0
    providers = (args.provider,) if args.provider else PROVIDERS
    discovered = [
        (provider, root)
        for provider in providers
        for root in ((args.source_root,) if args.source_root is not None else roots(provider))
    ]
    available: list[tuple[str, Path]] = []
    partial = False
    for provider, root in discovered:
        found = files(provider, root)
        if not found:
            partial = True
            print(
                f"{provider}: missing or empty source ({root})",
                file=sys.stderr if args.command == "import" else sys.stdout,
            )
        available.extend((provider, path) for path in found)
        if args.command == "doctor":
            print(f"{provider}: {len(found)} candidate files; schema validation occurs on import")
    if args.command == "doctor":
        print(
            "Approval prompts unavailable; clarification/approval requests are inferred. Codex: fixture-validated only."
        )
        return 2 if partial else 0
    if not available:
        return 2
    with Store(args.db) as store:
        changed = 0
        for provider, path in available:
            try:
                changed += store.import_file(provider, path, interruptions=args.interruptions)
            except (OSError, UnicodeError):
                print(f"{provider}: source_read_failed ({path})", file=sys.stderr)
                partial = True
            diagnostics = store.connection.execute(
                "SELECT line, code FROM diagnostics WHERE source_id = ? ORDER BY line",
                [opaque(provider + (":interruptions:" if args.interruptions else ":") + str(path.resolve()))],
            ).fetchall()
            for line, code in diagnostics:
                print(f"{provider}: {path}: line {line}: {code}", file=sys.stderr)
            partial = partial or bool(diagnostics)
        print(f"Imported {changed} changed files; {len(available) - changed} unchanged or failed.")
    return 2 if partial else 0


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if args.command == "report" and args.format == "chart" and args.group_by != "day":
        cli.error("--format chart requires --group-by day")
    if getattr(args, "source_root", None) is not None and args.provider is None:
        cli.error("--source-root requires --provider")
    if getattr(args, "interruptions", False) and (
        args.provider is None or args.source_root is None or not args.source_root.is_file()
    ):
        cli.error("--interruptions requires --provider and --source-root pointing to a log file")
    try:
        status = execute(args)
    except HookInstallError as error:
        print(str(error), file=sys.stderr)
        status = 1
    except (OSError, ValueError, ZoneInfoNotFoundError, duckdb.Error) as error:
        print(
            f"Operation failed ({type(error).__name__}); check paths, database and date/timezone arguments.",
            file=sys.stderr,
        )
        status = 1
    raise SystemExit(status)
