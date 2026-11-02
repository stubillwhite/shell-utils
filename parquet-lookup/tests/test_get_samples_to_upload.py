import os
import subprocess
from pathlib import Path

import pytest


def run_get_samples(tmp_path: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / "get-samples-to-upload"
    opener = tmp_path / "open"
    opener.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    opener.chmod(0o755)
    return subprocess.run(
        ["bash", str(script), *arguments],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("dataset", ["ani", "apr"])
def test_collects_only_selected_dataset_with_full_source_prefix(tmp_path: Path, dataset: str) -> None:
    for prefix in ["mhub", "rdp", "rdp-openalex", "rdp-ekb"]:
        for kind in ["ani", "apr"]:
            directory = tmp_path / "example data" / f"{prefix}-{kind}-20261016"
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
    assert {file.name: file.read_text() for file in upload.iterdir()} == {
        f"{prefix}-{dataset}-{key}.xml": content
        for prefix in ["mhub", "rdp", "rdp-openalex", "rdp-ekb"]
        for key, content in [("1234", f"{prefix}-{dataset}"), ("12345", "prefix match")]
    }


@pytest.mark.parametrize("arguments", [[], ["ani"], ["ani", "1234", "extra"]])
def test_requires_dataset_and_id_before_clearing_upload(tmp_path: Path, arguments: list[str]) -> None:
    upload = tmp_path / "upload"
    upload.mkdir()
    sample = upload / "existing.xml"
    sample.write_text("keep")

    result = run_get_samples(tmp_path, arguments)

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert sample.read_text() == "keep"
