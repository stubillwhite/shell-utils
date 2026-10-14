import os
import shlex
import subprocess
from pathlib import Path

import pytest


def run_build_index(
    *, tmp_path: Path, selection: str, existing_database: str | None = None
) -> subprocess.CompletedProcess[str]:
    source = (Path(__file__).resolve().parents[1] / "build-index").read_text()
    script = tmp_path / "build-index"
    script.write_text(source.replace('ARGS=$("${BUILD[@]}")', f'BUILD=({selection})\nARGS=$("${{BUILD[@]}}")'))
    make = tmp_path / "make"
    make.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    make.chmod(0o755)
    if existing_database is not None:
        (tmp_path / existing_database).touch()
    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path.parent,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("selection", "dataset", "key_column", "content_column", "region", "paths"),
    [
        (
            "mhub_apr 20260825",
            "mhub-apr-20260825",
            "key",
            "xml",
            "us-east-1",
            ["s3://sccontent-staging-corecomplete-xocs/sccontent-apr-parquet-prod/20260825/"],
        ),
        (
            "rdp_apr 20261005-200000",
            "rdp-apr-20261005",
            "id",
            "xml",
            "us-east-2",
            ["s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/APR/output/three_column/20261005-200000/"],
        ),
        (
            "mhub_openalex_ani 20260629 073848",
            "mhub-openalex-ani-20260629",
            "EID",
            "XML",
            "us-east-1",
            ["s3://mhub-d2m-export-parquet-prod/ANI/20260629/Ani_Eid_XML_20260629_073848.parquet"],
        ),
        (
            "rdp_openalex_ani 20261005-200000",
            "rdp-openalex-ani-20261005",
            "id",
            "xml",
            "us-east-2",
            [
                "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/openalex/ANI/Core/output/three_column/20261005-200000/"
            ],
        ),
        (
            "mhub_ani 20260825",
            "mhub-ani-20260825",
            "key",
            "xml",
            "us-east-1",
            ["s3://sccontent-staging-corecomplete-xocs/sccontent-ani-parquet-prod/20260825/"],
        ),
        (
            "rdp_ani 20260928-000001 20260827-063016 20260827-063017",
            "rdp-ani-20260928",
            "id",
            "xml",
            "us-east-2",
            [
                "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Core/output/three_column/20260928-000001/",
                "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Non-scopus-publication/output/three_column/20260827-063016/",
                "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Preprint/output/three_column/20260827-063017/",
            ],
        ),
    ],
)
def test_presets_build_the_correct_dataset(
    tmp_path: Path, selection: str, dataset: str, key_column: str, content_column: str, region: str, paths: list[str]
) -> None:
    result = run_build_index(tmp_path=tmp_path, selection=selection)

    assert result.returncode == 0, result.stderr
    target, arguments = result.stdout.splitlines()
    assert target == "run-build-index"
    assert arguments.startswith("ARGS=")
    assert shlex.split(arguments.removeprefix("ARGS=")) == [
        "--dataset",
        dataset,
        "--db",
        f"{dataset}.duckdb",
        "--key-column",
        key_column,
        "--content-column",
        content_column,
        "--region",
        region,
        *[argument for path in paths for argument in ("--path", path)],
    ]


@pytest.mark.parametrize("preset", ["mhub_apr", "rdp_apr"])
@pytest.mark.parametrize("timestamps", [[], ["20260825", "20261005-200000"]])
def test_apr_rejects_missing_or_extra_timestamps(tmp_path: Path, preset: str, timestamps: list[str]) -> None:
    result = run_build_index(tmp_path=tmp_path, selection=" ".join([preset, *timestamps]))

    assert result.returncode != 0
    assert f"build-index: {preset} expects 1 timestamp, got {len(timestamps)}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("preset", "arguments", "expected"),
    [
        ("mhub_openalex_ani", [], "2 arguments (date, time)"),
        ("mhub_openalex_ani", ["20260629"], "2 arguments (date, time)"),
        ("mhub_openalex_ani", ["20260629", "073848", "extra"], "2 arguments (date, time)"),
        ("rdp_openalex_ani", [], "1 timestamp"),
        ("rdp_openalex_ani", ["20261005-200000", "extra"], "1 timestamp"),
    ],
)
def test_openalex_rejects_missing_or_extra_arguments(
    tmp_path: Path, preset: str, arguments: list[str], expected: str
) -> None:
    result = run_build_index(tmp_path=tmp_path, selection=" ".join([preset, *arguments]))

    assert result.returncode != 0
    assert f"build-index: {preset} expects {expected}, got {len(arguments)}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("selection", "database"),
    [
        ("mhub_apr 20260825", "mhub-apr-20260825.duckdb"),
        ("rdp_apr 20261005-200000", "rdp-apr-20261005.duckdb"),
        ("mhub_openalex_ani 20260629 073848", "mhub-openalex-ani-20260629.duckdb"),
        ("rdp_openalex_ani 20261005-200000", "rdp-openalex-ani-20261005.duckdb"),
    ],
)
def test_presets_preserve_existing_database(tmp_path: Path, selection: str, database: str) -> None:
    result = run_build_index(tmp_path=tmp_path, selection=selection, existing_database=database)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert f"{database} already exists; skipping index build" in result.stderr
    assert (tmp_path / database).exists()
