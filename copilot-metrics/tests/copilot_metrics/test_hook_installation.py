import json
import os
import subprocess
import sys

import pytest

from copilot_metrics.interruptions import hook_config


@pytest.fixture
def hook_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / ".copilot"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    return tmp_path


def install(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "copilot_metrics", "install-interruption-hooks", *map(str, arguments)],
        capture_output=True,
        text=True,
    )


def config_paths(home):
    return {
        "copilot": home / ".copilot/hooks/metrics.json",
        "claude": home / ".claude/settings.json",
        "codex": home / ".codex/hooks.json",
    }


def test_installer_creates_global_configs_and_repeated_runs_do_not_write(hook_home):
    paths = config_paths(hook_home)
    result = install()
    assert result.returncode == 0, result.stderr
    before = {}
    for provider, path in paths.items():
        assert json.loads(path.read_text()) == hook_config(
            provider=provider, output=hook_home / ".local/state/copilot-metrics" / f"{provider}.jsonl"
        )
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert os.stat(path.parent).st_mode & 0o777 == 0o700
        assert f"{provider}: installed" in result.stdout
        before[provider] = (path.read_bytes(), path.stat().st_mtime_ns)
    assert not (hook_home / ".local").exists()
    assert not (hook_home / "metrics.duckdb").exists()
    repeated = install()
    assert repeated.returncode == 0, repeated.stderr
    for provider, path in paths.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before[provider]
        assert f"{provider}: unchanged" in repeated.stdout


@pytest.mark.parametrize("provider", ["copilot", "claude", "codex"])
def test_install_merges_settings_and_hooks_and_updates_only_generated_handlers(hook_home, provider):
    path = config_paths(hook_home)[provider]
    original = hook_config(provider=provider, output=hook_home / "old.jsonl")
    event = "permissionRequest" if provider == "copilot" else "PermissionRequest"
    other = {"type": "command", "command": "echo user-hook"}
    entry = original["hooks"][event][0]
    if provider == "copilot":
        original["hooks"][event].append(other)
    else:
        entry["hooks"].append(other)
        entry["matcher"] = "Bash"
    original["keep"] = {"nested": ["setting"]}
    original["hooks"]["SessionEnd"] = [{"hooks": [{"type": "command", "command": "echo goodbye"}]}]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(original))
    os.chmod(path.parent, 0o750)
    output = hook_home / "logs with spaces"
    result = install("--provider", provider, "--log-dir", output)
    assert result.returncode == 0, result.stderr
    merged = json.loads(path.read_text())
    assert merged["keep"] == original["keep"]
    assert merged["hooks"]["SessionEnd"] == original["hooks"]["SessionEnd"]
    assert "old.jsonl" not in path.read_text()
    assert path.read_text().count("echo user-hook") == 1
    if provider != "copilot":
        preserved = next(item for item in merged["hooks"][event] if item.get("matcher") == "Bash")
        assert preserved["hooks"] == [other]
    assert os.stat(path.parent).st_mode & 0o777 == 0o750
    for name, other_path in config_paths(hook_home).items():
        assert other_path.exists() == (name == provider)
    generated = merged["hooks"][event][-1]
    command = generated["command"] if provider == "copilot" else generated["hooks"][0]["command"]
    recorded = subprocess.run(
        command, shell=True, input='{"session_id":"s"}', text=True, capture_output=True, cwd=hook_home
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads((output / f"{provider}.jsonl").read_text())["kind"] == "permission_check"
    before = path.read_bytes()
    assert install("--provider", provider, "--log-dir", output).returncode == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        "SECRET not json",
        "[]",
        '{"hooks":[]}',
        '{"hooks":{"PermissionRequest":{}}}',
        '{"hooks":{"PermissionRequest":[{"hooks":"invalid"}]}}',
        '{"hooks":{"PermissionRequest":[null]}}',
        '{"keep":1,"keep":2}',
    ],
)
def test_all_provider_preflight_rejects_bad_configs_without_writing_anything(hook_home, content):
    path = config_paths(hook_home)["claude"]
    path.parent.mkdir()
    path.write_text(content)
    result = install()
    assert result.returncode == 1
    assert "claude:" in result.stderr
    assert "SECRET" not in result.stderr
    assert path.read_text() == content
    assert not config_paths(hook_home)["copilot"].exists()
    assert not config_paths(hook_home)["codex"].exists()


def test_installer_honors_provider_home_overrides(hook_home, monkeypatch):
    monkeypatch.setenv("COPILOT_HOME", str(hook_home / "custom-copilot"))
    monkeypatch.setenv("CODEX_HOME", str(hook_home / "custom-codex"))
    assert install().returncode == 0
    assert (hook_home / "custom-copilot/hooks/metrics.json").exists()
    assert (hook_home / "custom-codex/hooks.json").exists()
    assert not (hook_home / ".copilot").exists()
    assert not (hook_home / ".codex").exists()


def test_installer_rejects_symlink_config_and_invalid_copilot_version(hook_home):
    path = config_paths(hook_home)["copilot"]
    path.parent.mkdir(parents=True)
    target = hook_home / "original.json"
    target.write_text("{}")
    path.symlink_to(target)
    assert install("--provider", "copilot").returncode == 1
    assert target.read_text() == "{}"
    path.unlink()
    path.write_text('{"version":2}')
    assert install("--provider", "copilot").returncode == 1
    assert json.loads(path.read_text()) == {"version": 2}


def test_make_target_installs_only_selected_provider(hook_home):
    result = subprocess.run(
        ["make", "-o", "deps", "install-interruption-hooks", "PROVIDER=codex"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "codex: installed" in result.stdout
    assert config_paths(hook_home)["codex"].exists()
    assert not config_paths(hook_home)["copilot"].exists()
    assert not config_paths(hook_home)["claude"].exists()


def test_atomic_replace_failure_preserves_config_and_cleans_temporary_files(hook_home, monkeypatch):
    from copilot_metrics.hook_installation import HookInstallError, install_hooks

    path = config_paths(hook_home)["claude"]
    path.parent.mkdir()
    original = b'{"keep": "original"}\n'
    path.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("SECRET OS ERROR")

    monkeypatch.setattr("copilot_metrics.hook_installation.os.replace", fail_replace)
    with pytest.raises(HookInstallError, match="claude: config write failed") as error:
        install_hooks(provider="claude")
    assert "SECRET" not in str(error.value)
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_atomic_write_refuses_to_overwrite_an_edit_since_preflight(hook_home):
    from copilot_metrics.hook_installation import write_config

    path = hook_home / "config.json"
    path.write_bytes(b'{"concurrent":"edit"}')
    with pytest.raises(ValueError, match="config_changed_during_install"):
        write_config(path, content=b"{}", previous=b'{"old":"settings"}')
    assert path.read_bytes() == b'{"concurrent":"edit"}'
    assert list(hook_home.iterdir()) == [path]
