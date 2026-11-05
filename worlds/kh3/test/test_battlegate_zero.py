import json
from pathlib import Path

from ..Bridge import build_bridge_slot_data
from ..Data import load_extracted_data, normalize_locations
from ..export_ap_kh3_randomized_options import apply_event_override


def test_battlegate_zero_is_one_flag_backed_bridge_reward():
    rows = [row for row in normalize_locations(load_extracted_data())
            if row["source_id"] == "EVENT_REPORT_000"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Keyblade Graveyard - Battlegate 0"
    assert rows[0]["primary_item_name"] == "Potion"
    bridge = build_bridge_slot_data(rows, 7)
    reward, = bridge["locations"]
    assert reward["bridge_key"] == "Event:EVENT_REPORT_000"
    assert reward["bridge_delivery_required"] is True
    native_options = {"Event": {}}
    apply_event_override(native_options, reward, "ITEM_POTION")
    assert native_options == {"Event": {}}
    catalog = json.loads((Path(__file__).parents[1] / "data/event_flags.json").read_text())
    assert catalog["flags"]["EVENT_REPORT_000"] == {
        "flag_name": "global_BATTLEPORTAL_ex_39_CLEARED", "threshold": -1,
    }
