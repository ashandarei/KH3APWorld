"""Race player files build without the server's unrevealed item assignments."""

import json
import zlib
import zipfile

import pytest

import Utils
from BaseClasses import Item, ItemClassification, Location, Region
from NetUtils import NetworkItem, NetworkSlot, SlotType
from test.general import TestWorld as ForeignWorld, setup_multiworld

from .. import KH3_CONTAINER_PAYLOAD, KH3World, PakGenerator
from ..Pak import PakReader
from ..RaceMode import RACE_ITEM_NAME
from ..SlotData import ASSIGNMENT_FIELDS, decode_slot_data
from ..client_support.item_sync_service import ItemSyncInput
from ..client_support.seed_import_service import KH3SeedImportService
from ..seedgen.generator import METADATA_PATH, generate_seed
from . import test_client_status as sync_fixture
from . import test_python_seedgen as generator_fixture
from . import test_universal_tracker as tracker_fixture
from .test_python_seedgen import templates as templates


@pytest.fixture
def room():
    mw = setup_multiworld([KH3World, KH3World, ForeignWorld], seed=610065002, options={
        "include_space": 1, "synthesis_pool": "randomize", "photo_missions_pool": "randomize",
        "keyblade_upgrade_rewards_pool": "randomize", "olympus_coliseum_pool": "randomize",
        "adversary_checks": 1, "ingredients": 1, "music_chest_count": 1,
    })
    mw.seed_name = "610065002"
    for player in (1, 2):
        for index, location in enumerate(mw.get_unfilled_locations(player)):
            location.item = (mw.worlds[player].create_item("Potion") if index % 2 else
                             Item("RACE_SECRET_MARKER", ItemClassification.progression, 987654321, 3))
    region = Region("Foreign", 3, mw)
    mw.regions.append(region)
    remote = Location(3, "Remote race check", 900001, region)
    remote.item = Item("REMOTE_SECRET_MARKER", ItemClassification.progression, 987654322, 1)
    region.locations.append(remote)
    return mw


def race_data(room):
    room.is_race = True
    KH3World.stage_pre_output(room)
    return room.worlds[1].build_runtime_slot_data()


def assert_private(data):
    assert data["race_mode"] is True
    assert data["kh3_seed"].get("remote_hint_placements", []) == []
    assert data["kh3_randomizer"]["hint"] == {"type": "None", "checks": []}
    for row in data["bridge"]["locations"]:
        assert not set(ASSIGNMENT_FIELDS).intersection(row)
        assert row["bridge_delivery_required"] is True
    encoded = json.dumps(data)
    assert "RACE_SECRET_MARKER" not in encoded
    assert "REMOTE_SECRET_MARKER" not in encoded


def test_race_server_and_container_data_hide_rewards_without_changing_fill(room, tmp_path):
    world = room.worlds[1]
    normal = world.build_runtime_slot_data()
    assert "RACE_SECRET_MARKER" in json.dumps(normal)
    assert "REMOTE_SECRET_MARKER" in json.dumps(normal)
    placements = [(location, location.item) for location in room.get_locations()]
    runtime = race_data(room)
    assert_private(runtime)
    assert_private(decode_slot_data(world.fill_slot_data()))
    assert all(location.item is item for location, item in placements)
    assert [row["ap_location_id"] for row in runtime["bridge"]["locations"]] == [
        row["ap_location_id"] for row in normal["bridge"]["locations"]]
    world.generate_output(str(tmp_path))
    with zipfile.ZipFile(next(tmp_path.glob("*.apkh3"))) as archive:
        payload = json.loads(archive.read(KH3_CONTAINER_PAYLOAD))
    assert payload["remote_hint_placements"] == []
    assert_private(decode_slot_data(payload["slot_data"]))
    assert "RACE_SECRET_MARKER" not in json.dumps(payload)


