import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from ..platform_support import uobject_config as config
from ..platform_support import uobject_pool as pool


@pytest.fixture
def save_dir(tmp_path):
    return tmp_path / "OneDrive" / "Documents" / "KINGDOM HEARTS III" / "Epic Games Store" / ("a" * 32) / "SaveGames"


def test_patch_creates_config_without_touching_saves_and_is_idempotent(save_dir):
    result = config.install_uobject_config(save_dir)
    assert result["changed"]
    assert not save_dir.exists()
    path = Path(result["path"])
    assert path.read_text().splitlines() == [f"[{config.SECTION}]", f"{config.KEY}=2097152"]
    assert config.inspect_uobject_config(save_dir)["configured_capacity"] == 2097152
    assert not config.install_uobject_config(save_dir)["changed"]
    assert not list(path.parent.glob("*.apbak-*"))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1252"])
def test_patch_preserves_other_settings_and_backs_up_exact_bytes(save_dir, encoding):
    path = config.engine_ini_path(save_dir)
    path.parent.mkdir(parents=True)
    original = ("; café\r\n[Other]\r\ngc.MaxObjectsInGame=77\r\nKeep=1\r\n"
                f"[{config.SECTION}]\r\ngc.MaxObjectsInGame=1000000\r\ngc.CreateGCClusters=True\r\n").encode(encoding)
    path.write_bytes(original)
    result = config.install_uobject_config(save_dir)
    assert Path(result["backup"]).read_bytes() == original
    updated = path.read_bytes().decode(encoding)
    assert "[Other]\r\ngc.MaxObjectsInGame=77\r\nKeep=1" in updated
    assert "gc.CreateGCClusters=True" in updated
    assert "gc.MaxObjectsInGame=2097152" in updated
    assert config.inspect_uobject_config(save_dir)["status"] == "ready"


def test_higher_limit_is_not_reduced_and_conflicting_duplicates_are_normalized(save_dir):
    path = config.engine_ini_path(save_dir)
    path.parent.mkdir(parents=True)
    path.write_text(f"[{config.SECTION}]\ngc.MaxObjectsInGame=4194304\n"
                    f"[{config.SECTION}]\ngc.MaxObjectsInGame=1000000\n")
    result = config.install_uobject_config(save_dir)
    assert result["configured_capacity"] == 4194304
    assert path.read_text().count("gc.MaxObjectsInGame=") == 1
    before = path.read_bytes()
    assert not config.install_uobject_config(save_dir)["changed"]
    assert before == path.read_bytes()


@pytest.mark.parametrize("tail", ["", "\nOther=1", "\nGC.MAXOBJECTSINGAME=invalid"])
def test_missing_or_invalid_value_in_existing_section(save_dir, tail):
    path = config.engine_ini_path(save_dir)
    path.parent.mkdir(parents=True)
    path.write_text(f"[{config.SECTION}]" + tail)
    config.install_uobject_config(save_dir)
    assert config.inspect_uobject_config(save_dir)["configured_capacity"] == config.MIN_UOBJECT_CAPACITY


def test_unresolved_account_is_reported_without_guessing(tmp_path):
    with pytest.raises(ValueError, match="account"):
        config.install_uobject_config(tmp_path / "SaveGames")
    assert config.inspect_uobject_config(tmp_path / "SaveGames")["status"] == "unavailable"
    assert not (tmp_path / "Config").exists()


def test_failed_atomic_replace_preserves_original(save_dir):
    path = config.engine_ini_path(save_dir)
    path.parent.mkdir(parents=True)
    original = b"[Unrelated]\nKeep=1\n"
    path.write_bytes(original)
    with patch.object(config.os, "replace", side_effect=PermissionError("read only")):
        with pytest.raises(PermissionError):
            config.install_uobject_config(save_dir)
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


