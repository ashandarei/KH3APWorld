
from worlds.kh3.test.runtime_sources import native_project_root
import json
import struct
from unittest.mock import patch

import pytest

from ..kh02 import installation as source


def make_install(collection, platform):
    root = collection / source.GAME
    executable = root / "Binaries/Win64" / source.EXE
    executable.parent.mkdir(parents=True)
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    timestamp, image_size, _, _ = source.BUILDS[platform]
    struct.pack_into("<I", data, 72, timestamp)
    struct.pack_into("<I", data, 144, image_size)
    executable.write_bytes(data)
    return root


@pytest.mark.parametrize("platform", ["egs", "steam"])
def test_normalizes_collection_game_binary_executable_and_bridge(tmp_path, platform):
    root = make_install(tmp_path, platform)
    for path in (tmp_path, root, root / "Binaries", root / "Binaries/Win64",
                 root / "Binaries/Win64" / source.EXE, root / "Binaries/Win64/kh02_bridge"):
        assert source.inspect_install(path) == source.Installation(root, platform)
    with pytest.raises(ValueError, match="integrity"):
        source.inspect_install(root, verify=True)


def test_discovers_both_stores_preferring_egs_and_filters(tmp_path):
    epic = make_install(tmp_path / "epic", "egs")
    steamapps = tmp_path / "steamapps"
    steam = make_install(steamapps / "common" / "renamed collection", "steam")
    (steamapps / "appmanifest_2552440.acf").write_text('"appid" "2552440" "installdir" "renamed collection"')
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "valid.item").write_text(json.dumps(dict(DisplayName=source.COLLECTION, InstallLocation=str(epic.parent))))
    (manifests / "broken.item").write_text("{broken")
    args = dict(manifest_root=manifests, steamapps_dirs=[steamapps])
    assert [i.root for i in source.discover_installs(**args)] == [epic, steam]
    assert [i.root for i in source.discover_installs(source="steam", **args)] == [steam]
    assert [i.root for i in source.discover_installs(source="egs", **args)] == [epic]


def test_explicit_selection_wins_and_invalid_selection_does_not_fall_back(tmp_path):
    root = make_install(tmp_path, "steam")
    with patch.dict("os.environ", {"KH02_DIR": str(root)}), patch.object(source, "discover_installs") as discover:
        assert source.selected_install().platform == "steam"
        discover.assert_not_called()
    with patch.dict("os.environ", {"KH02_DIR": str(tmp_path / "missing")}):
        with pytest.raises(OSError):
            source.selected_install()


def test_runtime_selection_preserves_other_config_and_avoids_rewrite(tmp_path):
    root = make_install(tmp_path / "source", "egs")
    host = tmp_path / "host"
    target = host / "Binaries/Win64/kh02_portal_config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"other_option": True}))
    install = source.Installation(root, "egs")
    with patch.object(source, "inspect_install", return_value=install), patch(
            "worlds.kh3.kh02.host_runtime.provision_host") as provision:
        source.sync_runtime(host, install)
        stamp = target.stat().st_mtime_ns
        source.sync_runtime(host, install)
    assert target.stat().st_mtime_ns == stamp
    assert provision.call_count == 2
    from ..installer_support.paths import wine_visible_path_text
    assert json.loads(target.read_text()) == dict(other_option=True, source_platform="egs", source_executable=wine_visible_path_text(install.executable))


def test_native_and_python_build_identities_agree():
    header = (native_project_root() / 'shared/kh02/kh02_executable_verification.h').read_text()
    for timestamp, size, length, digest in source.BUILDS.values():
        assert hex(timestamp) in header
        assert hex(size) in header
        assert str(length) in header
        assert digest in header


@pytest.mark.parametrize("platform", ["egs", "steam"])
def test_config_detection_saves_selected_source_without_changing_kh3(tmp_path, platform):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ..gui.config import ConfigView

    class Value:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value

    install = source.Installation(tmp_path / platform, platform)
    view = ConfigView.__new__(ConfigView)
    view.ctx = SimpleNamespace(kh3_dir=tmp_path / "host", kh3_run_action_running=False)
    view.app = SimpleNamespace(action_running=False)
    view.busy = False
    view.paths = {"KH0.2 Dir": Value(), "KH3 Dir": Value("unchanged host")}
    with patch.object(source, "discover_installs", Mock(return_value=[install])) as discover, \
            patch.object(source, "save_selection", Mock(return_value=install)) as save:
        asyncio.run(view.detect("KH0.2 Dir", platform))
    discover.assert_called_once_with(source=platform)
    save.assert_called_once_with(install.root, view.ctx.kh3_dir)
    assert view.paths["KH0.2 Dir"].get() == str(install.root)
    assert view.paths["KH3 Dir"].get() == "unchanged host"
