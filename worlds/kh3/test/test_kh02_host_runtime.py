"""Host prerequisites use the normal selection flow and preserve existing settings."""
import hashlib
import json
import io
from unittest.mock import Mock

import pytest

from ..kh02 import host_runtime


@pytest.fixture
def resource(tmp_path, monkeypatch):
    package = tmp_path / "package/runtime/KH02"
    package.mkdir(parents=True)
    data = b"synthetic authored map"
    (package / host_runtime.HOST_PAK).write_bytes(data)
    (package / host_runtime.HOST_MANIFEST).write_text(json.dumps(dict(
        schema=1, repository="ashandarei/AP-GOA-KH3", release_tag="2.0.0", file=host_runtime.HOST_PAK, size=len(data), sha256=hashlib.sha256(data).hexdigest())))
    download = Mock(side_effect=lambda *args, **kwargs: io.BytesIO((package / host_runtime.HOST_PAK).read_bytes()))
    monkeypatch.setattr(host_runtime.urllib.request, "urlopen", download)
    return lambda *parts: tmp_path.joinpath("package", *parts)


def test_new_host_and_unchanged_refresh(tmp_path, resource):
    root = tmp_path / "game"
    host_runtime.provision_host(root, resource)
    config = root / "Binaries/Win64/kh02_story_config.json"
    pak = root / "Content/Paks/~mods" / host_runtime.HOST_PAK
    assert json.loads(config.read_text()) == host_runtime.STORY_DEFAULTS
    assert pak.read_bytes() == b"synthetic authored map"
    stamps = config.stat().st_mtime_ns, pak.stat().st_mtime_ns
    host_runtime.urllib.request.urlopen.side_effect = AssertionError("unchanged host must work offline")
    host_runtime.provision_host(root, resource)
    assert stamps == (config.stat().st_mtime_ns, pak.stat().st_mtime_ns)


def test_existing_config_preserved_and_old_map_backed_up(tmp_path, resource):
    root = tmp_path / "game"
    config = root / "Binaries/Win64/kh02_story_config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"prepare_gameplay_on_entry":false,"custom":true}')
    pak = root / "Content/Paks/~mods" / host_runtime.HOST_PAK
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"old host map")
    host_runtime.provision_host(root, resource)
    assert json.loads(config.read_text()) == dict(prepare_gameplay_on_entry=False, custom=True)
    backups = list(pak.parent.glob(pak.name + ".apbak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old host map"


def test_bad_map_preserves_host(tmp_path, resource):
    resource("runtime", "KH02", host_runtime.HOST_PAK).write_bytes(b"corrupt")
    root = tmp_path / "game"
    with pytest.raises(ValueError, match="verification"):
        host_runtime.provision_host(root, resource)
    assert not root.exists()


@pytest.mark.parametrize("payload", [b"corrupt", b"x" * 4096])
def test_bad_download_preserves_installed_map_and_settings(tmp_path, resource, payload):
    root = tmp_path / "game"
    pak = root / "Content/Paks/~mods" / host_runtime.HOST_PAK
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"previous map")
    resource("runtime", "KH02", host_runtime.HOST_PAK).write_bytes(payload)
    with pytest.raises(ValueError, match="verification"):
        host_runtime.provision_host(root, resource)
    assert pak.read_bytes() == b"previous map"
    assert not list(pak.parent.glob("*.apbak-*"))
    assert not (root / "Binaries").exists()


def test_download_is_version_pinned_and_uses_verified_tls(resource):
    host_runtime.host_pak_bytes(resource)
    args, kwargs = host_runtime.urllib.request.urlopen.call_args
    assert args == ("https://github.com/ashandarei/AP-GOA-KH3/releases/download/2.0.0/" + host_runtime.HOST_PAK,)
    import ssl
    assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["context"].check_hostname


@pytest.mark.parametrize("field,value", [("repository", "other/repo"), ("release_tag", "latest"), ("release_tag", "../2.0.0"), ("size", 0)])
def test_invalid_manifest_never_downloads(resource, field, value):
    path = resource("runtime", "KH02", host_runtime.HOST_MANIFEST)
    entry = json.loads(path.read_text())
    entry[field] = value
    path.write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="manifest"):
        host_runtime.host_pak_bytes(resource)
    host_runtime.urllib.request.urlopen.assert_not_called()


def test_network_failure_does_not_use_bundled_fallback(tmp_path, resource):
    host_runtime.urllib.request.urlopen.side_effect = OSError("offline")
    with pytest.raises(OSError, match="offline"):
        host_runtime.provision_host(tmp_path / "game", resource)
    assert not (tmp_path / "game").exists()
