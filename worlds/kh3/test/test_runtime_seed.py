import copy
import json

import pytest

from ..bridge_support.files import atomic_write_json
from ..RuntimeSeed import (
    HINTS_FILE,
    LIMITS,
    SETTINGS_FILE,
    encode_document,
    hint_reports,
    metadata_payload,
    runtime_documents,
)


def metadata():
    return {
        "format": "kh3_ap_seed_metadata", "format_version": 1,
        "seed": "AP_12345678-1-Sora", "slot": 1, "slot_name": "Sora",
        "slot_data": {
            "kh3_seed": {"seed_name": "12345678", "slot": 1,
                         "remote_hint_placements": [{"name": "remote"}], "all_players": ["Sora"]},
            "bridge": {"locations": [{"bridge_key": "TreasureTS:TS_SBOX_002", "ap_location_id": 123}]},
            "kh3_randomizer": {"pools": {"Enemies": "Randomize"}}, "include_space": 1,
            "universal_tracker": {"schema_version": 1, "options": {"chestsanity": 1, "portal_unlocks": 0}},
        },
        "native_randomization": {"version": 1, "enemy_chaos": True, "boss_blacklist": []},
        "party_randomization_initially_enabled": True,
        "placement_summary": {"locations": [{"name": "spoiler"}]},
        "foreign_item_manifest": {"archive_name": "AP_12345678.zip", "foreign_items": [{"item": "data"}]},
        "secret_report_hints": {"reports": {"1": [
            {"player": 2, "location": 123, "name": "presentation only"},
            {"player": 2, "location": 123}, {"player": 1, "location": 200}]}},
    }


def test_runtime_projection_retains_gameplay_and_separates_optional_hints():
    source = metadata()
    original = copy.deepcopy(source)
    projected = metadata_payload(source)
    documents = runtime_documents(projected)
    assert source == original
    assert "placement_summary" not in projected
    assert projected["foreign_item_manifest"] == {"archive_name": "AP_12345678.zip"}
    slot = documents["slot_data.json"]
    assert slot["bridge"] == source["slot_data"]["bridge"]
    assert slot["kh3_randomizer"] == source["slot_data"]["kh3_randomizer"]
    assert slot["include_space"] == 1
    assert slot["kh3_seed"] == {"seed_name": "12345678", "slot": 1}
    from ..universal_tracker import universal_tracker_options_from_slot_data
    assert universal_tracker_options_from_slot_data(slot) == {"chestsanity": 1, "portal_unlocks": 0}
    settings = documents[SETTINGS_FILE]
    assert "bridge" not in settings["slot_data"]
    assert "universal_tracker" not in settings["slot_data"]
    assert settings["native_randomization"] == source["native_randomization"]
    assert "enemy_runtime_remap" not in settings and "boss_runtime_remap" not in settings
    assert settings["party_randomization_initially_enabled"] is True
    assert documents[HINTS_FILE] == {
        "schema": "kh3ap.seed_hints.v1", "seed_name": "12345678", "slot": 1,
        "reports": {"1": [{"player": 1, "location": 200}, {"player": 2, "location": 123}]},
    }


@pytest.mark.parametrize("key,value", [
    ("music_shuffle", 3), ("include_custom_music", 0), ("form_themes", 1),
    ("auto_skip_cutscenes", 1), ("skip_guardians_and_mickey_walk", 1),
    ("fast_dance_minigame", 1), ("fast_beat_of_lead", 1), ("fast_union_cross", 1),
    ("dark_baymax_phase_1_skip", 1), ("max_level_ship", 1), ("fun_10k_heartless", 1),
    ("stylesanity", 0), ("keyblade_levels", 0), ("map_overlay_keybinds", 0),
    ("melody_of_memory_difficulty", 2), ("melody_of_memory_party", 3),
    ("moogle_shop_checks", 80), ("ultima_weapon_mode", 1),
    ("ultima_weapon_shop_required_keyblades", ["EX030"]),
    ("include_space", 1), ("include_grindy_space_missions", 1), ("include_space_superbosses", 1),
    ("portal_unlocks", 1), ("worlds_required", 9), ("heart_piece_count", 12),
    ("kh02_enabled", 0), ("kh02_enabled", 1), ("kh02_challenges", 0), ("kh02_challenges", 1),
    ("classic_kingdom_checks", 2), ("olympus_coliseum_pool", 2),
])
def test_fixed_options_survive_pak_settings_encoding(key, value):
    source = metadata()
    source["slot_data"][key] = value
    settings = runtime_documents(source)[SETTINGS_FILE]
    stored = json.loads(encode_document(SETTINGS_FILE, settings))
    assert stored["slot_data"][key] == value
    assert "bridge" not in stored["slot_data"]


def test_remote_generation_growth_does_not_increase_runtime_documents(tmp_path):
    source = metadata()
    expected = runtime_documents(source)
    source["slot_data"]["kh3_seed"]["remote_hint_placements"] *= 10000
    source["placement_summary"]["locations"] *= 10000
    source["foreign_item_manifest"]["foreign_items"] *= 10000
    assert runtime_documents(source) == expected
    atomic_write_json(tmp_path / "kh3_ap_seed_metadata.json", source)
    atomic_write_json(tmp_path / "slot_data.json", source["slot_data"])
    assert json.loads((tmp_path / "kh3_ap_seed_metadata.json").read_bytes()) == metadata_payload(metadata())
    assert json.loads((tmp_path / "slot_data.json").read_bytes()) == expected["slot_data.json"]
    assert b"\n" not in (tmp_path / "slot_data.json").read_bytes()


@pytest.mark.parametrize("filename,limit", [("slot_data.json", "seed_bytes"),
                                         (SETTINGS_FILE, "seed_bytes"), (HINTS_FILE, "hints_bytes")])
def test_runtime_limits_count_utf8_bytes_and_preserve_previous_file(tmp_path, monkeypatch, filename, limit):
    source = {"name": "世界"}
    encoded = encode_document(filename, source)
    monkeypatch.setitem(LIMITS, limit, len(encoded))
    atomic_write_json(tmp_path / filename, source)
    assert (tmp_path / filename).read_bytes() == encoded
    with pytest.raises(ValueError, match="size limit"):
        atomic_write_json(tmp_path / filename, {"name": "世界!"})
    assert (tmp_path / filename).read_bytes() == encoded


def test_depth_policy_matches_native_containers():
    payload = {}
    for _ in range(LIMITS["document_depth"] - 1):
        payload = {"child": payload}
    encode_document("slot_data.json", payload)
    with pytest.raises(ValueError, match="nesting limit"):
        encode_document("slot_data.json", {"child": payload})


def test_pak_import_checks_projected_size_before_accepting_metadata(monkeypatch):
    from ..client_support.seed_paks import _decode_kh3_ap_seed_metadata

    source = metadata()
    monkeypatch.setitem(LIMITS, "seed_bytes", 100)
    with pytest.raises(ValueError, match="slot_data.json.*maximum 100"):
        _decode_kh3_ap_seed_metadata(json.dumps(source), source="test-pak")


@pytest.mark.parametrize("hints", [None, [], {"reports": []}, {"reports": {"14": []}},
                                   {"reports": {"1": [None]}},
                                   {"reports": {"1": [{"player": True, "location": 12}]}},
                                   {"reports": {"1": [{"player": 1, "location": 0}]}}])
def test_bad_hint_targets_fail_during_generation_or_import(hints):
    with pytest.raises(ValueError, match="Secret Report"):
        hint_reports({"secret_report_hints": hints})
