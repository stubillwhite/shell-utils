import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import astuple
from pathlib import Path
from types import TracebackType

import duckdb

from copilot_metrics.collectors import COLLECTOR_REVISION, collect, opaque
from copilot_metrics.collectors.interruptions import collect_interruptions
from copilot_metrics.git_history import commits_between, repository_root, rework_between
from copilot_metrics.models import Collection


@contextmanager
def private_creation() -> Iterator[None]:
    mask = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(mask)


GIT_DIRECTORIES_TABLE = (
    "CREATE TABLE IF NOT EXISTS git_session_dirs (source_id VARCHAR, session VARCHAR, cwd VARCHAR, repo VARCHAR)"
)
GIT_COMMITS_TABLE = (
    "CREATE TABLE IF NOT EXISTS git_commits "
    "(repo VARCHAR, sha VARCHAR, committed_at VARCHAR, added BIGINT, deleted BIGINT)"
)
GIT_REWORK_TABLE = (
    "CREATE TABLE IF NOT EXISTS git_rework "
    "(repo VARCHAR, sha VARCHAR, origin_sha VARCHAR, lines BIGINT, committed_at VARCHAR)"
)


class Store:
    def __init__(self: "Store", path: Path, *, read_only: bool = False):
        if read_only:
            self.connection = duckdb.connect(str(path), read_only=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with private_creation():
            self.connection = duckdb.connect(str(path))
            self._initialize(path)

    def _initialize(self: "Store", path: Path) -> None:
        path.chmod(0o600)
        self.connection.execute(
            """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id VARCHAR PRIMARY KEY, digest VARCHAR, updated TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS events (
                    source_id VARCHAR, provider VARCHAR, session VARCHAR, identity VARCHAR,
                    kind VARCHAR, timestamp VARCHAR, model VARCHAR, input_tokens BIGINT,
                    output_tokens BIGINT, cached_input_tokens BIGINT, cache_creation_tokens BIGINT,
                    reasoning_tokens BIGINT, tool VARCHAR, child BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS settings (name VARCHAR PRIMARY KEY, value VARCHAR);
                CREATE TABLE IF NOT EXISTS diagnostics (
                    source_id VARCHAR, provider VARCHAR, line BIGINT, code VARCHAR
                );
                DROP VIEW IF EXISTS normalized_events;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS project_id VARCHAR;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS project VARCHAR;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS signature VARCHAR;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS cause VARCHAR;
                CREATE VIEW normalized_events AS
                SELECT events.* FROM events JOIN sources USING (source_id)
                QUALIFY row_number() OVER (
                    PARTITION BY provider, session, identity, kind
                    ORDER BY updated DESC, source_id
                ) = 1;
            """
        )

    def git_enabled(self: "Store") -> bool:
        try:
            row = self.connection.execute("SELECT value FROM settings WHERE name = 'git_enabled'").fetchone()
        except duckdb.CatalogException:
            return False
        return row is not None and row[0] == "on"

    def set_git_enabled(self: "Store", enabled: bool) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO settings VALUES ('git_enabled', ?)", ["on" if enabled else "off"]
        )
        if not enabled:
            self.purge_git_data()

    def purge_git_data(self: "Store") -> None:
        names = self.connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'git\\_%' ESCAPE '\\'"
        ).fetchall()
        for (name,) in names:
            self.connection.execute(f'DROP TABLE "{name}"')

    def __enter__(self: "Store") -> "Store":
        return self

    def __exit__(
        self: "Store", exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.connection.close()

    def import_file(self: "Store", provider: str, path: Path, *, interruptions: bool = False) -> bool:
        source_id = opaque(provider + (":interruptions:" if interruptions else ":") + str(path.resolve()))
        content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        revision = "interruptions-1" if interruptions else COLLECTOR_REVISION + (":git" if self.git_enabled() else "")
        digest = revision + ":" + content_digest
        previous = self.connection.execute("SELECT digest FROM sources WHERE source_id = ?", [source_id]).fetchone()
        if previous and previous[0] == digest:
            return False
        collection = (
            collect_interruptions(provider, path)
            if interruptions
            else collect(provider, path, keep_directories=self.git_enabled())
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != content_digest:
            raise OSError("source_changed_during_import")
        with private_creation():
            self.connection.execute("BEGIN")
            try:
                self.connection.execute("DELETE FROM events WHERE source_id = ?", [source_id])
                self.connection.execute("DELETE FROM diagnostics WHERE source_id = ?", [source_id])
                if self.git_enabled():
                    self.connection.execute(GIT_DIRECTORIES_TABLE)
                    self.connection.execute("DELETE FROM git_session_dirs WHERE source_id = ?", [source_id])
                self.connection.execute(
                    "INSERT OR REPLACE INTO sources VALUES (?, ?, current_timestamp)", [source_id, digest]
                )
                self._insert(source_id, provider, collection)
                self.connection.execute("COMMIT")
            except (duckdb.Error, OSError, ValueError):
                self.connection.execute("ROLLBACK")
                raise
        return True

    def _insert(self: "Store", source_id: str, provider: str, collection: Collection) -> None:
        if collection.events:
            self.connection.executemany(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(source_id, provider, *astuple(event)) for event in collection.events],
            )
        if collection.diagnostics:
            self.connection.executemany(
                "INSERT INTO diagnostics VALUES (?, ?, ?, ?)",
                [(source_id, provider, item.line, item.code) for item in collection.diagnostics],
            )
        if collection.directories and self.git_enabled():
            self.connection.executemany(
                "INSERT INTO git_session_dirs VALUES (?, ?, ?, NULL)",
                [(source_id, session, cwd) for session, cwd in collection.directories],
            )

    def sync_git(self: "Store", *, idle_minutes: float) -> None:
        if not self.git_enabled() or not self.has_table("git_session_dirs"):
            return
        self.connection.execute(GIT_COMMITS_TABLE)
        self.connection.execute(GIT_REWORK_TABLE)
        spans = self.connection.execute(
            """
                SELECT d.cwd, min(e.timestamp), max(e.timestamp) FROM git_session_dirs d
                JOIN normalized_events e USING (session) GROUP BY d.cwd
            """
        ).fetchall()
        located = [(cwd, repository_root(cwd), first, last) for cwd, first, last in spans]
        self.connection.execute("UPDATE git_session_dirs SET repo = NULL")
        for root in {root for _, root, _, _ in located if root}:
            windows = [(first, last) for _, other, first, last in located if other == root]
            commits = commits_between(
                root=root,
                since=min(first for first, _ in windows),
                until=max(last for _, last in windows),
                idle_minutes=idle_minutes,
            )
            if commits is None:
                continue
            repo = opaque(root)
            self.connection.execute("DELETE FROM git_commits WHERE repo = ?", [repo])
            if commits:
                self.connection.executemany(
                    "INSERT INTO git_commits VALUES (?, ?, ?, ?, ?)",
                    [(repo, commit.sha, commit.committed_at, commit.added, commit.deleted) for commit in commits],
                )
            self.connection.execute("DELETE FROM git_rework WHERE repo = ?", [repo])
            rework = rework_between(
                root=root,
                origins=commits,
                since=min(first for first, _ in windows),
                until=max(last for _, last in windows),
                idle_minutes=idle_minutes,
            )
            if rework:
                self.connection.executemany(
                    "INSERT INTO git_rework VALUES (?, ?, ?, ?, ?)",
                    [(repo, edge.sha, edge.origin_sha, edge.lines, edge.committed_at) for edge in rework],
                )
            self.connection.executemany(
                "UPDATE git_session_dirs SET repo = ? WHERE cwd = ?",
                [(repo, cwd) for cwd, other, _, _ in located if other == root],
            )

    def has_table(self: "Store", name: str) -> bool:
        return (
            self.connection.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()
            is not None
        )
