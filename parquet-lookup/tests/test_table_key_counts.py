import subprocess
import sys
from pathlib import Path

import duckdb

SCRIPT = Path(__file__).parents[1] / "print-table-key-counts.py"


def create_database(path: Path, *, key_count: int, file_count: int) -> None:
    connection = duckdb.connect(str(path))
    connection.execute('CREATE TABLE key_index ("key" VARCHAR)')
    connection.execute("CREATE TABLE files (file_path VARCHAR)")
    connection.executemany("INSERT INTO key_index VALUES (?)", [(f"key-{index}",) for index in range(key_count)])
    connection.executemany("INSERT INTO files VALUES (?)", [(f"file-{index}",) for index in range(file_count)])
    connection.close()


def test_print_table_key_counts_for_all_databases(tmp_path: Path) -> None:
    create_database(tmp_path / "second.duckdb", key_count=3, file_count=1)
    create_database(tmp_path / "first.duckdb", key_count=2, file_count=4)

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "Database       Key count",
        "first.duckdb   2",
        "second.duckdb  3",
    ]
