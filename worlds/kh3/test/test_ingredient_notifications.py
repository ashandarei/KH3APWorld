from pathlib import Path

from ..Client import KH3Context
from ..client_support.presentation import describe_last_ingredient_pickup
from ..client_support.status_snapshot_service import ClientStatusSnapshotService
from ..Ingredients import INGREDIENT_STATE_SCHEMA

SLOT = {"kh3_seed": {"seed_name": "notification-test", "slot": 1}}


def event(**overrides):
    return dict(schema=INGREDIENT_STATE_SCHEMA, source="food_prize",
                seed_name="notification-test", slot=1,
                last_pickup_sequence=4, last_pickup_spot="RA_01_F_003",
                last_pickup_at_utc="2026-09-11T18:00:00Z") | overrides


def test_latest_pickup_shows_exact_spot_id_including_repeat_harvests():
    payload = event(acquired_ingredients=["FST_ITEM26", "FST_ITEM28"],
                    inventory={"FST_ITEM26": 99})
    assert describe_last_ingredient_pickup(payload, SLOT) == (
        "RA_01_F_003 | Kingdom of Corona - The Forest - Pickup 003 (Possible drops: Bay Leaf)")
    # Repeat pickup remains visible even though the ingredient was already checked.
    assert describe_last_ingredient_pickup(payload | {"last_pickup_sequence": 5}, SLOT).startswith("RA_01_F_003 |")


def test_breakable_release_needs_no_item_data_and_legacy_items_are_ignored():
    payload = event(last_pickup_spot="MI_01_PF_001")
    expected = ("MI_01_PF_001 | Monstropolis - Monsters, Inc. - Pickup 001 "
                "(Possible drops: Lemon, Orange)")
    assert describe_last_ingredient_pickup(payload, SLOT) == expected
    payload["last_pickup_items"] = [{"id": "FST_ITEM35", "quantity": 2}]
    assert describe_last_ingredient_pickup(payload, SLOT) == expected


def test_spot_descriptions_cover_catalog_without_inventing_missing_drops():
    from ..Ingredients import INGREDIENT_SPOTS, INGREDIENT_SPOT_DESCRIPTIONS

    assert set(INGREDIENT_SPOT_DESCRIPTIONS) == {spot["id"] for spot in INGREDIENT_SPOTS}
    for description in INGREDIENT_SPOT_DESCRIPTIONS.values():
        assert " -  - " not in description
        assert "Map " not in description
    assert INGREDIENT_SPOT_DESCRIPTIONS["BX_02_PF_007"] == (
        "Ingredients+ - San Fransokyo - The City - Pickup 007 (Possible drops: Garlic, Zucchini)")
    assert " - Twilight Town - The Neighborhood - " in INGREDIENT_SPOT_DESCRIPTIONS["TT_01c_F_001"]
    assert INGREDIENT_SPOT_DESCRIPTIONS["TT_01c_F_001"].endswith("(Drop unknown)")


def test_tracker_aliases_are_plain_ingredient_names_with_unchanged_pickup_ids():
    from ..Ingredients import INGREDIENT_SPOTS, INGREDIENT_SPOT_DISPLAY_NAMES
    from ..Locations import location_id_to_alias, location_name_to_id

    assert {"Basil", "Carrot"}.issubset(set(INGREDIENT_SPOT_DISPLAY_NAMES.values()))
    for spot in INGREDIENT_SPOTS:
        location_id = location_name_to_id[spot["name"]]
        alias = location_id_to_alias[location_id]
        assert alias == INGREDIENT_SPOT_DISPLAY_NAMES[spot["id"]]
        assert "Pickup" not in alias
        assert spot["id"] not in alias
    assert INGREDIENT_SPOT_DISPLAY_NAMES["BX_02_PF_007"] == "Garlic / Zucchini"
    assert INGREDIENT_SPOT_DISPLAY_NAMES["TT_01c_F_001"] == "Unknown ingredient"
    assert {
        spot["id"]: location_name_to_id[spot["name"]]
        for spot in INGREDIENT_SPOTS if spot["id"] in {"BX_02_PF_001", "RA_01_F_003", "TT_01c_F_001"}
    } == {
        "BX_02_PF_001": 1263023524,
        "RA_01_F_003": 1263023714,
        "TT_01c_F_001": 1263023817,
    }


