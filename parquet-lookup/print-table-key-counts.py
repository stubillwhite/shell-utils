#!/usr/bin/env python3

from pathlib import Path

import duckdb


def key_count(database: Path) -> tuple[str, int]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        count = connection.execute("SELECT COUNT(*) FROM key_index").fetchone()[0]
        return database.name, count
    finally:
        connection.close()


def print_table(rows: list[tuple[str, int]]) -> None:
    headers = ("Database", "Key count")
    values = [headers, *[(database, str(count)) for database, count in rows]]
    widths = [max(len(row[index]) for row in values) for index in range(len(headers))]
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    for row in values[1:]:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())


def main() -> None:
    databases = sorted(Path.cwd().glob("*.duckdb"))
    rows = [key_count(database) for database in databases]
    print_table(rows)


if __name__ == "__main__":
    main()
