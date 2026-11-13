import os
import shutil
from pathlib import Path
import subprocess

import pytest

from tools.kh3 import local_environment as settings


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "renamed checkout"
    root.mkdir()
    monkeypatch.setattr(settings, "CHECKOUT_ROOT", root)
    for name in ("AP_ENV_FILE", "REPOSITORY_ROOT", "KH3_BRIDGE_DIR", "KH3_RUNTIME_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return root


def test_process_environment_wins_without_reading_file(checkout, monkeypatch):
    monkeypatch.setenv("AP_ENV_FILE", str(checkout / "missing.env"))
    monkeypatch.setenv("KH3_BRIDGE_DIR", "chosen bridge")
    assert settings.local_setting("KH3_BRIDGE_DIR") == "chosen bridge"


def test_dotenv_preserves_literal_windows_paths_and_ignores_unrelated_settings(checkout):
    (checkout / ".env").write_text(
        'UNRELATED="unfinished\n'
        'export KH3_BRIDGE_DIR="C:\\test folder\\bridge\\seed_player" # comment\n'
        "KH3_RUNTIME_ROOT='literal $value `text`'\n",
        encoding="utf-8",
    )
    assert settings.local_setting("KH3_BRIDGE_DIR") == r"C:\test folder\bridge\seed_player"
    assert settings.local_setting("KH3_RUNTIME_ROOT") == "literal $value `text`"


def test_relative_config_file_and_paths_are_independent_of_working_directory(checkout, tmp_path, monkeypatch):
    (checkout / "developer.env").write_text("REPOSITORY_ROOT=external source # comment\n", encoding="utf-8")
    monkeypatch.setenv("AP_ENV_FILE", "developer.env")
    monkeypatch.chdir(tmp_path)
    assert settings.local_path("REPOSITORY_ROOT", checkout) == checkout / "external source"


def test_dotenv_empty_values_use_defaults(checkout, monkeypatch):
    (checkout / ".env").write_text("KH3_RUNTIME_ROOT= # comment\n", encoding="utf-8")
    assert settings.local_path("KH3_RUNTIME_ROOT", checkout) == checkout
    monkeypatch.setenv("KH3_RUNTIME_ROOT", "")
    assert settings.local_setting("KH3_RUNTIME_ROOT", "default") == "default"


def test_explicit_missing_config_file_is_an_error(checkout, monkeypatch):
    monkeypatch.setenv("AP_ENV_FILE", "missing.env")
    with pytest.raises(FileNotFoundError):
        settings.local_setting("KH3_BRIDGE_DIR")


def test_missing_default_file_uses_fallback(checkout):
    assert settings.local_path("REPOSITORY_ROOT", checkout) == checkout
    assert settings.local_setting("KH3_BRIDGE_DIR") is None


def test_incomplete_requested_value_is_rejected(checkout):
    (checkout / ".env").write_text('KH3_BRIDGE_DIR="unfinished\n', encoding="utf-8")
    with pytest.raises(ValueError, match="KH3_BRIDGE_DIR"):
        settings.local_setting("KH3_BRIDGE_DIR")


@pytest.mark.parametrize("process_value,explicit,expected", [
    (None, "", "from file"),
    ("from environment", "", "from environment"),
    ("from environment", "from argument", "from argument"),
    ("", "", "fallback"),
])
def test_powershell_paths_use_literal_settings_and_checkout_root(
        tmp_path, process_value, explicit, expected):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is required for configuration tests")
    repository = Path(__file__).resolve().parents[3]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text('KH3_DIR="from file"\n', encoding="utf-8")
    script = tmp_path / "resolve.ps1"
    script.write_text(
        "param($Helper, $SettingsRoot, $Explicit)\n"
        "$ErrorActionPreference = 'Stop'\n"
        ". $Helper\n"
        "Resolve-Kh3LocalPath -Name KH3_DIR -Value $Explicit "
        "-Default fallback -SettingsRoot $SettingsRoot\n", encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("AP_ENV_FILE", None)
    environment.pop("KH3_DIR", None)
    if process_value is not None:
        environment["KH3_DIR"] = process_value
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-Helper", str(repository / "scripts/kh3-local-environment.ps1"),
        "-SettingsRoot", str(checkout), "-Explicit", explicit,
    ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == checkout / expected
