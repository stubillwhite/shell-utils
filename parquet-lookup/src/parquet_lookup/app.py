import argparse
import concurrent.futures as futures
import logging
import pathlib
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    paths: Sequence[str]


@dataclass(frozen=True)
class _ParquetFile:
    filesystem: pafs.FileSystem
    path: str
    stored_path: str


def _dataset_name(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("dataset name must not be empty")
    return value


def _filesystem_path(path: str) -> str:
    return path.removeprefix("s3://")


def _stored_path(dataset_path: str, file_path: str) -> str:
    return f"s3://{file_path}" if dataset_path.startswith("s3://") else file_path


def _normalized_path(path: str) -> str:
    if path.startswith("s3://"):
        if not path.removeprefix("s3://").strip("/"):
            raise ValueError("S3 dataset paths must include a bucket")
        return path
    return str(pathlib.Path(path).expanduser().resolve())


def _s3_filesystem(region: str | None) -> pafs.S3FileSystem:
    return pafs.S3FileSystem(region=region) if region else pafs.S3FileSystem()


def list_parquet_files(filesystem: pafs.FileSystem, dataset_path: str) -> list[str]:
    root = _filesystem_path(dataset_path).rstrip("/")
    file_info = filesystem.get_file_info(root)
    if file_info.type == pafs.FileType.File:
        return [file_info.path] if file_info.path.endswith(".parquet") else []
    selector = pafs.FileSelector(root, recursive=True)
    return sorted(
        info.path
        for info in filesystem.get_file_info(selector)
        if info.type == pafs.FileType.File and info.path.endswith(".parquet")
    )


def index_one_file(
    filesystem: pafs.FileSystem,
    file_id: int,
    file_path: str,
    key_column: str = "key",
    content_column: str = "xml_content",
) -> pa.Table:
    logger.info("Processing %s", file_path)
    parquet_file = pq.ParquetFile(_filesystem_path(file_path), filesystem=filesystem)
    available_columns = set(parquet_file.schema_arrow.names)
    missing_columns = {key_column, content_column} - available_columns
    if missing_columns:
        raise ValueError(
            f"{file_path} missing columns: {', '.join(sorted(missing_columns))}; "
            f"available columns: {', '.join(sorted(available_columns))}"
        )
    tables = [
        pa.table(
            {
                "key": parquet_file.read_row_group(row_group, columns=[key_column]).column(key_column),
                "file_id": pa.array([file_id] * parquet_file.metadata.row_group(row_group).num_rows, type=pa.int32()),
                "row_group": pa.array(
                    [row_group] * parquet_file.metadata.row_group(row_group).num_rows,
                    type=pa.int32(),
                ),
            }
        )
        for row_group in range(parquet_file.num_row_groups)
    ]
    if not tables:
        return pa.table(
            {
                "key": pa.array([], type=pa.string()),
                "file_id": pa.array([], type=pa.int32()),
                "row_group": pa.array([], type=pa.int32()),
            }
        )
    return pa.concat_tables(tables)


def _create_index_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY,
            dataset VARCHAR NOT NULL,
            file_path VARCHAR NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS key_index (
            "key" VARCHAR NOT NULL,
            file_id INTEGER NOT NULL,
            row_group INTEGER NOT NULL
        )
        """
    )
    connection.execute('CREATE INDEX IF NOT EXISTS idx_key_index ON key_index("key")')
    connection.execute("CREATE INDEX IF NOT EXISTS idx_files_id ON files(file_id)")
    connection.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS key_column VARCHAR DEFAULT 'key'")
    connection.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS content_column VARCHAR DEFAULT 'xml_content'")


def _resolve_dataset_sources(
    *,
    dataset: DatasetSpec,
    region: str | None,
    filesystem: pafs.FileSystem | None,
) -> list[tuple[str, pafs.FileSystem]]:
    return [
        (
            _normalized_path(path),
            filesystem or (_s3_filesystem(region) if path.startswith("s3://") else pafs.LocalFileSystem()),
        )
        for path in dataset.paths
    ]


def _discover_parquet_files(
    *,
    dataset: DatasetSpec,
    sources: Sequence[tuple[str, pafs.FileSystem]],
    max_files: int | None,
) -> list[_ParquetFile]:
    parquet_files: list[_ParquetFile] = []
    seen_paths: set[str] = set()
    for dataset_path, active_filesystem in sources:
        logger.info("[%s] Scanning %s", dataset.name, dataset_path)
        discovered = list_parquet_files(active_filesystem, dataset_path)
        logger.info("[%s] Found %d parquet files in %s", dataset.name, len(discovered), dataset_path)
        for file_path in discovered:
            stored_path = _stored_path(dataset_path, file_path)
            if stored_path not in seen_paths:
                parquet_files.append(_ParquetFile(active_filesystem, file_path, stored_path))
                seen_paths.add(stored_path)
    if max_files is not None:
        logger.info("[%s] Limiting to first %d parquet files", dataset.name, max_files)
        parquet_files = parquet_files[:max_files]
    return parquet_files


def _index_parquet_files(
    *,
    connection: duckdb.DuckDBPyConnection,
    dataset_name: str,
    parquet_files: Sequence[_ParquetFile],
    first_file_id: int,
    workers: int,
    key_column: str,
    content_column: str,
) -> int:
    file_rows = [
        (first_file_id + offset, dataset_name, parquet_file.stored_path, key_column, content_column)
        for offset, parquet_file in enumerate(parquet_files)
    ]
    if file_rows:
        connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?)", file_rows)
    total_keys = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(
                index_one_file,
                parquet_file.filesystem,
                first_file_id + offset,
                parquet_file.path,
                key_column,
                content_column,
            ): parquet_file.path
            for offset, parquet_file in enumerate(parquet_files)
        }
        for completed_count, future in enumerate(futures.as_completed(pending), start=1):
            table = future.result()
            connection.register("index_batch", table)
            try:
                connection.execute('INSERT INTO key_index SELECT "key", file_id, row_group FROM index_batch')
            finally:
                connection.unregister("index_batch")
            total_keys += table.num_rows
            logger.info(
                "[%s] Indexed %s (%d keys) [%d/%d]",
                dataset_name,
                pending[future],
                table.num_rows,
                completed_count,
                len(parquet_files),
            )
    return total_keys


def build_key_index(
    *,
    db_path: str,
    dataset: DatasetSpec,
    region: str | None = None,
    workers: int = 16,
    max_files: int | None = None,
    key_column: str = "key",
    content_column: str = "xml_content",
    filesystem: pafs.FileSystem | None = None,
) -> None:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be at least 1")
    if not key_column or not content_column:
        raise ValueError("column names must not be empty")
    if not dataset.name.strip():
        raise ValueError("dataset name must not be empty")
    if not dataset.paths or any(not path.strip() for path in dataset.paths):
        raise ValueError("at least one non-empty dataset path is required")
    sources = _resolve_dataset_sources(dataset=dataset, region=region, filesystem=filesystem)
    started = time.monotonic()
    connection = duckdb.connect(db_path)
    _create_index_schema(connection)
    connection.execute("BEGIN TRANSACTION")
    committed = False
    try:
        next_file_id_row = connection.execute("SELECT COALESCE(MAX(file_id), -1) + 1 FROM files").fetchone()
        if next_file_id_row is None:
            raise RuntimeError("failed to allocate the next file ID")
        next_file_id = int(next_file_id_row[0])
        connection.execute(
            "DELETE FROM key_index WHERE file_id IN (SELECT file_id FROM files WHERE dataset = ?)",
            [dataset.name],
        )
        connection.execute("DELETE FROM files WHERE dataset = ?", [dataset.name])
        parquet_files = _discover_parquet_files(
            dataset=dataset,
            sources=sources,
            max_files=max_files,
        )
        total_files = len(parquet_files)
        logger.info("[%s] Indexing %d parquet files with %d workers", dataset.name, total_files, workers)
        total_keys = _index_parquet_files(
            connection=connection,
            dataset_name=dataset.name,
            parquet_files=parquet_files,
            first_file_id=next_file_id,
            workers=workers,
            key_column=key_column,
            content_column=content_column,
        )
        connection.execute("COMMIT")
        committed = True
        connection.execute("CHECKPOINT")
        logger.info(
            "[%s] Done: %d keys from %d files in %.1fs -> %s",
            dataset.name,
            total_keys,
            total_files,
            time.monotonic() - started,
            db_path,
        )
    except Exception:
        if not committed:
            logger.error("[%s] Build failed; previous index for this dataset retained", dataset.name)
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def fetch_xml(
    filesystem: pafs.FileSystem,
    file_path: str,
    row_group: int,
    key: str,
    key_column: str = "key",
    content_column: str = "xml_content",
) -> str | None:
    parquet_file = pq.ParquetFile(_filesystem_path(file_path), filesystem=filesystem)
    table = parquet_file.read_row_group(row_group, columns=[key_column, content_column])
    filtered = table.filter(pc.equal(table.column(key_column), key))
    if filtered.num_rows == 0:
        return None
    xml = filtered.column(content_column)[0].as_py()
    if not isinstance(xml, str):
        raise TypeError("xml_content must contain strings")
    return xml


def _find_locations(
    connection: duckdb.DuckDBPyConnection,
    key: str,
    datasets: Sequence[str] | None,
) -> list[tuple[str, str, int, str, str]]:
    query = """
        SELECT f.dataset, f.file_path, k.row_group, f.key_column, f.content_column
        FROM key_index k
        JOIN files f ON k.file_id = f.file_id
        WHERE k."key" = ?
    """
    parameters: list[str] = [key]
    if datasets:
        query += f" AND f.dataset IN ({','.join('?' for _ in datasets)})"
        parameters.extend(datasets)
    query += " ORDER BY f.dataset, f.file_path, k.row_group"
    return connection.execute(query, parameters).fetchall()


def _process_lookup_locations(
    *,
    locations: Sequence[tuple[str, str, int, str, str]],
    key: str,
    region: str | None,
    out_dir: pathlib.Path | None,
    filesystem: pafs.FileSystem | None,
    stdout: TextIO,
    stderr: TextIO,
) -> bool:
    filesystems: dict[str, pafs.FileSystem] = {}
    stale_entry_found = False
    for dataset_name, file_path, row_group, key_column, content_column in locations:
        kind = "s3" if file_path.startswith("s3://") else "local"
        if kind not in filesystems:
            filesystems[kind] = filesystem or (_s3_filesystem(region) if kind == "s3" else pafs.LocalFileSystem())
        xml = fetch_xml(filesystems[kind], file_path, row_group, key, key_column, content_column)
        if xml is None:
            stale_entry_found = True
            print(
                f"[{dataset_name}] not found in expected row group (index may be stale)",
                file=stderr,
            )
            continue
        if out_dir:
            output_path = out_dir / f"{dataset_name}.xml"
            output_path.write_text(xml, encoding="utf-8")
            continue
        print(xml, file=stdout)
    return stale_entry_found


def lookup_key(
    *,
    db_path: str,
    key: str,
    datasets: Sequence[str] | None = None,
    region: str | None = None,
    out_dir: str | None = None,
    filesystem: pafs.FileSystem | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    connection = duckdb.connect(db_path, read_only=True)
    try:
        locations = _find_locations(connection, key, datasets)
    finally:
        connection.close()
    if not locations:
        print(f"Key '{key}' not found in index", file=stderr)
        return 1
    if len(locations) > 1:
        print(f"Key '{key}' has multiple matches; use --dataset to narrow the lookup", file=stderr)
        return 1

    output_directory = pathlib.Path(out_dir) if out_dir else None
    if output_directory:
        output_directory.mkdir(parents=True, exist_ok=True)
    stale_entry_found = _process_lookup_locations(
        locations=locations,
        key=key,
        region=region,
        out_dir=output_directory,
        filesystem=filesystem,
        stdout=stdout,
        stderr=stderr,
    )
    return 1 if stale_entry_found else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query an external index for Parquet datasets.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-index", help="Build or replace dataset entries in the local index.")
    build.add_argument(
        "--dataset",
        action="append",
        required=True,
        type=_dataset_name,
        help="Name of the dataset being built; exactly one per build.",
    )
    build.add_argument(
        "--path",
        action="append",
        required=True,
        help="Local or s3:// Parquet path; repeat to combine multiple paths in one dataset.",
    )
    build.add_argument("--db", default="key_index.duckdb")
    build.add_argument("--region")
    build.add_argument("--workers", type=int, default=16)
    build.add_argument("--max-files", type=int)
    build.add_argument("--key-column", default="key")
    build.add_argument("--content-column", default="xml_content")

    lookup = commands.add_parser("lookup", help="Look up XML content by key.")
    lookup.add_argument("key")
    lookup.add_argument("--db", default="key_index.duckdb")
    lookup.add_argument("--dataset", action="append", help="Restrict lookup to a named dataset; repeatable.")
    lookup.add_argument("--region")
    lookup.add_argument("--out-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "build-index":
        if len(arguments.dataset) != 1:
            parser.error("build-index accepts exactly one --dataset")
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            build_key_index(
                db_path=arguments.db,
                dataset=DatasetSpec(arguments.dataset[0], tuple(arguments.path)),
                region=arguments.region,
                workers=arguments.workers,
                max_files=arguments.max_files,
                key_column=arguments.key_column,
                content_column=arguments.content_column,
            )
        finally:
            logger.removeHandler(handler)
        return 0
    return lookup_key(
        db_path=arguments.db,
        key=arguments.key,
        datasets=arguments.dataset,
        region=arguments.region,
        out_dir=arguments.out_dir,
    )
