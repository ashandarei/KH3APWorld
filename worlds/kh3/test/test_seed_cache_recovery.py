"""Recover derived seed caches from the pak without changing seed progress."""

import json
from unittest.mock import Mock

import pytest

from ..bridge_support.files import atomic_write_json, load_json_file, load_optional_json_file
from ..client_support.offline_bridge_service import OfflineBridgeInitializationInput
from ..client_support.seed_paks import load_installed_kh3_ap_seed_metadata
from .test_client_status import TestOfflineBridgeService as _ServiceTests
from .test_seed_startup_version import compatible_metadata, seed_pak


@pytest.mark.parametrize("damage", ["catalog", "json"])
def test_invalid_bridge_metadata_and_slot_file_are_backed_up_and_rebuilt(tmp_path, damage):
    metadata = compatible_metadata()
    pak = seed_pak(tmp_path, metadata)
    bridge_root = tmp_path / "bridge"
    bridge = bridge_root / "12345678_Sora"
    bridge.mkdir(parents=True)
    broken_slot = json.loads(json.dumps(metadata["slot_data"]))
    broken_slot["location_catalog"]["sha256"] = "outdated"
    broken_metadata = dict(metadata, slot_data=broken_slot)
    originals = {"kh3_ap_seed_metadata.json": json.dumps(broken_metadata),
                 "slot_data.json": json.dumps(broken_slot)}
    if damage == "json":
        originals = {name: "{truncated" for name in originals}
    for name, text in originals.items():
        (bridge / name).write_text(text)
    status = dict(seed_name="12345678", player_name="Sora", slot=1,
                  checked_locations=[1001], goal_complete=True)
    (bridge / "ap_status.json").write_text(json.dumps(status))
    loader = Mock(side_effect=lambda mods, seed: load_installed_kh3_ap_seed_metadata(mods, expected_seed=seed))
    service = _ServiceTests.build_service(
        list_pak_paths=lambda _: [pak], infer_slot_name_from_pak=lambda _: "Sora",
        load_installed_metadata=loader, load_optional_json=load_optional_json_file,
        atomic_write_json=atomic_write_json, load_cached_items=lambda _: {"items": [{"index": 0}]},
    )
    source = OfflineBridgeInitializationInput(None, bridge_root, pak.parent, "12345678", None, None, None)
    result = service.initialize(source)
    assert result is not None
    loader.assert_called_once()
    assert service.offline_bridge_matches(result.metadata, bridge)
    service.write_offline_files(source=source, metadata=result.metadata, pak_path=pak,
                                bridge_dir=bridge, seed_name="12345678", slot_data=result.slot_data,
                                location_count=0, preserve_previous_status=True)
    assert load_json_file(bridge / "kh3_ap_seed_metadata.json")["slot_data"] == result.slot_data
    assert load_json_file(bridge / "slot_data.json") == result.slot_data
    for name, text in originals.items():
        backups = list(bridge.glob(name + ".*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text() == text
    preserved = load_json_file(bridge / "ap_status.json")
    assert preserved["checked_locations"] == [1001]
    assert preserved["goal_complete"] is True
    assert preserved["received_item_count"] == 1
