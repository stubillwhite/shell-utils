from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    session: str
    identity: str
    kind: str
    timestamp: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    tool: str | None = None
    child: bool = False
    project_id: str | None = None
    project: str | None = None
    signature: str | None = None
    cause: str | None = None


@dataclass(frozen=True)
class Diagnostic:
    line: int
    code: str


@dataclass(frozen=True)
class Collection:
    events: tuple[Metric, ...]
    diagnostics: tuple[Diagnostic, ...]


PROVIDERS = ("copilot", "codex", "claude")
PERMISSION_CAUSES = ("denied", "rejected")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_tokens", "reasoning_tokens")
