import hashlib
import json
from pathlib import Path


def test_catalog_is_bound_to_packaged_primary_table():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "data/vbonus_flags.json").read_text())
    for extension, expected in catalog["source_sha256"].items():
        assert hashlib.sha256((root / (catalog["source"] + extension)).read_bytes()).hexdigest() == expected
    assert len(catalog["flags"]) == 556
