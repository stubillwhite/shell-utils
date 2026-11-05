import os
import subprocess
from pathlib import Path

import pytest

from tests.data_versions import copy_script, stub, write_data_versions


@pytest.fixture(autouse=True)
def download_tools(tmp_path: Path) -> None:
    tools = tmp_path / "tools with spaces"
    tools.mkdir()
    copy_script(name="get-samples-to-upload", directory=tools)
    write_data_versions(tools)
    stub(
        directory=tools,
        name="query-dataset",
        body='printf "%s %s\\n" "$1" "$2" >> query.log\nprintf "<record/>\\n"',
    )
    stub(
        directory=tmp_path,
        name="aws",
        body='printf "%s\\n" "$*" >> credentials.log\n'
        "if [[ -f fail-credentials ]]; then\n"
        '  echo "Credentials missing or expired" >&2\n  exit 1\nfi',
    )
    stub(
        directory=tmp_path,
        name="xmllint",
        body='[[ "$1" == --format ]] || exit 1\n' 'grep -qx "<record/>" "$2" || exit 1\nprintf "<formatted/>\\n"',
    )


def run_get_samples(tmp_path: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "tools with spaces" / "get-samples-to-upload"
    opener = tmp_path / "open"
    opener.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    opener.chmod(0o755)
    return subprocess.run(
        ["zsh", str(script), *arguments],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


def cache_record(tmp_path: Path, source: str, key: str, content: str) -> Path:
    path = tmp_path / "example-data" / source / f"{key}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.mark.parametrize("dataset", ["ani", "apr"])
def test_collects_only_selected_dataset_with_full_source_prefix(tmp_path: Path, dataset: str) -> None:
    for prefix in ["mhub", "rdp", "rdp-openalex", "rdp-ekb"]:
        for kind in ["ani", "apr"]:
            version = {"mhub": "20990101", "rdp": "20990202"}.get(prefix, "20261016")
            directory = tmp_path / "example-data" / f"{prefix}-{kind}-{version}"
            directory.mkdir(parents=True)
            (directory / "1234.xml").write_text(f"{prefix}-{kind}")
            (directory / "12345.xml").write_text("prefix match")
            (directory / "9999.xml").write_text("unrelated")
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "stale.xml").write_text("stale")

    result = run_get_samples(tmp_path, [dataset, "1234"])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "./upload"
    assert not (tmp_path / "credentials.log").exists()
    assert {file.name: file.read_text() for file in upload.iterdir()} == {
        f"{prefix}-{dataset}-{key}.xml": content
        for prefix in ["mhub", "rdp", "rdp-openalex", "rdp-ekb"]
        for key, content in [("1234", f"{prefix}-{dataset}"), ("12345", "prefix match")]
    }


@pytest.mark.parametrize("arguments", [[], ["ani"], ["ani", "1234", "extra"], ["unknown", "1234"]])
def test_requires_dataset_and_id_before_clearing_upload(tmp_path: Path, arguments: list[str]) -> None:
    upload = tmp_path / "upload"
    upload.mkdir()
    sample = upload / "existing.xml"
    sample.write_text("keep")

    result = run_get_samples(tmp_path, arguments)

    assert result.returncode != 0
    assert "Usage:" in result.stderr or "Unknown dataset" in result.stderr
    assert sample.read_text() == "keep"


@pytest.mark.parametrize("contents", ["1234\n\n5678\n", "1234\r\n\r\n5678"])
def test_collects_all_ids_from_file_without_clearing_between_ids(tmp_path: Path, contents: str) -> None:
    sources = ["mhub-ani-20990101", "rdp-ani-20990202", "rdp-openalex-ani-20261016"]
    for source in sources:
        for key in ["1234", "5678", "9999"]:
            cache_record(tmp_path, source, key, key)
    ids_file = tmp_path / "sample ids.txt"
    ids_file.write_bytes(contents.encode())
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "stale.xml").write_text("stale")

    result = run_get_samples(tmp_path, ["ani", str(ids_file)])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["./upload"]
    assert not (tmp_path / "credentials.log").exists()
    assert {file.name: file.read_text() for file in upload.iterdir()} == {
        f"{source.rsplit('-', 1)[0]}-{key}.xml": key for source in sources for key in ["1234", "5678"]
    }


@pytest.mark.parametrize("contents", ["", "\n\r\n"])
def test_empty_id_list_does_not_collect_every_sample(tmp_path: Path, contents: str) -> None:
    directory = tmp_path / "rdp-ani-20261016"
    directory.mkdir()
    (directory / "1234.xml").write_text("unrequested")
    ids_file = tmp_path / "ids.txt"
    ids_file.write_bytes(contents.encode())

    result = run_get_samples(tmp_path, ["ani", str(ids_file)])

    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "upload").iterdir()) == []
    assert not (tmp_path / "credentials.log").exists()


