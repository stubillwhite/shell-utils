import json
import re
from datetime import datetime, timezone
from pathlib import Path

from copilot_metrics.models import PROVIDERS, Collection, Diagnostic, Metric

OBSERVATION_KINDS = (
    "permission_check",
    "clarification_check",
    "approval_notification",
    "guidance_notification",
    "elicitation_accept",
    "elicitation_decline",
    "elicitation_cancel",
)


def collect_interruptions(provider: str, path: Path) -> Collection:
    events: list[Metric] = []
    diagnostics: list[Diagnostic] = []
    fields = {"version", "provider", "session", "identity", "kind", "timestamp", "child"}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            diagnostics.append(Diagnostic(number, "invalid_json"))
            continue
        try:
            if (
                not isinstance(item, dict)
                or set(item) != fields
                or not isinstance(item["version"], int)
                or isinstance(item["version"], bool)
                or item["version"] != 1
                or provider not in PROVIDERS
                or item["provider"] != provider
                or item["kind"] not in OBSERVATION_KINDS
                or not isinstance(item["child"], bool)
                or any(
                    not isinstance(item[key], str) or re.fullmatch("[0-9a-f]{64}", item[key]) is None
                    for key in ("session", "identity")
                )
                or not isinstance(item["timestamp"], str)
            ):
                raise ValueError
            instant = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError
            events.append(
                Metric(
                    session=item["session"],
                    identity=item["identity"],
                    kind=item["kind"],
                    timestamp=instant.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                    child=item["child"],
                )
            )
        except (ValueError, TypeError):
            diagnostics.append(Diagnostic(number, "invalid_interruption_observation"))
    if not events and not diagnostics:
        diagnostics.append(Diagnostic(0, "no_timestamped_events"))
    return Collection(tuple(events), tuple(diagnostics))
