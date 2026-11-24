import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

GIT_TIMEOUT_SECONDS = 30
RECORD_MARK = "\x01"
REWORK_DAYS = 14
MAX_HUNKS_PER_COMMIT = 200
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: str
    added: int
    deleted: int


@dataclass(frozen=True)
class Rework:
    sha: str
    origin_sha: str
    lines: int
    committed_at: str


def run_git(directory: str, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", directory, *arguments],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def repository_root(directory: str) -> str | None:
    root = run_git(directory, "rev-parse", "--show-toplevel")
    return root.strip() or None if root else None


def count_lines(value: str) -> int:
    return int(value) if value.isdigit() else 0


def parse_commits(output: str) -> list[Commit]:
    commits = []
    for record in output.split(RECORD_MARK)[1:]:
        header, *stats = record.strip("\n").split("\n")
        sha, committed_at = header.split(" ")
        fields = [line.split("\t")[:2] for line in stats if line.count("\t") >= 2]
        instant = datetime.fromisoformat(committed_at).astimezone(timezone.utc)
        commits.append(
            Commit(
                sha=sha,
                committed_at=instant.isoformat(timespec="microseconds"),
                added=sum(count_lines(added) for added, _ in fields),
                deleted=sum(count_lines(deleted) for _, deleted in fields),
            )
        )
    return commits


def own_commits(root: str, *, since: str, until: datetime, numstat: bool) -> list[Commit] | None:
    email = (run_git(root, "config", "user.email") or "").strip()
    if not email:
        return None
    output = run_git(
        root,
        "log",
        "--all",
        "--no-merges",
        *(["--numstat"] if numstat else []),
        "--fixed-strings",
        f"--author=<{email}>",
        f"--since={since}",
        f"--until={until.isoformat()}",
        f"--format={RECORD_MARK}%h %cI",
    )
    return None if output is None else parse_commits(output)


def commits_between(*, root: str, since: str, until: str, idle_minutes: float) -> list[Commit] | None:
    window_end = datetime.fromisoformat(until) + timedelta(minutes=idle_minutes)
    return own_commits(root, since=since, until=window_end, numstat=True)


def removed_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    ranges: dict[str, list[tuple[int, int]]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            path = line[len("--- a/") :].split("\t")[0] if line.startswith("--- a/") else None
            continue
        match = HUNK.match(line)
        if path and match:
            start = int(match[1])
            count = 1 if match[2] is None else int(match[2])
            if count:
                ranges.setdefault(path, []).append((start, start + count - 1))
    return ranges


def blamed_lines(root: str, *, revision: str, path: str, spans: list[tuple[int, int]]) -> dict[str, int]:
    selections = [argument for start, end in spans for argument in ("-L", f"{start},{end}")]
    output = run_git(root, "blame", "--porcelain", *selections, revision, "--", path)
    counts: dict[str, int] = {}
    origin = None
    for line in (output or "").splitlines():
        header = BLAME_HEADER.match(line)
        if header:
            origin = header[1]
        elif line.startswith("\t") and origin:
            counts[origin] = counts.get(origin, 0) + 1
    return counts


def origins_rewritten_by(root: str, commit: Commit) -> dict[str, int]:
    diff = run_git(
        root,
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        f"{commit.sha}^",
        commit.sha,
    )
    ranges = removed_ranges(diff or "")
    hunks = [(path, span) for path, spans in ranges.items() for span in spans][:MAX_HUNKS_PER_COMMIT]
    totals: dict[str, int] = {}
    for path in {path for path, _ in hunks}:
        spans = [span for owner, span in hunks if owner == path]
        for origin, lines in blamed_lines(root, revision=f"{commit.sha}^", path=path, spans=spans).items():
            totals[origin] = totals.get(origin, 0) + lines
    return totals


def within_rework_window(origin: Commit, later: Commit) -> bool:
    lag = datetime.fromisoformat(later.committed_at) - datetime.fromisoformat(origin.committed_at)
    return timedelta(0) <= lag <= timedelta(days=REWORK_DAYS)


def rework_between(*, root: str, origins: list[Commit], since: str, until: str, idle_minutes: float) -> list[Rework]:
    window_end = datetime.fromisoformat(until) + timedelta(minutes=idle_minutes, days=REWORK_DAYS)
    later_commits = own_commits(root, since=since, until=window_end, numstat=False) or []
    return [
        Rework(sha=later.sha, origin_sha=origin.sha, lines=lines, committed_at=later.committed_at)
        for later in later_commits
        for full, lines in origins_rewritten_by(root, later).items()
        for origin in origins
        if full.startswith(origin.sha) and origin.sha != later.sha and within_rework_window(origin, later)
    ]
