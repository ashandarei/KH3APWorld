
from worlds.kh3.test.runtime_sources import native_project_root
"""Run the actual DLL generation core without Unreal or loose runtime files."""
import json
import subprocess
from pathlib import Path

import pytest

from ..Pak import PakReader, pack_directory
from ..RuntimeSeed import runtime_documents
from ..seedgen.remaps import add_runtime_settings
from .reference_remaps import boss_remap, enemy_remap

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "worlds/kh3/randomizer"
RECORD = "KINGDOM HEARTS III/Content/Archipelago/runtime_seed_settings.json"
PAK_NAME = "pakchunk9-NativeTest-randomizer-AP_123-1_99_P.pak"


@pytest.fixture(scope="module")
def native():
    native_project_root()
    from tools.kh3.native_randomization import harness_path
    exe = harness_path()
    if not exe.is_file():
        pytest.fail("Run KH3Runtime/scripts/build-runtime-harness.ps1 before native integration tests")
    def run(inputs):
        result = subprocess.run([exe], input="".join(json.dumps(row) + "\n" for row in inputs),
                                text=True, encoding="utf-8", capture_output=True, check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(rows) == len(inputs), result.stderr
        return rows
    return run


def settings(seed="AP_123-1-NativeTest", exceptions=None, blacklist=()):
    metadata = dict(seed=seed, slot=1, slot_name="NativeTest", slot_data={})
    add_runtime_settings(TEMPLATES, seed, {"exceptions": exceptions or {}, "boss_blacklist": blacklist}, metadata)
    return runtime_documents(metadata)["runtime_seed_settings.json"]


def test_native_matches_reference_and_is_independent_of_request_order(native):
    inputs, expected = [], []
    cases = [{}, {"Enemy Chaos": True}, {"Default Data Battles": True},
             {"Default End Game Battles": True, "Default Yozora Battle": True},
             {"Enemy Chaos": True, "Default Data Battles": True, "Default End Game Battles": True,
              "Default Yozora Battle": True}]
    for seed in ["PythonTest", "AP_12345-1-Sora", "世界 🗝", "max", *map(str, range(12))]:
        for exceptions in cases:
            for blacklist in ((), ("Yozora", "Data Ansem", "Demon Tide", "Marluxia")):
                inputs.append(settings(seed, exceptions, blacklist))
                expected.append(dict(enemy_runtime_remap=enemy_remap(TEMPLATES, seed, exceptions),
                                     boss_runtime_remap=boss_remap(TEMPLATES, seed, exceptions, blacklist)))
    actual = native(inputs)
    for result, reference in zip(actual, expected):
        assert "error" not in result, result
        for kind in ("enemy_runtime_remap", "boss_runtime_remap"):
            assert result[kind] == {key: reference[kind][key] for key in result[kind]}
    assert native(list(reversed(inputs[:8]))) == list(reversed(actual[:8]))
    assert len({json.dumps(row, sort_keys=True) for row in actual}) > 16


def test_generation_works_from_pak_without_loose_metadata(native, tmp_path):
    payload = tmp_path / "payload"
    record = payload / RECORD
    record.parent.mkdir(parents=True)
    value = settings(exceptions={"Enemy Chaos": True})
    record.write_text(json.dumps(value), encoding="utf-8")
    (payload / "compressed.bin").write_bytes(b"compressible" * 1000)
    pak = tmp_path / PAK_NAME
    pack_directory(payload, pak)
    reader = PakReader(pak)
    assert reader.entries[RECORD].codec == 0
    assert reader.entries["compressed.bin"].codec == 1
    record.unlink()
    assert native([{"pak": str(pak)}])[0] == native([value])[0]
    assert not value["enemy_randomization_initially_enabled"]
    assert not value["boss_randomization_initially_enabled"]
    assert not value["party_randomization_initially_enabled"]
    assert "enemy_runtime_remap" not in value and "boss_runtime_remap" not in value


def test_native_rejects_bad_config_and_impossible_pool(native):
    from ..BossCatalog import RANDOMIZABLE_BOSS_IDS
    invalid = []
    for key, value in (("version", 99), ("boss_blacklist", ["Unknown"]),
                       ("boss_blacklist", sorted(RANDOMIZABLE_BOSS_IDS)), ("enemy_chaos", None)):
        row = settings()
        row["native_randomization"][key] = value
        invalid.append(row)
    row = settings()
    row["seed"] = ""
    invalid.append(row)
    assert all("error" in row for row in native(invalid))
    with pytest.raises(ValueError, match="no compatible replacement"):
        settings(blacklist=RANDOMIZABLE_BOSS_IDS)


def test_full_seed_pak_drives_native_generation(native, tmp_path):
    from ..seedgen.generator import generate_seed
    from .test_python_seedgen import inputs
    config, items, metadata = inputs(TEMPLATES)
    # Generation needs a writable template staging directory, never the live install.
    from ..PakGenerator import prepare_randomizer_root
    templates = prepare_randomizer_root(tmp_path / "work")
    pak, _ = generate_seed(templates, tmp_path / "output", metadata["seed"], config, items, metadata)
    record = json.loads(PakReader(pak).read(RECORD))
    result = native([{"pak": str(pak)}])[0]
    assert "error" not in result, result
    assert result == native([record])[0]
    assert result["enemy_runtime_remap"]["entry_count"] > 4000
    assert result["boss_runtime_remap"]["boss_runtime_entry_count"] > 20
    assert record["party_randomization_initially_enabled"] is True


def test_pak_reader_rejects_damage_and_ambiguous_install(native, tmp_path):
    payload = tmp_path / "payload"
    record = payload / RECORD
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(settings()), encoding="utf-8")
    mods = tmp_path / "mods"
    mods.mkdir()
    pak = mods / PAK_NAME
    pack_directory(payload, pak)
    assert "error" not in native([{"mods": str(mods)}])[0]
    pristine = pak.read_bytes()
    entry = PakReader(pak).entries[RECORD]
    damaged = bytearray(pristine)
    damaged[entry.offset + entry.header_size + 10] ^= 1
    pak.write_bytes(damaged)
    assert "checksum mismatch" in native([{"pak": str(pak)}])[0]["error"]
    pak.write_bytes(pristine[:-5])
    assert "error" in native([{"pak": str(pak)}])[0]
    pak.write_bytes(pristine)
    (mods / PAK_NAME.replace("AP_123", "AP_456")).write_bytes(pristine)
    assert "Multiple" in native([{"mods": str(mods)}])[0]["error"]


