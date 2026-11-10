"""Pinned native downloads are verified before installation touches the game."""

import hashlib
import io
import json
import zipfile
from unittest.mock import patch

import pytest

from ..installer_support import installation, probe_release, resources, status
from .runtime_download_fixture import probe_release_fixture, probe_release_payload


def source_for(tmp_path, config):
    path = tmp_path / "runtime-release.json"
    path.write_text(json.dumps(config))
    return probe_release.PinnedProbeResource(path)


def test_download_checks_archive_and_dll_then_reuses_verified_bytes(tmp_path):
    config, payload = probe_release_payload()
    source = source_for(tmp_path, config)
    with patch.object(probe_release, "_download", return_value=payload) as download:
        assert source.is_file()
        download.assert_not_called()
        assert source.read_bytes() == b"probe"
        assert source.read_bytes() == b"probe"
        download.assert_called_once_with(
            f"https://github.com/ashandarei/KH3Runtime/releases/download/2.0.0/{config['asset']}", len(payload))


@pytest.mark.parametrize("field,value", [
    ("repository", "other/repository"), ("release_tag", "latest"), ("version", "2.0.1"),
    ("asset", "../main.dll"), ("sha256", "bad"), ("archive_sha256", "bad"),
    ("size", True), ("archive_size", 0), ("size", probe_release.MAX_SIZE + 1),
])
def test_invalid_pins_fail_before_download(tmp_path, field, value):
    config, _ = probe_release_payload()
    config[field] = value
    source = source_for(tmp_path, config)
    with patch.object(probe_release, "_download") as download:
        assert not source.is_file()
        with pytest.raises(ValueError):
            source.read_bytes()
        download.assert_not_called()


@pytest.mark.parametrize("mutation", ["truncated", "oversized", "corrupt", "wrong-dll", "duplicate", "entry-size"])
def test_rejects_corrupt_or_mismatched_download(tmp_path, mutation):
    config, payload = probe_release_payload()
    if mutation == "truncated":
        payload = payload[:-1]
    elif mutation == "oversized":
        payload += b"X"
    elif mutation == "corrupt":
        payload = b"X" * len(payload)
    else:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(probe_release.DLL_ENTRY, b"wrong" if mutation == "wrong-dll" else b"probe")
            if mutation == "duplicate":
                with pytest.warns(UserWarning):
                    archive.writestr(probe_release.DLL_ENTRY, b"probe")
        payload = output.getvalue()
        config.update(archive_size=len(payload), archive_sha256=hashlib.sha256(payload).hexdigest())
        if mutation == "entry-size":
            config["size"] += 1
    with patch.object(probe_release, "_download", return_value=payload), pytest.raises(ValueError):
        source_for(tmp_path, config).read_bytes()


def test_installed_hash_match_works_offline_and_same_size_mismatch_does_not(tmp_path):
    config, _ = probe_release_payload()
    installed = tmp_path / "main.dll"
    installed.write_bytes(b"probe")
    with patch.object(probe_release, "_download", side_effect=OSError("offline")) as download:
        source = source_for(tmp_path, config)
        source.use_installed(installed)
        assert source.read_bytes() == b"probe"
        download.assert_not_called()
        installed.write_bytes(b"wrong")
        source = source_for(tmp_path, config)
        source.use_installed(installed)
        with pytest.raises(OSError):
            source.read_bytes()


@pytest.mark.parametrize("failure", [OSError("offline"), b"corrupted archive"])
def test_failed_probe_download_preserves_install_and_skips_prerequisites(tmp_path, failure):
    win64 = tmp_path / "game/Binaries/Win64"
    win64.mkdir(parents=True)
    (win64 / "UE4SS.dll").write_bytes(b"original loader")
    probe = tmp_path / "fixture.dll"
    probe.write_bytes(b"probe")
    before = {str(p.relative_to(win64)): p.read_bytes() for p in win64.rglob("*") if p.is_file()}
    kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with probe_release_fixture(probe), \
            patch.object(installation, "stage_runtime", return_value={"skipped": []}), \
            patch.object(probe_release, "_download", **kwargs), \
            patch.object(installation, "prepare_xinput_runtime") as prerequisite:
        result = installation.install_kh3_runtime(tmp_path / "game")
    assert result["status"] == "error"
    assert "no runtime files changed" in result["errors"][0]
    prerequisite.assert_not_called()
    assert before == {str(p.relative_to(win64)): p.read_bytes() for p in win64.rglob("*") if p.is_file()}


def test_status_checks_pin_without_network_and_detects_same_size_mismatch(tmp_path):
    probe = tmp_path / "fixture.dll"
    probe.write_bytes(b"probe")
    installed = tmp_path / "game/Binaries/Win64/Mods/KH3APProbeCpp/dlls/main.dll"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"wrong")
    with probe_release_fixture(probe), patch.object(probe_release, "_download") as download:
        snapshot = status.runtime_install_status(tmp_path / "game")
        assert snapshot["bundle_probe_exists"]
        assert not snapshot["probe_dll_matches_bundle"]
        installed.write_bytes(b"probe")
        assert status.runtime_install_status(tmp_path / "game")["probe_dll_matches_bundle"]
        download.assert_not_called()


def test_repository_pin_matches_world_version_without_bundled_dll():
    source = resources._runtime_probe_source()
    manifest = json.loads(resources._runtime_resource("archipelago.json").read_bytes())
    assert source.config["version"] == manifest["world_version"]
    assert not resources._runtime_resource("runtime", "Mods", "KH3APProbeCpp", "dlls", "main.dll").is_file()


@pytest.mark.parametrize("overrides", [
    {"performance_diagnostics": True}, {"automation_handlers": True},
    {"release_eligible": False}, {"ue4ss_dirty": True}, {"allow_unpinned_ue4ss": True},
    {"first_party_license": "other"}, {"sha256": "0" * 64},
])
def test_rejects_non_player_or_unverified_metadata(tmp_path, overrides):
    config, payload = probe_release_payload(metadata_overrides=overrides)
    with patch.object(probe_release, "_download", return_value=payload), pytest.raises(ValueError):
        source_for(tmp_path, config).read_bytes()
