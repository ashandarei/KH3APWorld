from types import SimpleNamespace


def session(root):
    return SimpleNamespace(
        bridge_dir=root,
        slot_data={
            "kh3_seed": {"seed_name": "new-seed", "slot": 1},
            "bridge": {
                "locations": [
                    {
                        "source_id": str(i),
                        "source_table": "LuckyMark",
                        "bridge_key": f"LuckyMark:{i}",
                        "ap_location_name": f"Lucky Emblems - Milestone {i}",
                        "ap_location_id": 1263023257 + i,
                        "world": "Lucky Emblems",
                        "type": "lucky_emblem",
                    }
                    for i in [1, 33, 90]
                ]
            },
        },
        load_bridge_runtime_status=lambda: {"kh3_process_pid": 123},
    )


def test_native_checkpoint_is_not_owned_or_reset_by_client():
    from ..bridge_support.paths import SHARED_SIDECAR_FILENAMES
    from ..bridge_support.state import OWNED

    for collection in (OWNED, SHARED_SIDECAR_FILENAMES):
        assert "lucky_mark_progress.json" not in collection
        assert "lucky_emblem_progress.json" not in collection