def test_randomizer_services_have_no_loose_seed_metadata_dependency():
    core = native_project_root() / 'KH3APProbeCpp/kh3ap'
    for relative in ("features/enemies/enemy_remap_service.cpp", "features/party/party_member_service.cpp",
                     "core/settings/goa_settings_service.cpp"):
        source = (core / relative).read_text(encoding="utf-8")
        assert '"runtime_seed_settings.json"' not in source
        assert "installed_pak_seed_settings()" in source


def test_pak_catalog_preserves_multiworld_assignments_and_optional_hints(native, tmp_path):
    from .test_slot_data import TestCompactSlotData
    from ..RuntimeSeed import encode_document, slot_payload
    TestCompactSlotData.setUpClass()
    expected = slot_payload(TestCompactSlotData.runtime)
    payload = tmp_path / "catalog"
    directory = payload / "KINGDOM HEARTS III/Content/Archipelago"
    directory.mkdir(parents=True)
    hints = dict(schema="kh3ap.seed_hints.v1", seed_name=expected["kh3_seed"]["seed_name"],
                 slot=1, reports={"1": [dict(player=2, location=987654321)]})
    for name, value in {"slot_data.json": expected, "runtime_seed_settings.json": settings(),
                        "runtime_seed_hints.json": hints}.items():
        (directory / name).write_bytes(encode_document(name, value))
    pak = tmp_path / PAK_NAME
    pack_directory(payload, pak)
    reader = PakReader(pak)
    for name in ("slot_data.json", "runtime_seed_settings.json", "runtime_seed_hints.json"):
        assert reader.entries["KINGDOM HEARTS III/Content/Archipelago/" + name].codec == 0
        (directory / name).unlink()
    result = native([{"catalog_pak": str(pak)}])[0]
    assert result == dict(slot_data=expected, hints=hints)
    foreign = [row for row in result["slot_data"]["bridge"]["locations"] if row.get("is_foreign_item")]
    assert foreign and foreign[0]["assigned_item_id"] == 987654321
    assert foreign[0]["assigned_item_player"] == 2
    pristine = pak.read_bytes()
    entry = reader.entries["KINGDOM HEARTS III/Content/Archipelago/slot_data.json"]
    broken = bytearray(pristine)
    broken[entry.offset + entry.header_size + 5] ^= 1
    pak.write_bytes(broken)
    assert "checksum mismatch" in native([{"catalog_pak": str(pak)}])[0]["error"]
    entry = reader.entries["KINGDOM HEARTS III/Content/Archipelago/runtime_seed_hints.json"]
    broken = bytearray(pristine)
    broken[entry.offset + entry.header_size + 5] ^= 1
    pak.write_bytes(broken)
    assert native([{"catalog_pak": str(pak)}])[0] == dict(slot_data=expected, hints=None)
