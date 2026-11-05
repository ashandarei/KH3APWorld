import hashlib
import json
from pathlib import Path


def test_event_catalog_matches_primary_asset_and_preserves_dependencies():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "data/event_flags.json").read_text())
    for extension, digest in catalog["source_sha256"].items():
        assert hashlib.sha256((root / (catalog["source"] + extension)).read_bytes()).hexdigest() == digest
    flags = catalog["flags"]
    assert flags["EVENT_KEYBLADE_002"]["requires_tt_clear"]
    assert flags["EVENT_CKGAME_001"]["requires_tt_clear"]
    assert flags["EVENT_KEYBLADE_011"]["flag_name"] == "gameflow_VBONUS_070_kg_002"
    assert flags["EVENT_007"]["threshold"] == 600
    assert flags["EVENT_REPORT_000"] == {
        "flag_name": "global_BATTLEPORTAL_ex_39_CLEARED", "threshold": -1,
    }
