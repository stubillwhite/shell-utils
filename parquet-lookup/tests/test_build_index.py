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
    "mhub_ani_non_core": (
        "mhub-ani-non-core-20990606",
        "key",
        "xml",
        "us-east-1",
        ["s3://sccontent-staging-corecomplete-xocs/sccontent-anidummy-parquet-prod/20990606/"],
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
    "rdp_ani_non_core": (
        "rdp-ani-non-core-20990707",
        "id",
        "xml",
        "us-east-2",
        [f"{RDP_ROOT}/xocs/ANI/Dummy/output/three_column/20990707-555555/"],
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


def test_no_arguments_includes_mhub_ani_non_core(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=[])

    assert result.returncode == 0, result.stderr
    assert expected_arguments("mhub_ani_non_core") in built_arguments(result.stdout)


def test_no_arguments_includes_rdp_ani_non_core(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=[])

    assert result.returncode == 0, result.stderr
    assert expected_arguments("rdp_ani_non_core") in built_arguments(result.stdout)


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


def test_list_paths_matches_all_build_inputs_without_building(tmp_path: Path) -> None:
    build_directory = tmp_path / "build"
    list_directory = tmp_path / "list"
    build_directory.mkdir()
    list_directory.mkdir()

    builds = run_build_index(tmp_path=build_directory, presets=[])
    result = run_build_index(tmp_path=list_directory, presets=["--list-paths"])

    assert builds.returncode == 0, builds.stderr
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert [line.partition(": ")[0] for line in lines] == [
        "MHub ANI",
        "MHub ANI NON CORE",
        "MHub APR",
        "MHub IHR",
        "MHub IPR",
        "RDP ANI",
        "RDP ANI",
        "RDP ANI",
        "RDP ANI NON CORE",
        "RDP APR",
        "RDP IHR",
        "MHub OpenAlex ANI",
        "RDP OpenAlex ANI",
    ]
    assert [line.partition(": ")[2] for line in lines] == [
        arguments[index + 1]
        for arguments in built_arguments(builds.stdout)
        for index, argument in enumerate(arguments)
        if argument == "--path"
    ]
    assert not list(list_directory.glob("*.duckdb"))


def test_list_paths_reports_mhub_ani_non_core_source(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["--list-paths", "mhub_ani_non_core"])

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "MHub ANI NON CORE: s3://sccontent-staging-corecomplete-xocs/sccontent-anidummy-parquet-prod/20990606/"
    ]


def test_list_paths_reports_rdp_ani_non_core_source(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["--list-paths", "rdp_ani_non_core"])

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "RDP ANI NON CORE: s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Dummy/output/three_column/20990707-555555/"
    ]


def test_list_paths_uses_selected_versions_even_when_database_exists(tmp_path: Path) -> None:
    presets = ["rdp_ani", "mhub_openalex_ani"]

    result = run_build_index(
        tmp_path=tmp_path,
        presets=["--list-paths", *presets],
        existing_databases=[f"{EXPECTED[preset][0]}.duckdb" for preset in presets],
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        f"{label}: {path}"
        for preset, label in zip(presets, ["RDP ANI", "MHub OpenAlex ANI"])
        for path in EXPECTED[preset][4]
    ]


def test_list_paths_rejects_unknown_preset_before_printing_any_paths(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["--list-paths", "mhub_ani", "bogus"])

    assert result.returncode != 0
    assert "build-index: unknown preset bogus" in result.stderr
    assert result.stdout == ""


def test_list_paths_requires_data_versions(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["--list-paths"], with_versions=False)

    assert result.returncode != 0
    assert result.stdout == ""


def test_missing_data_versions_fails(tmp_path: Path) -> None:
    result = run_build_index(tmp_path=tmp_path, presets=["mhub_ani"], with_versions=False)

    assert result.returncode != 0
    assert result.stdout == ""
