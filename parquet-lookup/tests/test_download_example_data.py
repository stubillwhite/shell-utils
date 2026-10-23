import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("existing_data", [False, True])
def test_download_saves_active_datasets_and_creates_inactive_directories(tmp_path: Path, existing_data: bool) -> None:
    source = Path(__file__).resolve().parents[1] / "download-example-data"
    script = tmp_path / source.name
    script.write_text(source.read_text())
    if existing_data:
        (tmp_path / "example-data").mkdir()
    query = tmp_path / "query-dataset"
    query.write_text('#!/usr/bin/env bash\nprintf "<record/>\\n"\n')
    query.chmod(0o755)
    formatter = tmp_path / "xmllint"
    formatter.write_text('#!/usr/bin/env bash\ncat "$2"\n')
    formatter.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    for dataset, key, count in [
        ("mhub-ani-20260825", "0013885228", 50),
        ("rdp-ani-20261016", "0013885228", 50),
        ("rdp-ekb-ani-20261016", "105035626582", 50),
    ]:
        directory = tmp_path / "example-data" / dataset
        assert (directory / f"{key}.xml").read_text() == "<record/>\n"
        assert len(list(directory.glob("*.xml"))) == count
    for dataset in [
        "mhub-apr-20260825",
        "rdp-apr-20260827",
        "rdp-apr-20261005",
        "mhub-openalex-ani-20260629",
        "rdp-openalex-ani-20261005",
    ]:
        assert (tmp_path / "example-data" / dataset).is_dir()
    assert not list((tmp_path / "example-data").glob(".download.*"))


def test_download_reports_failure_after_attempting_remaining_records(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "download-example-data"
    script = tmp_path / source.name
    script.write_text(source.read_text())
    query = tmp_path / "query-dataset"
    query.write_text("#!/usr/bin/env bash\nexit 1\n")
    query.chmod(0o755)

    result = subprocess.run(["bash", str(script)], cwd=tmp_path, capture_output=True, text=True)

    assert result.returncode == 1
    assert "Failed to download record 0013885228 from mhub-ani-20260825" in result.stderr
    assert "Failed to download record 85155640574 from rdp-ekb-ani-20261016" in result.stderr
    assert not list((tmp_path / "example-data").glob(".download.*"))


@pytest.mark.parametrize(
    ("dataset", "region"),
    [
        ("mhub-openalex-ani-20260629", "us-east-1"),
        ("rdp-openalex-ani-20261005", "us-east-2"),
        ("rdp-ekb-ani", "us-east-2"),
        ("rdp-ekb-ani-20261016", "us-east-2"),
    ],
)
def test_query_dataset_uses_dataset_region(tmp_path: Path, dataset: str, region: str) -> None:
    source = Path(__file__).resolve().parents[1] / "query-dataset"
    script = tmp_path / source.name
    script.write_text(source.read_text())
    make = tmp_path / "make"
    make.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    make.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), dataset, "0018283312"],
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "run-lookup",
        f"ARGS=--db {dataset}.duckdb --dataset {dataset} --region {region} 0018283312",
    ]
