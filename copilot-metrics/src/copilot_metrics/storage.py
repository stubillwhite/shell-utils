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
from copilot_metrics.models import Collection


@contextmanager
def private_creation() -> Iterator[None]:
    mask = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(mask)


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
                CREATE TABLE IF NOT EXISTS diagnostics (
                    source_id VARCHAR, provider VARCHAR, line BIGINT, code VARCHAR
                );
                DROP VIEW IF EXISTS normalized_events;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS project_id VARCHAR;
                ALTER TABLE events ADD COLUMN IF NOT EXISTS project VARCHAR;
                CREATE VIEW normalized_events AS
                SELECT events.* FROM events JOIN sources USING (source_id)
                QUALIFY row_number() OVER (
                    PARTITION BY provider, session, identity, kind
                    ORDER BY updated DESC, source_id
                ) = 1;
            """
        )

    def __enter__(self: "Store") -> "Store":
        return self

    def __exit__(
        self: "Store", exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.connection.close()

    def import_file(self: "Store", provider: str, path: Path, *, interruptions: bool = False) -> bool:
        source_id = opaque(provider + (":interruptions:" if interruptions else ":") + str(path.resolve()))
        content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digest = ("interruptions-1" if interruptions else COLLECTOR_REVISION) + ":" + content_digest
        previous = self.connection.execute("SELECT digest FROM sources WHERE source_id = ?", [source_id]).fetchone()
        if previous and previous[0] == digest:
            return False
        collection = collect_interruptions(provider, path) if interruptions else collect(provider, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != content_digest:
            raise OSError("source_changed_during_import")
        with private_creation():
            self.connection.execute("BEGIN")
            try:
                self.connection.execute("DELETE FROM events WHERE source_id = ?", [source_id])
                self.connection.execute("DELETE FROM diagnostics WHERE source_id = ?", [source_id])
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
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(source_id, provider, *astuple(event)) for event in collection.events],
            )
        if collection.diagnostics:
            self.connection.executemany(
                "INSERT INTO diagnostics VALUES (?, ?, ?, ?)",
                [(source_id, provider, item.line, item.code) for item in collection.diagnostics],
            )
