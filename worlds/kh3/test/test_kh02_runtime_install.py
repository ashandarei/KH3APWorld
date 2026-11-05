"""Exercise KH0.2 DLL changes only in disposable game folders."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..kh02 import runtime_install as runtime
from ..kh02.installation import Installation
from .runtime_download_fixture import runtime_release_fixture


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("enabled", [False, True])
def test_mod_list_deduplicates_windows_and_unix_entries(newline, enabled):
    original = newline.join([b"; keep comment", b"KH02APProbeCpp : 1", b"Other : 1",
                             b"  kh02approbecpp:0", b""])
    expected = newline.join([b"; keep comment", f"KH02APProbeCpp : {int(enabled)}".encode(),
                             b"Other : 1", b""])
    assert runtime.mod_list(original, enabled) == expected
    assert runtime.mod_list(expected, enabled) == expected


@pytest.fixture
def game(tmp_path, monkeypatch):
    install = Installation(tmp_path / "game", "egs")
    binary = install.executable.parent
    binary.mkdir(parents=True)
    install.executable.write_bytes(b"untouched executable")
    monkeypatch.setattr(runtime, "inspect_install", Mock(return_value=install))
    monkeypatch.setattr(runtime, "is_process_running", Mock(return_value=False))
    files = {runtime.MOD: b"probe", "UE4SS.dll": b"loader", "dwmapi.dll": b"proxy",
             "UE4SS-settings.ini": b"settings", "MemberVariableLayout.ini": b"members",
             "VTableLayout.ini": b"vtable", "override.txt": b".\n"}
    monkeypatch.setattr(runtime, "payload", lambda _: dict(files))
    return binary, files


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_install_repair_uninstall_restores_original_files(game):
    root, files = game
    put(root, "UE4SS-settings.ini", b"original settings")
    put(root, runtime.MODS, b"; original comment\n")
    runtime.install(root)
    files[runtime.MOD] = b"updated probe"
    runtime.install(root)
    assert (root / runtime.MOD).read_bytes() == b"updated probe"
    runtime.uninstall(root)
    assert not (root / runtime.MOD).exists()
    assert not (root / "UE4SS.dll").exists()
    assert (root / "UE4SS-settings.ini").read_bytes() == b"original settings"
    assert (root / runtime.MODS).read_bytes() == b"; original comment\n"
    assert (root / "KINGDOM HEARTS 0.2 Birth by Sleep.exe").read_bytes() == b"untouched executable"


def test_retired_catalog_is_removed_only_when_owned_and_its_backup_stays_reversible(game):
    root, files = game
    runtime.install(root)
    previous = b"original developer metadata"
    installed = b"preceding installer metadata"
    name = runtime.RETIRED_CATALOG
    put(root, name, installed)
    backup = f"{runtime.STATE}/backups/{name}"
    put(root, backup, previous)
    state = runtime.read_state(root)
    state["files"][name] = {"backup": backup, "original_sha256": runtime.digest(previous),
                            "sha256": runtime.digest(installed)}
    runtime.atomic_write_json(root / runtime.STATE / "installation.json", state)
    runtime.install(root)
    assert not (root / name).exists()
    assert (root / backup).read_bytes() == previous
    runtime.uninstall(root)
    assert (root / name).read_bytes() == previous


def test_unowned_retired_catalog_is_preserved_and_new_installs_create_none(game):
    root, _ = game
    runtime.install(root)
    name = runtime.RETIRED_CATALOG
    assert not (root / name).exists()
    put(root, name, b"unowned metadata")
    runtime.install(root)
    assert (root / name).read_bytes() == b"unowned metadata"


def test_uninstall_preserves_new_mods_and_shared_loader(game):
    root, _ = game
    runtime.install(root)
    other = put(root, "Mods/Other/dlls/main.dll", b"other mod")
    put(root, runtime.MODS, b"KH02APProbeCpp : 1\nOther : 1\n")
    runtime.uninstall(root)
    assert other.read_bytes() == b"other mod"
    assert (root / "UE4SS.dll").read_bytes() == b"loader"
    assert (root / runtime.MODS).read_bytes() == b"KH02APProbeCpp : 0\nOther : 1\n"
    assert not (root / runtime.MOD).exists()


def test_developer_install_can_be_removed_without_removing_shared_files(game):
    root, _ = game
    put(root, runtime.MOD, b"developer build")
    put(root, "UE4SS.dll", b"shared loader")
    put(root, runtime.MODS, b"Other:1\nKH02APProbeCpp:1\n")
    runtime.uninstall(root)
    assert not (root / runtime.MOD).exists()
    assert next((root / runtime.MOD).parent.glob("main.dll.uninstalled-*")).read_bytes() == b"developer build"
    assert (root / "UE4SS.dll").read_bytes() == b"shared loader"
    assert (root / runtime.MODS).read_bytes() == b"Other:1\nKH02APProbeCpp : 0\n"


def test_install_over_existing_probe_does_not_reactivate_old_dll_on_uninstall(game):
    root, _ = game
    put(root, runtime.MOD, b"old developer build")
    put(root, runtime.MODS, b"KH02APProbeCpp : 1\n")
    runtime.install(root)
    runtime.uninstall(root)
    assert not (root / runtime.MOD).exists()
    assert (root / runtime.MODS).read_bytes() == b"KH02APProbeCpp : 0\n"
    assert (root / runtime.STATE / "backups" / runtime.MOD).read_bytes() == b"old developer build"


def test_interrupted_upgrade_can_be_uninstalled(game, monkeypatch):
    root, files = game
    runtime.install(root)
    files[runtime.MOD] = b"new build"
    write = runtime.atomic_bytes
    def fail(path, data):
        if path == root / runtime.MOD:
            raise OSError("interrupted copy")
        write(path, data)
    monkeypatch.setattr(runtime, "atomic_bytes", fail)
    with pytest.raises(OSError, match="interrupted"):
        runtime.install(root)
    monkeypatch.setattr(runtime, "atomic_bytes", write)
    runtime.uninstall(root)
    assert not (root / runtime.MOD).exists()


def test_modified_files_are_preserved_and_corrupt_backups_block_removal(game):
    root, _ = game
    put(root, "UE4SS-settings.ini", b"original")
    runtime.install(root)
    put(root, runtime.MOD, b"external update")
    with pytest.raises(RuntimeError, match="outside"):
        runtime.install(root)
    backup = put(root, f"{runtime.STATE}/backups/UE4SS-settings.ini", b"corrupt")
    with pytest.raises(RuntimeError, match="verification"):
        runtime.uninstall(root)
    assert (root / "UE4SS.dll").exists()
    backup.write_bytes(b"original")
    assert "externally changed" in runtime.uninstall(root)
    assert (root / runtime.MOD).read_bytes() == b"external update"


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_running_game_blocks_changes(game, monkeypatch, action):
    root, _ = game
    monkeypatch.setattr(runtime, "is_process_running", lambda _: True)
    with pytest.raises(RuntimeError, match="Close KH0.2"):
        getattr(runtime, action)(root)
    assert not (root / runtime.STATE).exists()


def test_invalid_journal_cannot_write_outside_game(game):
    root, _ = game
    state = {"schema": 1, "root": str(root), "files": {"../../outside.dll": {}}}
    put(root, f"{runtime.STATE}/installation.json", json.dumps(state).encode())
    with pytest.raises(ValueError, match="managed file"):
        runtime.uninstall(root)


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_config_dispatches_selected_directory_and_reports_result(tmp_path, monkeypatch, action):
    from ..gui.config import ConfigView
    view = ConfigView.__new__(ConfigView)
    view.apply = AsyncMock(return_value=True)
    view.paths = {"KH0.2 Dir": SimpleNamespace(get=lambda: str(tmp_path))}
    view.ctx = SimpleNamespace(kh3_run_action_running=False)
    view.app = SimpleNamespace(action_running=False, messages=SimpleNamespace(put=Mock()))
    operation = Mock(return_value="Completed")
    monkeypatch.setattr(runtime, action, operation)
    asyncio.run(view.kh02_action(action))
    view.apply.assert_awaited_once_with("KH0.2 Dir")
    operation.assert_called_once_with(tmp_path)
    view.app.messages.put.assert_called_once_with("Completed")


@pytest.fixture
def verified_release(tmp_path, monkeypatch):
    import io
    from .. import RuntimeDownload
    from ..kh02 import runtime_release
    bundle = tmp_path / "release"
    bundle.mkdir()
    synthetic = {}
    companion = b"MZ-reviewed-companion-fixture"
    synthetic[runtime_release.CONFIG_NAME] = json.dumps(dict(
        repository=runtime_release.REPOSITORY, release_tag="2.0.0", asset="main.dll", asset_protocol=3,
        source="release", size=len(companion), sha256=runtime.digest(companion))).encode()
    monkeypatch.setattr(runtime_release.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(companion))
    for name, data in synthetic.items():
        (bundle / name).write_bytes(data)
    monkeypatch.setattr(runtime, "inspect_install", lambda *a, **kw: Installation(tmp_path / "game", "egs"))
    resource = runtime._runtime_resource
    monkeypatch.setattr(runtime, "_runtime_resource", lambda *parts: (
        bundle / RuntimeDownload.RELEASE_CONFIG_NAME
        if parts == ("runtime", "Win64", RuntimeDownload.RELEASE_CONFIG_NAME) else
        bundle / parts[-1] if parts[:2] == ("runtime", "KH02") and parts[-1] in synthetic else resource(*parts)))
    with runtime_release_fixture(bundle):
        yield bundle


def test_packaged_payload_downloads_companion_and_reuses_verified_loader(tmp_path, monkeypatch, verified_release):
    import io
    import zipfile
    from ..kh02 import runtime_release
    from .. import RuntimeDownload
    from ..installer_support import resources
    root = tmp_path / "game"
    for name in RuntimeDownload.DLL_NAMES:
        put(root, name, b"test-release-" + name.encode())
    archive = tmp_path / "test.apworld"
    with zipfile.ZipFile(archive, "w") as bundle:
        from ..kh02.runtime_release import CONFIG_NAME
        config = json.loads(runtime._runtime_resource("runtime", "KH02", CONFIG_NAME).read_bytes())
        config.update(source="release", size=len(b"downloaded probe"), sha256=runtime.digest(b"downloaded probe"))
        bundle.writestr("kh3/runtime/KH02/" + CONFIG_NAME, json.dumps(config))
        for name in runtime.SETUP:
            bundle.writestr("kh3/runtime/KH02/" + name, b"ModsFolderPath =\n")
        bundle.writestr("kh3/runtime/Win64/" + RuntimeDownload.RELEASE_CONFIG_NAME,
                       (verified_release / RuntimeDownload.RELEASE_CONFIG_NAME).read_bytes())
    monkeypatch.setattr(resources, "_apworld_archive_context", lambda: (archive, "kh3"))
    monkeypatch.setattr(runtime, "_runtime_resource", resources._runtime_resource)
    download = Mock(side_effect=lambda *a, **kw: io.BytesIO(b"downloaded probe"))
    monkeypatch.setattr(runtime_release.urllib.request, "urlopen", download)
    files = runtime.payload(root)
    assert files[runtime.MOD] == b"downloaded probe"
    assert download.call_args.args[0].full_url == "https://github.com/ashandarei/02-GoA/releases/download/2.0.0/main.dll"
    assert runtime.RETIRED_CATALOG not in files
    assert files["UE4SS-settings.ini"] == b"ModsFolderPath =\n"
    assert "UE4SS.dll" not in files and "dwmapi.dll" not in files
    assert [call.args[0] for call in RuntimeDownload._read_url.call_args_list] == [
        RuntimeDownload.release_url(json.loads((verified_release / RuntimeDownload.RELEASE_CONFIG_NAME).read_bytes()))]


def test_clean_install_stages_loader_before_mutating_game(tmp_path, monkeypatch, verified_release):
    from .. import RuntimeDownload
    root = tmp_path / "game"
    root.mkdir()
    monkeypatch.setattr(runtime, "inspect_install", lambda *a, **kw: Installation(tmp_path / "game", "egs"))
    calls = []
    def stage(config, binary, staging):
        assert not list(root.iterdir())
        calls.append(binary)
        (staging / "UE4SS.dll").write_bytes(b"verified loader")
        (staging / "dwmapi.dll").write_bytes(b"verified proxy")
        return {"downloaded": list(RuntimeDownload.DLL_NAMES)}
    monkeypatch.setattr(RuntimeDownload, "stage_runtime", stage)
    files = runtime.payload(root)
    assert calls == [root]
    assert files["UE4SS.dll"] == b"verified loader"
    assert files[runtime.MOD][:2] == b"MZ"
    assert not list(root.iterdir())


@pytest.mark.parametrize("states", [
    ("valid", "valid"), ("wrong", "valid"), ("valid", "wrong"), ("wrong", "wrong"),
    ("missing", "valid"), ("valid", "missing"), ("missing", "missing"),
])
def test_payload_verifies_both_loaders_and_only_stages_mismatches(tmp_path, verified_release, states):
    from .. import RuntimeDownload
    root = tmp_path / "game"
    root.mkdir()
    original = {}
    expected_downloads = []
    for name, state in zip(RuntimeDownload.DLL_NAMES, states):
        expected = b"test-release-" + name.encode()
        if state != "missing":
            # Same size ensures verification must use the hash, not just existence/size.
            original[name] = expected if state == "valid" else b"x" * len(expected)
            put(root, name, original[name])
        if state != "valid":
            expected_downloads.append(name)
    files = runtime.payload(root)
    assert [name for name in RuntimeDownload.DLL_NAMES if name in files] == expected_downloads
    for name in expected_downloads:
        assert files[name] == b"test-release-" + name.encode()
    assert {path.name: path.read_bytes() for path in root.iterdir()} == original
    download_urls = [call.args[0] for call in RuntimeDownload._read_url.call_args_list][1:]
    assert [url.rsplit("/", 1)[-1] for url in download_urls] == expected_downloads


def test_loader_repair_backs_up_and_uninstall_restores_existing_files(tmp_path, monkeypatch, verified_release):
    install = Installation(tmp_path / "game", "egs")
    root = install.executable.parent
    root.mkdir(parents=True)
    monkeypatch.setattr(runtime, "inspect_install", Mock(return_value=install))
    monkeypatch.setattr(runtime, "is_process_running", Mock(return_value=False))
    original = {"UE4SS.dll": b"old loader", "dwmapi.dll": b"old proxy"}
    for name, data in original.items():
        put(root, name, data)
    runtime.install(root)
    for name, data in original.items():
        assert (root / name).read_bytes() == b"test-release-" + name.encode()
        assert (root / runtime.STATE / "backups" / name).read_bytes() == data
    runtime.install(root)
    runtime.uninstall(root)
    for name, data in original.items():
        assert (root / name).read_bytes() == data


def test_bad_loader_download_blocks_install_before_game_writes(tmp_path, monkeypatch, verified_release):
    from .. import RuntimeDownload
    install = Installation(tmp_path / "game", "egs")
    root = install.executable.parent
    root.mkdir(parents=True)
    monkeypatch.setattr(runtime, "inspect_install", Mock(return_value=install))
    monkeypatch.setattr(runtime, "is_process_running", Mock(return_value=False))
    original = {"UE4SS.dll": b"old loader", "dwmapi.dll": b"old proxy"}
    for name, data in original.items():
        put(root, name, data)
    read_url = RuntimeDownload._read_url.side_effect
    RuntimeDownload._read_url.side_effect = lambda url, limit: (
        b"corrupt download" if url.endswith("/dwmapi.dll") else read_url(url, limit))
    with pytest.raises(ValueError, match="SHA-256"):
        runtime.install(root)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == original


@pytest.mark.parametrize("key", ["KH0.2 Dir", "MoM Dir"])
def test_mod_button_detects_files_and_incomplete_installation(tmp_path, key):
    from ..gui.config import ConfigView
    if key == "KH0.2 Dir":
        from .test_kh02_installation import make_install
        root = make_install(tmp_path, "egs")
        binary = root / "Binaries/Win64"
        mod = binary / runtime.MOD
        journal = binary / runtime.STATE / "installation.json"
    else:
        from ..client_support.mom_installation import MOM_EXE, MOD_PATH, STATE_DIR
        root = tmp_path
        put(root, MOM_EXE, b"exe")
        put(root, "GameAssembly.dll", b"assembly")
        mod = root / MOD_PATH
        journal = root / STATE_DIR / "installation.json"
    assert ConfigView.installed_mod_action(key, "") is None
    assert ConfigView.installed_mod_action(key, str(root)) == "install"
    mod.parent.mkdir(parents=True, exist_ok=True)
    mod.write_bytes(b"mod")
    assert ConfigView.installed_mod_action(key, str(root)) == "uninstall"
    mod.unlink()
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}")
    assert ConfigView.installed_mod_action(key, str(root)) == "uninstall"


@pytest.mark.parametrize("key", ["KH0.2 Dir", "MoM Dir"])
def test_mod_button_changes_label_after_action_without_idle_lookups(key):
    from ..gui.config import ConfigView
    view = ConfigView.__new__(ConfigView)
    button = Mock()
    view.mod_buttons = {key: button}
    view.mod_actions = {}
    view.mod_button_revision = 0
    view.mod_buttons_checked_revision = -1
    view.paths = {key: SimpleNamespace(get=lambda: "selected game")}
    view.app = SimpleNamespace(closed=False, action_running=False)
    view.ctx = SimpleNamespace(kh3_run_action_running=False)
    state = {"action": "install"}
    view.installed_mod_action = Mock(side_effect=lambda *_: state["action"])
    async def change(action):
        assert action == state["action"]
        state["action"] = "uninstall" if action == "install" else "install"
    view.kh02_action = AsyncMock(side_effect=change)
    view.mom_action = AsyncMock(side_effect=change)
    async def exercise():
        await view.refresh_mod_buttons()
        button.configure.assert_called_with(text="Install Mod")
        await view.refresh_mod_buttons()
        assert view.installed_mod_action.call_count == 1
        await view.toggle_mod(key)
        button.configure.assert_called_with(text="Uninstall Mod")
        await view.toggle_mod(key)
        button.configure.assert_called_with(text="Install Mod")
        action = view.kh02_action if key == "KH0.2 Dir" else view.mom_action
        assert [call.args for call in action.await_args_list] == [("install",), ("uninstall",)]
        state["action"] = "uninstall"  # Installed externally since the last lookup.
        await view.toggle_mod(key)
        button.configure.assert_called_with(text="Uninstall Mod")
        assert action.await_count == 2  # Refresh the stale label without uninstalling unexpectedly.
    asyncio.run(exercise())
