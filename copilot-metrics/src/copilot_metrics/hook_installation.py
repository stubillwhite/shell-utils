import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from copilot_metrics.interruptions import hook_config
from copilot_metrics.models import PROVIDERS
from copilot_metrics.storage import private_creation


class HookInstallError(ValueError):
    pass


def managed_handler(handler: dict[str, Any], *, provider: str) -> bool:
    command = handler.get("command")
    if not isinstance(command, str) or handler.get("type", "command") != "command":
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    return (
        len(arguments) == 10
        and arguments[1:5] == ["-m", "copilot_metrics", "record-interruption", "--provider"]
        and arguments[5] == provider
        and arguments[6] == "--event"
        and arguments[7] in ("Notification", "PermissionRequest", "PreToolUse", "ElicitationResult")
        and arguments[8] == "--output"
    )


def retained_entry(entry: dict[str, Any], *, provider: str) -> dict[str, Any] | None:
    if managed_handler(entry, provider=provider):
        return None
    if "hooks" not in entry:
        return entry
    handlers = [handler for handler in entry["hooks"] if not managed_handler(handler, provider=provider)]
    if handlers == entry["hooks"]:
        return entry
    return {**entry, "hooks": handlers} if handlers else None


def merged_config(original: object, *, provider: str, output: Path) -> dict[str, Any]:
    if not isinstance(original, dict):
        raise ValueError("invalid_config")
    hooks = original.get("hooks", {})
    if not isinstance(hooks, dict) or any(
        not isinstance(entries, list)
        or any(
            not isinstance(entry, dict)
            or (
                "hooks" in entry
                and (
                    not isinstance(entry["hooks"], list)
                    or any(not isinstance(handler, dict) for handler in entry["hooks"])
                )
            )
            for entry in entries
        )
        for entries in hooks.values()
    ):
        raise ValueError("invalid_hooks")
    if (
        provider == "copilot"
        and "version" in original
        and (
            not isinstance(original["version"], int)
            or isinstance(original["version"], bool)
            or original["version"] != 1
        )
    ):
        raise ValueError("unsupported_version")
    generated = hook_config(provider=provider, output=output)
    retained = {
        event: [kept for entry in entries if (kept := retained_entry(entry, provider=provider)) is not None]
        for event, entries in hooks.items()
    }
    return {
        **original,
        **({"version": 1} if provider == "copilot" else {}),
        "hooks": {
            **retained,
            **{event: retained.get(event, []) + entries for event, entries in generated["hooks"].items()},
        },
    }


def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate_config_key")
    return dict(pairs)


def config_bytes(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError("symlink_config")
    return path.read_bytes() if path.exists() else None


def write_config(path: Path, *, content: bytes, previous: bytes | None) -> None:
    with private_creation():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix=".metrics-hooks-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        if config_bytes(path) != previous:
            raise ValueError("config_changed_during_install")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_hooks(*, provider: str | None = None, log_dir: Path | None = None) -> tuple[tuple[str, Path, bool], ...]:
    home = Path.home()
    paths = {
        "copilot": Path(os.environ.get("COPILOT_HOME", str(home / ".copilot"))) / "hooks/metrics.json",
        "claude": home / ".claude/settings.json",
        "codex": Path(os.environ.get("CODEX_HOME", str(home / ".codex"))) / "hooks.json",
    }
    directory = (log_dir if log_dir is not None else home / ".local/state/copilot-metrics").resolve()
    prepared = []
    for name in (provider,) if provider else PROVIDERS:
        path = paths[name]
        try:
            previous = config_bytes(path)
            original = json.loads(previous, object_pairs_hook=unique_fields) if previous is not None else {}
            merged = merged_config(original, provider=name, output=directory / f"{name}.jsonl")
            content = (json.dumps(merged, indent=2) + "\n").encode()
            changed = previous is None or merged != original
            prepared.append((name, path, previous, content, changed))
        except (OSError, UnicodeError, ValueError) as error:
            raise HookInstallError(
                f"{name}: config validation failed; check {path}. No configs were written."
            ) from error
    for name, path, previous, content, changed in prepared:
        if not changed:
            continue
        try:
            write_config(path, content=content, previous=previous)
        except (OSError, ValueError) as error:
            raise HookInstallError(
                f"{name}: config write failed; check {path}. Earlier providers may already be installed; rerun safely."
            ) from error
    return tuple((name, path, changed) for name, path, _, _, changed in prepared)
