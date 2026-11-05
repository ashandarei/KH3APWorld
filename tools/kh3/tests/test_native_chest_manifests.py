"""Generated chest assignments consumed by the production worker and IPC."""

import json
import os
import random
import subprocess
from types import SimpleNamespace

import pytest

from tools.kh3.tests.test_pipe_transport import NATIVE, wait_until


@NATIVE
@pytest.mark.parametrize("ipc", ["named_pipe", "local_tcp"])
@pytest.mark.parametrize("conversion_count", [32, 128])
def test_generated_chests_follow_external_clear_and_server_release_facts(tmp_path, ipc, conversion_count):
    from worlds.kh3.Bridge import build_bridge_slot_data
    from worlds.kh3.client_support.mom_challenges import MoMChallengeService
    from worlds.kh3.Data import WORLD_LOCATION_POOL_OPTIONS, load_extracted_data, normalize_locations
    from worlds.kh3.KeybladeChests import KEYBLADE_CHEST_PALETTES, assign_keyblade_chests
    from worlds.kh3.MelodyOfMemory import select_music_chests
    from worlds.kh3.transport.pipe_transport import register, unregister

    rows = [row for row in normalize_locations(load_extracted_data()) if row["type"] == "treasure"]
    rng = random.Random(107)
    placed = select_music_chests(rng, rows, 8, conversion_count, 2)
    options = SimpleNamespace(
        chestsanity=SimpleNamespace(value=1),
        portal_unlocks=SimpleNamespace(value=0),
        **{name: SimpleNamespace(value=2) for name in WORLD_LOCATION_POOL_OPTIONS.values()},
    )
    assign_keyblade_chests(rng, rows, options)
    slot = {
        "chestsanity": 1, "portal_unlocks": 0,
        "kh3_seed": {"seed_name": "chest-ipc", "slot": 1},
        "bridge": build_bridge_slot_data(rows + placed, 7),
    }
    (tmp_path / "slot_data.json").write_text(json.dumps(slot), encoding="utf-8")
    command = [os.environ["KH3_PIPE_TEST_HOST"], str(tmp_path)]
    if ipc == "local_tcp":
        command.append("--tcp")
    native = subprocess.Popen(command, stdout=subprocess.DEVNULL)
    transport = register(tmp_path, transport=ipc).start()
    try:
        try:
            wait_until(lambda: transport.read("runtime_keyblade_chests.json") is not None)
        except AssertionError:
            pytest.fail(f"Native chest initialization: {transport.read('runtime_reconciliation_state.json')}")
        keys = transport.read("runtime_keyblade_chests.json")
        expected = {
            row["source_id"]: row
            for row in rows
            if row.get("keyblade_chest_keyblade") and not row["keyblade_chest_native"]
        }
        assert keys["owner"] == "dll" and keys["process_id"] == native.pid
        assert {row["treasure_id"] for row in keys["chests"]} == expected.keys()
        for row in keys["chests"]:
            assignment = expected[row["treasure_id"]]
            assert row["keyblade"] == assignment["keyblade_chest_token"]
            palette = KEYBLADE_CHEST_PALETTES[assignment["keyblade_chest_keyblade"]]
            assert [row[f"body_{axis}"] for axis in "rgb"] == palette["body"]

        def music():
            return (transport.read("runtime_music_chests.json") or {}).get("chests", [])

        expected_music_count = conversion_count + 8
        wait_until(lambda: len(music()) == expected_music_count)
        placed_ids = sorted(row["ap_location_id"] for row in music() if "placement_version" in row)
        converted_ids = {row["ap_location_id"] for row in music() if "placement_version" not in row}
        assert len(placed_ids) == 8 and len(converted_ids) == expected_music_count - 8
        assert not any(row["cleared"] for row in music())
        mom = MoMChallengeService(tmp_path, slot)
        mom.state["cleared"] = [placed_ids[0]]
        mom.publish_clears(tmp_path)  # No receipt catalog or loaded gameplay yet.
        wait_until(lambda: any(row["ap_location_id"] == placed_ids[0] and row["cleared"] for row in music()))
        transport.write(
            "runtime_receipt_catalog.json",
            {
                "schema": "kh3ap.receipt_catalog.v4",
                "process_id": native.pid,
                "seed_name": "chest-ipc",
                "slot": 1,
                "items": [],
                "server_checked_locations": sorted(converted_ids | set(placed_ids)),
            },
        )
        wait_until(lambda: all(row["cleared"] for row in music() if "placement_version" not in row))
        assert {row["ap_location_id"] for row in music() if "placement_version" in row and row["cleared"]} == {
            placed_ids[0]
        }
        mom.state["cleared"].append(placed_ids[1])
        mom.publish_clears(tmp_path)
        wait_until(lambda: any(row["ap_location_id"] == placed_ids[1] and row["cleared"] for row in music()))
    finally:
        unregister(transport)
        native.terminate()
        native.wait(timeout=5)
