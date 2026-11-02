import os
import subprocess
from pathlib import Path

from tests.data_versions import copy_script
from tests.data_versions import stub
from tests.data_versions import write_data_versions

RDP_ROOT = "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs"


def run_script(
    *, tmp_path: Path, name: str, arguments: list[str] = [], env: dict[str, str] = {}
) -> subprocess.CompletedProcess[str]:
    script = copy_script(name=name, directory=tmp_path)
    write_data_versions(tmp_path)
    return subprocess.run(
        [str(script), *arguments],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", **env},
        capture_output=True,
        text=True,
    )


def test_common_keys_compares_configured_ani_indexes(tmp_path: Path) -> None:
    stub(directory=tmp_path, name="duckdb", body='printf "%s\\n" "$@"')

    result = run_script(tmp_path=tmp_path, name="common-keys")

    assert result.returncode == 0, result.stderr
    assert "attach 'mhub-ani-20990101.duckdb' as mhub;" in result.stdout
    assert "attach 'rdp-ani-20990202.duckdb' as rdp;" in result.stdout


def test_build_file_list_pairs_files_from_configured_ani_datasets(tmp_path: Path) -> None:
    data = tmp_path / "example-data"
    for dataset, names in [("mhub-ani-20990101", ["a.xml", "b.xml"]), ("rdp-ani-20990202", ["a.xml", "c.xml"])]:
        (data / dataset).mkdir(parents=True)
        for name in names:
            (data / dataset / name).touch()

    result = run_script(tmp_path=tmp_path, name="build-file-list")

    assert result.returncode == 0, result.stderr
    resolved = data.resolve()
    assert result.stdout.splitlines() == [f"{resolved}/mhub-ani-20990101/a.xml {resolved}/rdp-ani-20990202/a.xml"]


def test_check_for_new_content_lists_configured_rdp_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").touch()
    stub(directory=tmp_path, name="aws-login", body="true")
    stub(directory=tmp_path, name="aws", body="true")
    stub(directory=tmp_path, name="gsed", body='sed "$@"')
    stub(directory=tmp_path, name="highlight", body="cat")

    result = run_script(tmp_path=tmp_path, name="check-for-new-content", env={"HOME": str(home)})

    assert result.returncode == 0, result.stderr
    assert [line for line in result.stdout.splitlines() if line] == [
        f"{RDP_ROOT}/APR/output/three_column/20990202-111111/",
        f"{RDP_ROOT}/ANI/Core/output/three_column/20990202-111111/",
        f"{RDP_ROOT}/ANI/Non-scopus-publication/output/three_column/20990202-111111/",
        f"{RDP_ROOT}/ANI/Preprint/output/three_column/20990202-111111/",
    ]
