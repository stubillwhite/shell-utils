import io
import logging
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import pytest
from pytest_mock import MockerFixture

from parquet_lookup import app


def write_parquet(path: Path, rows: dict[str, list[str]], row_group_size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows), path, row_group_size=row_group_size)


def indexed_rows(db_path: Path) -> list[tuple[str, str, str, int]]:
    connection = duckdb.connect(str(db_path), read_only=True)
    rows = connection.execute(
        """
        SELECT f.dataset, f.file_path, k."key", k.row_group
        FROM key_index k
        JOIN files f ON f.file_id = k.file_id
        ORDER BY f.dataset, k."key"
        """
    ).fetchall()
    connection.close()
    return rows


def test_main_dispatches_build_index(mocker: MockerFixture) -> None:
    build = mocker.patch("parquet_lookup.app.build_key_index")

    result = app.main(
        [
            "build-index",
            "--dataset",
            "archive",
            "--path",
            "s3://bucket/prefix",
            "--path",
            "./other-part",
            "--db",
            "index.duckdb",
            "--region",
            "eu-west-1",
            "--workers",
            "4",
            "--key-column",
            "id",
            "--content-column",
            "xml",
            "--max-files",
            "1",
        ]
    )

    assert result == 0
    build.assert_called_once_with(
        db_path="index.duckdb",
        dataset=app.DatasetSpec(name="archive", paths=("s3://bucket/prefix", "./other-part")),
        region="eu-west-1",
        workers=4,
        key_column="id",
        content_column="xml",
        max_files=1,
    )


def test_main_dispatches_lookup(mocker: MockerFixture) -> None:
    lookup = mocker.patch("parquet_lookup.app.lookup_key", return_value=1)

    result = app.main(
        [
            "lookup",
            "KEY123",
            "--db",
            "index.duckdb",
            "--dataset",
            "archive",
            "--out-dir",
            "results",
        ]
    )

    assert result == 1
    lookup.assert_called_once_with(
        db_path="index.duckdb",
        key="KEY123",
        datasets=["archive"],
        region=None,
        out_dir="results",
    )