@pytest.mark.parametrize(
    ("dataset", "sources"),
    [
        ("ani", ["mhub-ani-20990101", "rdp-ani-20990202"]),
        ("apr", ["mhub-apr-20990101", "rdp-apr-20990202"]),
        ("openalex-ani", ["mhub-openalex-ani-20990303", "rdp-openalex-ani-20990404"]),
    ],
)
def test_downloads_current_source_pair_and_publishes_formatted_xml(
    tmp_path: Path, dataset: str, sources: list[str]
) -> None:
    result = run_get_samples(tmp_path, [dataset, "001234"])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["./upload"]
    assert (tmp_path / "credentials.log").read_text().splitlines() == ["sts get-caller-identity"]
    assert (tmp_path / "query.log").read_text().splitlines() == [f"{source} 001234" for source in sources]
    for source in sources:
        assert (tmp_path / "example-data" / source / "001234.xml").read_text() == "<formatted/>\n"
        assert (tmp_path / "upload" / f"{source.rsplit('-', 1)[0]}-001234.xml").read_text() == "<formatted/>\n"
    assert not list((tmp_path / "example-data").glob(".upload.*"))


def test_batch_downloads_only_missing_exact_current_records_with_one_credentials_check(tmp_path: Path) -> None:
    cache_record(tmp_path, "mhub-ani-20990101", "001234", "cached")
    cache_record(tmp_path, "rdp-ani-20990202", "001234", "")
    cache_record(tmp_path, "mhub-ani-20990101", "0056789", "prefix")
    cache_record(tmp_path, "rdp-ani-20000101", "005678", "old")
    ids = tmp_path / "ids.txt"
    ids.write_text("001234\n005678\n")
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "stale.xml").write_text("stale")

    result = run_get_samples(tmp_path, ["ani", str(ids)])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["./upload"]
    assert (tmp_path / "credentials.log").read_text().splitlines() == ["sts get-caller-identity"]
    assert (tmp_path / "query.log").read_text().splitlines() == [
        "rdp-ani-20990202 001234",
        "mhub-ani-20990101 005678",
        "rdp-ani-20990202 005678",
    ]
    assert (tmp_path / "example-data/mhub-ani-20990101/001234.xml").read_text() == "cached"
    assert {path.name for path in upload.iterdir()} == {
        "mhub-ani-001234.xml",
        "rdp-ani-001234.xml",
        "mhub-ani-005678.xml",
        "rdp-ani-005678.xml",
        "mhub-ani-0056789.xml",
    }


@pytest.mark.parametrize("failure", ['printf "<partial/>"; exit 1', "exit 0", 'printf "invalid XML"'])
def test_failed_records_leave_no_cache_and_do_not_block_remaining_downloads(tmp_path: Path, failure: str) -> None:
    stub(
        directory=tmp_path / "tools with spaces",
        name="query-dataset",
        body='printf "%s %s\\n" "$1" "$2" >> query.log\n'
        f'if [[ "$1" == mhub-* && "$2" == 1234 ]]; then\n{failure}\nelse\nprintf "<record/>\\n"\nfi',
    )
    failed = cache_record(tmp_path, "mhub-ani-20990101", "1234", "")
    ids = tmp_path / "ids.txt"
    ids.write_text("1234\n5678")

    result = run_get_samples(tmp_path, ["ani", str(ids)])

    assert result.returncode != 0
    assert "1234" in result.stderr and "mhub-ani-20990101" in result.stderr
    assert result.stdout.splitlines() == ["./upload"]
    assert len((tmp_path / "query.log").read_text().splitlines()) == 4
    assert not failed.exists()
    assert not (tmp_path / "upload/mhub-ani-1234.xml").exists()
    assert (tmp_path / "upload/rdp-ani-1234.xml").read_text() == "<formatted/>\n"
    assert (tmp_path / "upload/mhub-ani-5678.xml").read_text() == "<formatted/>\n"
    assert (tmp_path / "upload/rdp-ani-5678.xml").read_text() == "<formatted/>\n"
    assert not list((tmp_path / "example-data").glob(".upload.*"))


def test_invalid_credentials_exit_before_querying_or_changing_upload(tmp_path: Path) -> None:
    cache_record(tmp_path, "mhub-ani-20990101", "1234", "cached")
    (tmp_path / "fail-credentials").touch()
    upload = tmp_path / "upload"
    upload.mkdir()
    existing = upload / "existing.xml"
    existing.write_text("keep")

    result = run_get_samples(tmp_path, ["ani", "1234"])

    assert result.returncode != 0
    assert "AWS credentials" in result.stderr
    assert "Credentials missing or expired" in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "query.log").exists()
    assert existing.read_text() == "keep"
    assert not list((tmp_path / "example-data").glob(".upload.*"))


def test_rejects_ids_that_would_write_outside_the_source_cache(tmp_path: Path) -> None:
    ids = tmp_path / "ids.txt"
    ids.write_text("1234\n../../outside\n")
    upload = tmp_path / "upload"
    upload.mkdir()
    existing = upload / "existing.xml"
    existing.write_text("keep")

    result = run_get_samples(tmp_path, ["ani", str(ids)])

    assert result.returncode != 0
    assert "Invalid sample ID" in result.stderr
    assert existing.read_text() == "keep"
    assert not (tmp_path / "credentials.log").exists()
