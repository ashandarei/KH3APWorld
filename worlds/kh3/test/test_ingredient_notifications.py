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
