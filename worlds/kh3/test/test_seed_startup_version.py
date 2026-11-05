"""Check seed contents before their cached bridge can close the client."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from .. import Client, PakGenerator
from ..Pak import pack_directory
from ..client_support.seed_paks import KH3_AP_SEED_METADATA_PAK_PATH, deactivate_incompatible_seed_paks
from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..SlotData import encode_slot_data


def compatible_metadata():
    protocol = dict(protocol_name=BRIDGE_PROTOCOL_NAME, protocol_version=BRIDGE_PROTOCOL_VERSION)
    return dict(format="kh3_ap_seed_metadata", format_version=1,
                seed="AP_12345678-1-Sora", slot=1, slot_name="Sora",
                foreign_item_manifest={"archive_name": "AP_12345678.zip"},
                slot_data=encode_slot_data(dict(protocol, bridge=dict(protocol, locations=[]),
                                               kh3_seed={"seed_name": "12345678", "slot": 1})))


def seed_pak(tmp_path, metadata, name="pakchunk9-Sora-randomizer-AP_12345678-1_99_P.pak"):
    source = tmp_path / "source"
    target = source / KH3_AP_SEED_METADATA_PAK_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata))
    mods = tmp_path / "mods"
    mods.mkdir(exist_ok=True)
    pak = mods / name
    pack_directory(source, pak)
    return pak


@pytest.mark.parametrize("version", [None, "", "  ", "0.17.25"])
def test_incompatible_catalog_is_preserved_and_disabled_regardless_of_version(tmp_path, version):
    metadata = compatible_metadata()
    metadata["slot_data"]["location_catalog"]["sha256"] = "wrong-catalog"
    if version is not None:
        metadata["seed_creation_version"] = version
    pak = seed_pak(tmp_path, metadata)
    original = pak.read_bytes()
    cached = tmp_path / "bridge/kh3_ap_seed_metadata.json"
    cached.parent.mkdir()
    cached.write_text(json.dumps(metadata))
    disabled = pak.with_name(pak.name + ".disabled")
    disabled.write_bytes(b"previous disabled seed")
    assert deactivate_incompatible_seed_paks(pak.parent)
    assert not pak.exists()
    assert disabled.read_bytes() == b"previous disabled seed"
    assert any(p.read_bytes() == original for p in pak.parent.glob("*.disabled"))
    assert json.loads(cached.read_text()) == metadata
    assert deactivate_incompatible_seed_paks(pak.parent)


@pytest.mark.parametrize("version", [None, "", "0.1.0", "0.17.25"])
def test_compatible_seed_and_unrelated_mod_stay_active_regardless_of_version(tmp_path, version):
    metadata = compatible_metadata()
    if version is not None:
        metadata["seed_creation_version"] = version
    pak = seed_pak(tmp_path, metadata)
    unrelated = pak.parent / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak"
    unrelated.write_bytes(b"not a seed")
    original = pak.read_bytes()
    assert deactivate_incompatible_seed_paks(pak.parent)
    assert pak.read_bytes() == original
    assert unrelated.read_bytes() == b"not a seed"


def test_failed_disable_blocks_offline_startup(tmp_path):
    pak = seed_pak(tmp_path, {})
    with patch("pathlib.Path.rename", side_effect=PermissionError("locked")):
        assert not deactivate_incompatible_seed_paks(pak.parent)
    assert pak.is_file()


@pytest.mark.parametrize("writer", ["write_archipelago_seed_metadata", "write_container_seed_metadata"])
def test_pak_metadata_keeps_generation_version_instead_of_current_client_version(tmp_path, writer):
    for suffix in ("foreign-items", "placement-summary"):
        (tmp_path / f"seed.{suffix}.json").write_text("{}")
    slot = {"slot": 1, "name": "Sora", "slot_name": "Sora",
            "slot_data": {"kh3_seed": {"seed_creation_version": "0.17.25"}}}
    metadata = json.loads(getattr(PakGenerator, writer)(tmp_path, slot, "seed").read_text())
    assert metadata["seed_creation_version"] == "0.17.25"


@pytest.mark.parametrize("safe", [True, False])
def test_main_checks_compatibility_before_gui_and_offline_metadata_read(tmp_path, safe):
    async def run():
        events = []
        ctx = SimpleNamespace(kh3_mods_dir=tmp_path, auth=None, exit_event=asyncio.Event(),
                              initialize_offline_bridge_after_startup=AsyncMock())
        ctx.exit_event.set()
        ctx.lifecycle_service = SimpleNamespace(
            start_gui=lambda: events.append("gui"), start_cli=Mock(), start_bridge_watcher=Mock(),
            shutdown=AsyncMock(),
        )
        def check(mods):
            assert mods == tmp_path
            events.append("compatibility")
            return safe
        with patch.object(Client, "KH3Context", return_value=ctx), patch.object(Client, "gui_enabled", True), \
                patch.object(Client, "deactivate_incompatible_seed_paks", side_effect=check):
            await Client.main()
        assert events == ["compatibility", "gui"]
        assert ctx.initialize_offline_bridge_after_startup.await_count == int(safe)
    asyncio.run(run())
