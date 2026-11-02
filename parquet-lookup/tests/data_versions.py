from pathlib import Path

VERSIONS = {
    "MHUB_VERSION": "20990101",
    "RDP_VERSION": "20990202-111111",
    "MHUB_OPENALEX_VERSION": "20990303-222222",
    "RDP_OPENALEX_VERSION": "20990404-333333",
    "RDP_EKB_VERSION": "20990505-444444",
}


def write_data_versions(directory: Path) -> Path:
    path = directory / "data-versions"
    path.write_text("".join(f"{name}={value}\n" for name, value in VERSIONS.items()))
    return path


def copy_script(*, name: str, directory: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / name
    script = directory / name
    script.write_text(source.read_text())
    script.chmod(0o755)
    return script


def stub(*, directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