def fake_memory(identity, current=411727, capacity=2097152):
    dos, nt = bytearray(64), bytearray(84)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 256)
    nt[:4] = b"PE\0\0"
    struct.pack_into("<H", nt, 4, 0x8664)
    struct.pack_into("<I", nt, 8, identity[0])
    struct.pack_into("<H", nt, 24, 0x20B)
    struct.pack_into("<I", nt, 80, identity[1])
    base = 0x7FF000000000
    header = struct.pack("<Qii", 0x10000000, capacity, current)
    memory = {base: bytes(dos), base + 256: bytes(nt)}
    if identity in pool.PROFILES:
        memory[base + pool.PROFILES[identity][1]] = header
    def read(address, size):
        assert len(memory[address]) == size
        return memory[address]
    return read, base


@pytest.mark.parametrize("identity", list(pool.PROFILES))
def test_reader_uses_verified_layout_and_reports_current_max(identity):
    result = pool.read_pool_header(*fake_memory(identity))
    assert result["status"] == "ok"
    assert (result["current"], result["capacity"]) == (411727, 2097152)
    assert result["minimum_applied"]


def test_unknown_executable_rejected_before_array_read():
    with pytest.raises(ValueError, match="version"):
        pool.read_pool_header(*fake_memory((123, 456)))


def test_corrupt_count_not_misreported_as_valid_usage():
    result = pool.read_pool_header(*fake_memory(next(iter(pool.PROFILES)), current=1002157, capacity=1000000))
    assert result["status"] == "invalid_header"
    assert result["current"] == 1002157


def test_reader_rejects_unstable_snapshot():
    read, base = fake_memory(next(iter(pool.PROFILES)))
    calls = 0
    def unstable(address, size):
        nonlocal calls
        if size == 16:
            calls += 1
            return struct.pack("<Qii", 0x10000000, 2097152, calls)
        return read(address, size)
    with pytest.raises(ValueError, match="changing"):
        pool.read_pool_header(unstable, base)


def test_display_identifies_snapshot_and_low_capacity():
    text = pool.format_uobject_snapshot(dict(status="ok", current=411727, capacity=1000000, at="now", pid=42))
    assert "411,727 / 1,000,000" in text
    assert "Snapshot now" in text
    assert "restart" in text


def test_patch_action_installs_configuration_for_selected_account(save_dir):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from ..Client import KH3Context
    from ..client_support.runtime_artifact_service import RuntimeArtifactAction, RuntimeArtifactActionKind

    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_dir = save_dir.parents[3]
    ctx.bridge_dir = None
    ctx.bridge_save_dir = save_dir
    ctx.probe_config_path = None
    ctx._get_client_status_presenter = lambda: SimpleNamespace(invalidate_runtime_status_cache=Mock())
    with patch(
        "worlds.kh3.client_support.runtime_artifact_executor.install_kh3_runtime_files", return_value={"status": "ok"}
    ):
        ctx._create_runtime_artifact_executor().apply(RuntimeArtifactAction(RuntimeArtifactActionKind.INSTALL_RUNTIME_FILES))
    assert config.inspect_uobject_config(save_dir)["status"] == "ready"


def test_bundle_records_live_capacity_config_and_last_button_snapshot(save_dir, tmp_path):
    import json
    import zipfile

    from ..client_support import debug_bundle

    config.install_uobject_config(save_dir)
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "uobject_pool_snapshot.json").write_text('{"status":"ok","current":123}')
    snapshot = dict(status="ok", current=456, capacity=2097152)
    with patch.object(debug_bundle, "snapshot_uobject_pool", return_value=snapshot), \
            patch.object(debug_bundle, "_kh3_process_summary", return_value={"running": False}):
        result = debug_bundle.export_kh3_debug_bundle(
            kh3_dir=tmp_path / "game", bridge_dir=bridge, kh3_mods_dir=tmp_path / "mods",
            save_dir=save_dir, ue4ss_log=None, output_dir=tmp_path / "output")
    with zipfile.ZipFile(result["bundle_path"]) as archive:
        summary = json.loads(archive.read("debug_summary.json"))
        assert summary["uobject_pool"] == snapshot
        assert summary["uobject_config"]["configured_capacity"] == 2097152
        assert archive.read("config/Engine.ini") == config.engine_ini_path(save_dir).read_bytes()
        assert any(name.endswith("/uobject_pool_snapshot.json") for name in archive.namelist())
