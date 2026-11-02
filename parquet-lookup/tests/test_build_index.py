import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.data_versions import copy_script
from tests.data_versions import stub
from tests.data_versions import write_data_versions

RDP_ROOT = "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod"

EXPECTED = {
    "mhub_ani": (
        "mhub-ani-20990101",
        "key",
        "xml",
        "us-east-1",
        ["s3://sccontent-staging-corecomplete-xocs/sccontent-ani-parquet-prod/20990101/"],
    ),
    "rdp_ani": (
        "rdp-ani-20990202",
        "id",
        "xml",
        "us-east-2",
        [
            f"{RDP_ROOT}/xocs/ANI/Core/output/three_column/20990202-111111/",
            f"{RDP_ROOT}/xocs/ANI/Non-scopus-publication/output/three_column/20990202-111111/",
            f"{RDP_ROOT}/xocs/ANI/Preprint/output/three_column/20990202-111111/",
        ],
    ),
    "mhub_apr": (
        "mhub-apr-20990101",
        "key",
        "xml",
        "us-east-1",
        ["s3://sccontent-staging-corecomplete-xocs/sccontent-apr-parquet-prod/20990101/"],
    ),
    "rdp_apr": (
        "rdp-apr-20990202",
        "id",
        "xml",
        "us-east-2",
        [f"{RDP_ROOT}/xocs/APR/output/three_column/20990202-111111/"],
    ),
    "mhub_openalex_ani": (
        "mhub-openalex-ani-20990303",
        "EID",
        "XML",
        "us-east-1",
        ["s3://mhub-d2m-export-parquet-prod/ANI/20990303/Ani_Eid_XML_20990303_222222.parquet"],
    ),
    "rdp_openalex_ani": (
        "rdp-openalex-ani-20990404",
        "id",
        "xml",
        "us-east-2",
        [f"{RDP_ROOT}/openalex/ANI/Core/output/three_column/20990404-333333/"],
    ),
    "rdp_ekb_ani": (
        "rdp-ekb-ani-20990505",
        "id",
        "xml",
        "us-east-2",
        [f"{RDP_ROOT}/ekb/ANI/Core/output/three_column/20990505-444444/"],
    ),
}


def run_build_index(
    *, tmp_path: Path, presets: list[str], existing_databases: list[str] = [], with_versions: bool = True
) -> subprocess.CompletedProcess[str]:
    script = copy_script(name="build-index", directory=tmp_path)
    if with_versions:
        write_data_versions(tmp_path)
    stub(directory=tmp_path, name="make", body='printf "%s\\n" "$@"')
    for database in existing_databases:
        (tmp_path / database).touch()
    return subprocess.run(
        ["bash", str(script), *presets],
        cwd=tmp_path.parent,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


def expected_arguments(preset: str) -> list[str]:
    dataset, key_column, content_column, region, paths = EXPECTED[preset]
    return [
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


def built_arguments(stdout: str) -> list[list[str]]:
    lines = stdout.splitlines()
    assert lines[::2] == ["run-build-index"] * (len(lines) // 2)
    return [shlex.split(line.removeprefix("ARGS=")) for line in lines[1::2]]


@pytest.mark.parametrize("preset", list(EXPECTED))
def test_preset_builds_dataset_from_configured_versions(tmp_path: Path, preset: str) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=[preset])

    assert result.returncode == 0, result.stderr
    assert built_arguments(result.stdout) == [expected_arguments(preset)]


def test_multiple_presets_build_in_requested_order(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["rdp_apr", "mhub_ani"])

    assert result.returncode == 0, result.stderr
    assert built_arguments(result.stdout) == [expected_arguments("rdp_apr"), expected_arguments("mhub_ani")]


def test_no_arguments_builds_every_missing_dataset(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=[], existing_databases=["rdp-apr-20990202.duckdb"])

    assert result.returncode == 0, result.stderr
    assert built_arguments(result.stdout) == [expected_arguments(preset) for preset in EXPECTED if preset != "rdp_apr"]
    assert "rdp-apr-20990202.duckdb already exists; skipping index build" in result.stderr


@pytest.mark.parametrize("preset", list(EXPECTED))
def test_existing_database_is_preserved(tmp_path: Path, preset: str) -> None:
    database = f"{EXPECTED[preset][0]}.duckdb"

    result = run_build_index(tmp_path=tmp_path, presets=[preset], existing_databases=[database])

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert f"{database} already exists; skipping index build" in result.stderr


def test_unknown_preset_fails_before_building_anything(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["mhub_ani", "bogus"])

    assert result.returncode != 0
    assert "build-index: unknown preset bogus" in result.stderr
    assert result.stdout == ""


def test_missing_data_versions_fails(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["mhub_ani"], with_versions=False)

    assert result.returncode != 0
    assert result.stdout == ""
