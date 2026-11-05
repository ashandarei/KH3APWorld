from __future__ import annotations

import json

from .. import extract_data


def test_json_source_catalog_reproduces_bundled_items_locations_and_runtime_flags():
    descriptions = extract_data.load_source_descriptions()
    defaults = extract_data.load_default_kh3()
    bundled = json.loads(extract_data.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert extract_data.build_items(defaults, descriptions["value_display_names"]) == bundled["items"]
    locations = extract_data.build_locations(
        defaults,
        descriptions["value_display_names"],
        descriptions["key_display_names"],
        descriptions["key_descriptions"],
        descriptions["chest_location_descriptions"],
    )
    assert locations == bundled["locations"]
    assert extract_data.build_runtime_flag_rows_by_token(defaults) == bundled["meta"]["runtime_flag_rows_by_token"]
