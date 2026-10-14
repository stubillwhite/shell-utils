import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("existing_data", [False, True])
def test_download_creates_directories_for_ani_apr_and_openalex(tmp_path: Path, existing_data: bool) -> None:
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
        ("mhub-ani-20260825", "0002660952", 50),
        ("rdp-ani-20261005", "0002660952", 50),
        ("mhub-apr-20260825", "10038760900", 25),
        ("rdp-apr-20260827", "10038760900", 25),
        ("rdp-apr-20261005", "10038760900", 25),
        ("mhub-openalex-ani-20260629", "0018283312", 25),
        ("rdp-openalex-ani-20261005", "0018283312", 25),
    ]:
        directory = tmp_path / "example-data" / dataset
        assert (directory / f"{key}.xml").read_text() == "<record/>\n"
        assert len(list(directory.glob("*.xml"))) == count
    assert not list((tmp_path / "example-data").glob(".download.*"))


@pytest.mark.parametrize(
    ("dataset", "region"),
    [
        ("mhub-openalex-ani-20260629", "us-east-1"),
        ("rdp-openalex-ani-20261005", "us-east-2"),
    ],
)
def test_query_dataset_uses_openalex_region(tmp_path: Path, dataset: str, region: str) -> None:
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