def test_build_index_replaces_only_named_datasets(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    archive = tmp_path / "archive"
    current = tmp_path / "current"
    write_parquet(archive / "part.parquet", {"key": ["old"], "xml_content": ["<old/>"]})
    write_parquet(current / "part.parquet", {"key": ["current"], "xml_content": ["<current/>"]})

    for name, path in (("archive", archive), ("current", current)):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec(name, (str(path),)),
            workers=1,
            filesystem=filesystem,
        )

    write_parquet(archive / "part.parquet", {"key": ["new"], "xml_content": ["<new/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(archive),)),
        workers=1,
        filesystem=filesystem,
    )

    assert [(dataset, key) for dataset, _, key, _ in indexed_rows(database)] == [
        ("archive", "new"),
        ("current", "current"),
    ]


def test_list_parquet_files_accepts_a_direct_parquet_file(tmp_path: Path) -> None:
    parquet = tmp_path / "part.parquet"
    write_parquet(parquet, {"key": ["one"], "xml_content": ["<one/>"]})

    assert app.list_parquet_files(pafs.LocalFileSystem(), str(parquet)) == [str(parquet)]


def test_build_index_max_files_limits_the_complete_dataset(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    first, second = tmp_path / "first", tmp_path / "second"
    write_parquet(first / "a.parquet", {"key": ["first"], "xml_content": ["<first/>"]})
    write_parquet(second / "b.parquet", {"key": ["second"], "xml_content": ["<second/>"]})

    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("limited", (str(first), str(second))),
        workers=1,
        max_files=1,
    )

    assert [(name, key) for name, _, key, _ in indexed_rows(database)] == [("limited", "first")]


def test_build_index_combines_multiple_paths_under_one_dataset(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    first, second = tmp_path / "first", tmp_path / "second"
    write_parquet(first / "part.parquet", {"key": ["first"], "xml_content": ["<first/>"]})
    write_parquet(second / "part.parquet", {"key": ["second"], "xml_content": ["<second/>"]})

    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("combined", (str(first), str(second))),
        workers=1,
    )

    assert {(name, key) for name, _, key, _ in indexed_rows(database)} == {
        ("combined", "first"),
        ("combined", "second"),
    }
    for key, expected_xml in (("first", "<first/>\n"), ("second", "<second/>\n")):
        output = io.StringIO()
        assert app.lookup_key(db_path=str(database), key=key, stdout=output) == 0
        assert output.getvalue() == expected_xml


def test_discover_parquet_files_deduplicates_repeated_dataset_paths(tmp_path: Path) -> None:
    dataset_path = tmp_path / "shared"
    write_parquet(dataset_path / "part.parquet", {"key": ["one"], "xml_content": ["<one/>"]})

    dataset = app.DatasetSpec("combined", (str(dataset_path), str(dataset_path)))
    sources = app._resolve_dataset_sources(dataset=dataset, region=None, filesystem=pafs.LocalFileSystem())
    files = app._discover_parquet_files(
        dataset=dataset,
        sources=sources,
        max_files=None,
    )

    assert [(file.path, file.stored_path) for file in files] == [
        (str(dataset_path / "part.parquet"), str(dataset_path / "part.parquet"))
    ]


def test_build_index_replaces_all_previous_paths_and_rolls_back_all_paths(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    first, second, replacement, unrelated = (
        tmp_path / name for name in ("first", "second", "replacement", "unrelated")
    )
    for path, key in ((first, "old-first"), (second, "old-second"), (unrelated, "keep")):
        write_parquet(path / "part.parquet", {"key": [key], "xml_content": [f"<{key}/>"]})

    for name, path in (("combined", first), ("combined", second), ("other", unrelated)):
        app.build_key_index(db_path=str(database), dataset=app.DatasetSpec(name, (str(path),)))

    write_parquet(replacement / "part.parquet", {"key": ["new"], "xml_content": ["<new/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("combined", (str(replacement),)),
    )

    assert {(name, key) for name, _, key, _ in indexed_rows(database)} == {
        ("combined", "new"),
        ("other", "keep"),
    }

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "part.parquet").write_text("not parquet")
    with pytest.raises(Exception):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec("combined", (str(replacement), str(broken))),
        )

    assert {(name, key) for name, _, key, _ in indexed_rows(database)} == {
        ("combined", "new"),
        ("other", "keep"),
    }


def test_build_index_uses_filesystem_for_each_mixed_path(tmp_path: Path, mocker: MockerFixture) -> None:
    database = tmp_path / "index.duckdb"
    local = tmp_path / "local"
    write_parquet(local / "part.parquet", {"key": ["local"], "xml_content": ["<local/>"]})
    s3_filesystem = mocker.Mock(spec=pafs.FileSystem)
    mocker.patch("parquet_lookup.app._s3_filesystem", return_value=s3_filesystem)
    list_local_files = app.list_parquet_files
    index_local_file = app.index_one_file

    def discover(filesystem: pafs.FileSystem, path: str) -> list[str]:
        if filesystem is s3_filesystem:
            return ["bucket/part.parquet"]
        return list_local_files(filesystem, path)

    def index_file(
        filesystem: pafs.FileSystem,
        file_id: int,
        file_path: str,
        key_column: str,
        content_column: str,
    ) -> pa.Table:
        if filesystem is s3_filesystem:
            return pa.table(
                {
                    "key": ["s3"],
                    "file_id": pa.array([file_id], type=pa.int32()),
                    "row_group": pa.array([0], type=pa.int32()),
                }
            )
        return index_local_file(filesystem, file_id, file_path, key_column, content_column)

    discover_call = mocker.patch("parquet_lookup.app.list_parquet_files", side_effect=discover)
    index_call = mocker.patch("parquet_lookup.app.index_one_file", side_effect=index_file)

    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("combined", (str(local), "s3://bucket/part")),
        workers=1,
    )

    assert any(call.args[0] is s3_filesystem for call in discover_call.call_args_list)
    assert any(call.args[0] is s3_filesystem for call in index_call.call_args_list)
    assert {(name, key) for name, _, key, _ in indexed_rows(database)} == {
        ("combined", "local"),
        ("combined", "s3"),
    }


def test_build_index_logs_progress_for_each_path_and_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    database = tmp_path / "index.duckdb"
    first, second = tmp_path / "first", tmp_path / "second"
    write_parquet(first / "a.parquet", {"key": ["a1", "a2"], "xml_content": ["<a1/>", "<a2/>"]})
    write_parquet(second / "b.parquet", {"key": ["b1"], "xml_content": ["<b1/>"]})

    with caplog.at_level(logging.INFO, logger="parquet_lookup.app"):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec("combined", (str(first), str(second))),
            workers=1,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(str(first) in message and "Scanning" in message for message in messages)
    assert any(str(second) in message and "Scanning" in message for message in messages)
    assert any("a.parquet" in message and "Processing" in message for message in messages)
    assert any("a.parquet" in message and "2 keys" in message and "1/2" in message for message in messages)
    assert any("b.parquet" in message and "1 keys" in message for message in messages)
    assert any("3 keys" in message and "2 files" in message for message in messages)


def test_lookup_success_writes_no_log_output(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    database = tmp_path / "index.duckdb"
    directory = tmp_path / "sample"
    write_parquet(directory / "part.parquet", {"key": ["one"], "xml_content": ["<one/>"]})
    app.build_key_index(db_path=str(database), dataset=app.DatasetSpec("sample", (str(directory),)))
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="parquet_lookup.app"):
        assert app.lookup_key(db_path=str(database), key="one", stdout=io.StringIO()) == 0

    assert caplog.records == []


def test_main_sends_build_progress_to_stderr_not_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    directory = tmp_path / "sample"
    write_parquet(directory / "part.parquet", {"key": ["one"], "xml_content": ["<one/>"]})

    assert (
        app.main(
            ["build-index", "--dataset", "sample", "--path", str(directory), "--db", str(tmp_path / "index.duckdb")]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "part.parquet" in captured.err


def test_build_index_removes_old_rows_when_named_dataset_is_empty(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "dataset"
    write_parquet(dataset / "part.parquet", {"key": ["old"], "xml_content": ["<old/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(dataset),)),
        workers=1,
        filesystem=filesystem,
    )
    (dataset / "part.parquet").unlink()

    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(dataset),)),
        workers=1,
        filesystem=filesystem,
    )

    assert indexed_rows(database) == []


def test_build_index_rolls_back_replacement_on_failure(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "dataset"
    write_parquet(dataset / "part.parquet", {"key": ["old"], "xml_content": ["<old/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(dataset),)),
        workers=1,
        filesystem=filesystem,
    )
    (dataset / "part.parquet").write_text("not parquet")

    with pytest.raises(Exception):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec("archive", (str(dataset),)),
            workers=1,
            filesystem=filesystem,
        )

    assert [(dataset_name, key) for dataset_name, _, key, _ in indexed_rows(database)] == [("archive", "old")]


def test_lookup_reads_indexed_row_group_and_prints_xml(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "dataset"
    write_parquet(
        dataset / "part.parquet",
        {"key": ["first", "wanted"], "xml_content": ["<first/>", "<wanted/>"]},
        row_group_size=1,
    )
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(dataset),)),
        workers=1,
        filesystem=filesystem,
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    result = app.lookup_key(
        db_path=str(database),
        key="wanted",
        filesystem=filesystem,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert stdout.getvalue() == "<wanted/>\n"
    assert stderr.getvalue() == ""


def test_lookup_filters_datasets_and_writes_output(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_parquet(first / "part.parquet", {"key": ["shared"], "xml_content": ["<first/>"]})
    write_parquet(second / "part.parquet", {"key": ["shared"], "xml_content": ["<second/>"]})
    for name, path in (("first", first), ("second", second)):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec(name, (str(path),)),
            workers=1,
            filesystem=filesystem,
        )
    output = tmp_path / "output"

    result = app.lookup_key(
        db_path=str(database),
        key="shared",
        datasets=["second"],
        out_dir=str(output),
        filesystem=filesystem,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert result == 0
    assert (output / "second.xml").read_text() == "<second/>"
    assert not (output / "first.xml").exists()


def test_lookup_multiple_datasets_fails_without_printing_xml(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    for name in ("first", "second"):
        directory = tmp_path / name
        write_parquet(directory / "part.parquet", {"key": ["shared"], "xml_content": [f"<{name}/>"]})
        app.build_key_index(db_path=str(database), dataset=app.DatasetSpec(name, (str(directory),)))
    stdout, stderr = io.StringIO(), io.StringIO()

    assert app.lookup_key(db_path=str(database), key="shared", stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == ""
    assert "multiple matches" in stderr.getvalue()
    assert "--dataset" in stderr.getvalue()


def test_lookup_repeated_key_in_one_row_group_fails_without_printing_xml(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    directory = tmp_path / "sample"
    write_parquet(
        directory / "part.parquet",
        {"key": ["shared", "shared"], "xml_content": ["<first/>", "<second/>"]},
        row_group_size=2,
    )
    app.build_key_index(db_path=str(database), dataset=app.DatasetSpec("sample", (str(directory),)))
    stdout, stderr = io.StringIO(), io.StringIO()

    assert app.lookup_key(db_path=str(database), key="shared", stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == ""
    assert "multiple matches" in stderr.getvalue()


def test_lookup_file_output_leaves_stdout_empty(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    directory = tmp_path / "sample"
    write_parquet(directory / "part.parquet", {"key": ["one"], "xml_content": ["<one/>"]})
    app.build_key_index(db_path=str(database), dataset=app.DatasetSpec("sample", (str(directory),)))
    stdout, stderr = io.StringIO(), io.StringIO()

    assert (
        app.lookup_key(db_path=str(database), key="one", out_dir=str(tmp_path / "out"), stdout=stdout, stderr=stderr)
        == 0
    )
    assert stdout.getvalue() == ""
    assert (tmp_path / "out" / "sample.xml").read_text() == "<one/>"


def test_lookup_reports_missing_and_stale_keys(tmp_path: Path) -> None:
    filesystem = pafs.LocalFileSystem()
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "dataset"
    write_parquet(dataset / "part.parquet", {"key": ["old"], "xml_content": ["<old/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("archive", (str(dataset),)),
        workers=1,
        filesystem=filesystem,
    )

    missing_error, missing_output = io.StringIO(), io.StringIO()
    assert (
        app.lookup_key(
            db_path=str(database),
            key="missing",
            filesystem=filesystem,
            stdout=missing_output,
            stderr=missing_error,
        )
        == 1
    )
    assert "not found in index" in missing_error.getvalue()
    assert missing_output.getvalue() == ""

    write_parquet(dataset / "part.parquet", {"key": ["new"], "xml_content": ["<new/>"]})
    stale_error, stale_output = io.StringIO(), io.StringIO()
    assert (
        app.lookup_key(
            db_path=str(database),
            key="old",
            filesystem=filesystem,
            stdout=stale_output,
            stderr=stale_error,
        )
        == 1
    )
    assert "index may be stale" in stale_error.getvalue()
    assert stale_output.getvalue() == ""


def test_local_dataset_with_custom_columns_builds_and_looks_up_without_injected_filesystem(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "sample"
    write_parquet(
        dataset / "part.parquet",
        {"id": ["one", "two"], "xml": ["<one/>", "<two/>"]},
        row_group_size=1,
    )

    assert (
        app.main(
            [
                "build-index",
                "--dataset",
                "sample",
                "--path",
                str(dataset),
                "--db",
                str(database),
                "--key-column",
                "id",
                "--content-column",
                "xml",
            ]
        )
        == 0
    )
    assert [(name, key, group) for name, _, key, group in indexed_rows(database)] == [
        ("sample", "one", 0),
        ("sample", "two", 1),
    ]

    output = io.StringIO()
    assert app.lookup_key(db_path=str(database), key="two", stdout=output) == 0
    assert output.getvalue() == "<two/>\n"


def test_builder_rejects_multiple_datasets_on_command_line() -> None:
    with pytest.raises(SystemExit) as error:
        app.main(
            [
                "build-index",
                "--dataset",
                "a",
                "--path",
                "./a",
                "--dataset",
                "b",
                "--path",
                "./b",
            ]
        )
    assert error.value.code == 2


def test_builder_rejects_missing_content_column_without_replacing_old_index(tmp_path: Path) -> None:
    database = tmp_path / "index.duckdb"
    dataset = tmp_path / "sample"
    write_parquet(dataset / "part.parquet", {"id": ["one"], "xml": ["<one/>"]})
    app.build_key_index(
        db_path=str(database),
        dataset=app.DatasetSpec("sample", (str(dataset),)),
        key_column="id",
        content_column="xml",
    )

    with pytest.raises(ValueError, match="missing columns: not_xml"):
        app.build_key_index(
            db_path=str(database),
            dataset=app.DatasetSpec("sample", (str(dataset),)),
            key_column="id",
            content_column="not_xml",
        )

    assert [(name, key) for name, _, key, _ in indexed_rows(database)] == [("sample", "one")]