def test_client_details_do_not_change_generated_catalog_records():
    from ..Ingredients import append_ingredient_locations, INGREDIENT_SPOT_DESCRIPTIONS
    from ..Data import normalize_locations
    from ..client_support.notification_service import ClientNotificationService
    from ..client_support.item_sync_service import ItemSyncService

    data = {"locations": []}
    append_ingredient_locations(data)
    location = next(row for row in data["locations"] if row["source_id"] == "RA_01_F_003")
    expected = INGREDIENT_SPOT_DESCRIPTIONS["RA_01_F_003"]
    assert location["name"] == "Ingredients+ - Kingdom of Corona - Pickup 003"
    assert location["display_name"] == "Bay Leaf"
    assert location["location_description"] == location["name"]
    normalized = next(row for row in normalize_locations(data) if row["source_id"] == "RA_01_F_003")
    assert normalized["name"] == location["name"]
    assert normalized["location_description"] == location["name"]
    location["location_description"] = location["name"]
    assert ClientNotificationService._display_slot_location(location) == expected
    assert ItemSyncService.display_location_name(location) == expected


def test_wrong_seed_shop_legacy_and_malformed_payloads_are_not_displayed():
    for override in ({"seed_name": "other"}, {"slot": 2}, {"slot": True},
                     {"source": "shop"}, {"schema": "old"}, {"last_pickup_spot": ""},
                     {"last_pickup_spot": "UNKNOWN"}, {"last_pickup_spot": "FST_ITEM28"},
                     {"last_pickup_spot": None}, {"last_pickup_spot": []}):
        assert describe_last_ingredient_pickup(event(**override), SLOT) == "-"
    assert describe_last_ingredient_pickup({"acquired_ingredients": ["FST_ITEM28"]}, SLOT) == "-"
    legacy = event(acquired_spots=["RA_01_F_003"])
    del legacy["last_pickup_spot"]
    assert describe_last_ingredient_pickup(legacy, SLOT) == "-"
    assert describe_last_ingredient_pickup(event(), {}) == "-"


def test_status_service_preserves_event_payload():
    payload = event()
    inputs = ClientStatusSnapshotService.inputs_from_payloads({"runtime_ingredient_state": payload})
    assert inputs.as_presenter_payloads()["runtime_ingredient_state"] == payload


def test_client_debug_snapshot_refreshes_and_clears_on_seed_change():
    ctx = KH3Context.__new__(KH3Context)
    ctx.checked_locations = set()
    ctx.locations_checked = set()
    ctx.missing_locations = set()
    ctx.location_key_to_id = {}
    ctx.player_names = {}
    ctx.slot = None
    ctx.auth = "Player"
    ctx.seed_name = "notification-test"
    ctx.slot_data = SLOT
    ctx.bridge_dir = Path("bridge")
    ctx.server = None
    ctx.items_received = []
    ctx.installed_kh3_ap_seed_paks = lambda: []
    ctx._load_bridge_payload = lambda name: {}
    current = event()
    ctx._load_bridge_sidecar_payload = lambda name: current if name == "runtime_ingredient_state.json" else {}
    def label():
        return ctx._get_status_snapshot_service().build_snapshot().as_legacy_dict()["last_ingredient_pickup"]
    assert label().startswith("RA_01_F_003 |")
    current = event(last_pickup_spot="RA_01_F_001", last_pickup_sequence=5)
    assert label().startswith("RA_01_F_001 |")
    ctx.slot_data = {"kh3_seed": {"seed_name": "different-seed", "slot": 1}}
    assert label() == "-"