@pytest.mark.parametrize("source", ["container", "connected", "archive"])
def test_race_player_pak_generation_has_no_placements_or_spoilers(room, tmp_path, source):
    runtime = race_data(room)
    world = room.worlds[1]
    output = tmp_path / "player"
    output.mkdir()
    # A reused work/output directory must not retain this seed's old spoilers.
    pak_seed = world.build_seed_container_data()["pak_seed"]
    if source == "archive":
        pak_seed = f"AP_610065002-1-{room.get_player_name(1)}"
    for directory in (output, output / "work"):
        directory.mkdir(exist_ok=True)
        (directory / f"SpoilerLog-{pak_seed}.json").write_text("RACE_SECRET_MARKER")
        (directory / f"{pak_seed}.generated-metadata.json").write_text("RACE_SECRET_MARKER")
    if source == "container":
        world.generate_output(str(tmp_path))
        result = PakGenerator.generate_kh3_pak_from_container(next(tmp_path.glob("*.apkh3")), output)
    elif source == "connected":
        payload = KH3SeedImportService._container_data_from_server_slot_data(
            runtime, expected_slot=1, expected_seed_name=room.seed_name)
        container = tmp_path / "connected.apkh3"
        with zipfile.ZipFile(container, "w") as archive:
            archive.writestr("archipelago.json", json.dumps({"game": world.game}))
            archive.writestr(KH3_CONTAINER_PAYLOAD, json.dumps(payload))
        result = PakGenerator.generate_kh3_pak_from_container(container, output)
    else:
        # Full server archives deliberately contain authoritative placements.
        # Import must use the race slot data without consulting those assignments.
        multidata = {
            "race_mode": 1, "slot_data": {1: world.fill_slot_data()},
            "datapackage": {},
            "slot_info": {1: NetworkSlot(room.get_player_name(1), world.game, SlotType.player)},
            "locations": {1: {row["ap_location_id"]: (987654321, 3, 1)
                              for row in runtime["bridge"]["locations"]}},
            "private_test_marker": "RACE_SECRET_MARKER",
        }
        source_zip = tmp_path / "AP_610065002.zip"
        with zipfile.ZipFile(source_zip, "w") as archive:
            archive.writestr("AP_610065002.archipelago", b"\x03" + zlib.compress(Utils.restricted_dumps(multidata)))
        result, = PakGenerator.generate_kh3_paks_from_ap_zip(source_zip, output, requested_slot=1)
    assert result.pak_path.is_file()
    assert result.spoiler_log_path is None
    assert not list(output.rglob("SpoilerLog*"))
    assert not list(output.rglob("*.generated-metadata.json"))
    pak = PakReader(result.pak_path)
    assert "SpoilerLog.json" not in pak.entries
    runtime_metadata = json.loads(pak.read(METADATA_PATH))
    from ..RuntimeSeed import expand_document
    assert_private(expand_document("kh3_ap_seed_metadata.json", runtime_metadata)["slot_data"])
    assert "remote_hint_placements" not in runtime_metadata["slot_data"]["kh3_seed"]
    assert all(not targets for targets in runtime_metadata["secret_report_hints"]["reports"].values())
    for file in output.rglob("*.json"):
        assert b"RACE_SECRET_MARKER" not in file.read_bytes(), file
        assert b"REMOTE_SECRET_MARKER" not in file.read_bytes(), file
    for name in pak.entries:
        payload = pak.read(name)
        assert b"RACE_SECRET_MARKER" not in payload, name
        assert b"REMOTE_SECRET_MARKER" not in payload, name
    summary = json.loads(next(output.rglob("*.placement-summary.json")).read_bytes())
    assert summary["hint_locations"] == []
    assert {row["item_name"] for row in summary["locations"]} == {RACE_ITEM_NAME}
    assert {row["recipient_slot"] for row in summary["locations"]} == {0}
    assert {row["runtime_token"] for row in summary["locations"]} == {"AP_ITEM_000"}
    for row in summary["locations"]:
        if row["location_type"] in {"event", "vbonus"}:
            assert row["native_reward_token"] == "ETresVictoryBonusKind::NONE"


def test_race_received_items_still_resolve_real_rewards_and_local_source(room):
    slot_data = race_data(room)
    row = next(r for r in slot_data["bridge"]["locations"] if r["type"] == "treasure")
    item_id = room.worlds[1].item_name_to_id["Heart Piece"]
    receipt = NetworkItem(item_id, row["ap_location_id"], 1, 1)
    source = ItemSyncInput(
        received_items=[receipt], game=KH3World.game, player_names={1: "Sora"},
        location_id_to_bridge_key={row["ap_location_id"]: row["bridge_key"]}, slot_data=slot_data,
        bridge_save_dir=None, lookup_item_name=lambda *_: "Heart Piece",
        lookup_location_name=lambda *_: row["ap_location_name"],
    )
    service = sync_fixture.TestItemSyncService.build_service()
    payload = service.build_items_payload(source)
    assert payload["items"][0]["item_name"] == "Heart Piece"
    assert payload["items"][0]["bridge_key"] == row["bridge_key"]
    assert row["bridge_delivery_required"] is True


def test_race_universal_tracker_reconstructs_checks_without_placements(room):
    runtime = race_data(room)
    restored = tracker_fixture.TestKH3UniversalTracker().build_yamlless_tracker_multiworld(runtime)
    world = restored.worlds[1]
    regenerated = world.build_runtime_slot_data()
    assert_private(regenerated)
    assert {row["ap_location_id"] for row in regenerated["bridge"]["locations"]} == {
        row["ap_location_id"] for row in runtime["bridge"]["locations"]}


def test_race_generator_rejects_ordinary_placement_metadata(templates, tmp_path):
    config, items, metadata = generator_fixture.inputs(templates)
    metadata["slot_data"]["race_mode"] = True
    with pytest.raises(ValueError, match="race generation requires generic AP rewards"):
        generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    assert not list(tmp_path.iterdir())


def test_race_hints_stay_disabled_even_if_configuration_is_verbose(templates):
    config, _, metadata = generator_fixture.inputs(templates)
    metadata["slot_data"]["race_mode"] = True
    hints = generator_fixture.make_hints(metadata["seed"], config, metadata)
    assert all(not text for values in hints.values() for text in values)
    assert all(not targets for targets in metadata["secret_report_hints"]["reports"].values())
