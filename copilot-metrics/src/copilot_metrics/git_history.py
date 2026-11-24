import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

GIT_TIMEOUT_SECONDS = 30
RECORD_MARK = "\x01"


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: str
    added: int
    deleted: int


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


def commits_between(*, root: str, since: str, until: str, idle_minutes: float) -> list[Commit] | None:
    email = (run_git(root, "config", "user.email") or "").strip()
    if not email:
        return None
    window_end = datetime.fromisoformat(until) + timedelta(minutes=idle_minutes)
    output = run_git(
        root,
        "log",
        "--all",
        "--no-merges",
        "--numstat",
        "--fixed-strings",
        f"--author=<{email}>",
        f"--since={since}",
        f"--until={window_end.isoformat()}",
        f"--format={RECORD_MARK}%h %cI",
    )
    return None if output is None else parse_commits(output)
