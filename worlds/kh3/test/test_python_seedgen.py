from __future__ import annotations

import copy
import importlib
import json
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from .. import PakGenerator
from ..Pak import PakReader
from ..seedgen.assets import Asset, AssetError, Reader, fstring, get, name_hash
from ..seedgen.generator import FINGERPRINT_PATH, METADATA_PATH, generate_seed
from ..seedgen.localization import Locres, make_hints
from ..seedgen.presentation import text_ids
from .reference_remaps import PINNED_BOSSES, boss_compatible, boss_remap
from ..seedgen.rng import SeedRandom
from ..seedgen.tables import PATHS, prefix, randomize_options


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    return PakGenerator.prepare_randomizer_root(tmp_path_factory.mktemp("python-kh3"))


def inputs(root, *, stylesanity=0, keyblade_levels=0, hint="Verbose", ultima=0):
    items = json.loads((root / "wwwroot/DefaultKH3.json").read_text(encoding="utf-8-sig"))
    for table, rows in items.items():
        if table.startswith("Treasure"):
            for row in rows.values():
                for field in row:
                    row[field] = "ETresVictoryBonusKind::NONE"
    camp = "ITEM_AP_I02061_FOREIGN"
    shop = "ITEM_AP_I07001_SHOP"
    items["LuckyMark"] = {"1": {"Reward": camp}, "90": {"Reward": "KEY_ITEM14"}}
    items["Event"]["EVENT_001"]["RandomizedItem"] = "AP_ITEM_12345"
    slots = dict(
        stylesanity=stylesanity, keyblade_levels=keyblade_levels, ultima_weapon_mode=ultima, bridge={"locations": []}
    )
    locations = [
        dict(
            ap_location_id=101,
            location_player_slot=1,
            recipient_slot=2,
            source_table="LuckyMark",
            source_id="1",
            location_type="lucky_emblem",
            export_token=camp,
            item_name="Hookshot",
            item_display_name="Riku's Hookshot 🗝",
            is_foreign_item=True,
            world="Lucky Emblems",
        ),
        dict(
            ap_location_id=102,
            location_player_slot=1,
            recipient_slot=1,
            source_table="Shop",
            source_id="1",
            location_type="shop",
            export_token=shop,
            shop_item_token=shop,
            shop_price=321,
            item_classification=3,
            item_name="Proof of Fantasy",
            item_display_name="Sora's Proof of Fantasy",
            item_game="Kingdom Hearts II",
            world="Moogle Workshop",
        ),
        dict(
            ap_location_id=103,
            location_player_slot=2,
            recipient_slot=1,
            source_table="Remote",
            source_id="103",
            location_type="remote",
            export_token="",
            item_name="Secret Report 1",
            is_remote_location=True,
            world="Hyrule",
            location_game="A Link to the Past",
            location_player_name="Riku",
            location_description="Eastern Palace",
        ),
    ]
    metadata = dict(
        format="kh3_ap_seed_metadata",
        format_version=1,
        seed="AP_171313-1-PythonTest",
        slot=1,
        slot_name="PythonTest",
        slot_data=slots,
        foreign_item_manifest={},
        placement_summary=dict(kh3_slot=1, locations=locations),
    )
    config = dict(
        skip_world_intro_cutscenes=False,
        pools={
            "Enemies": "Randomize",
            "Bosses": "Randomize",
            "Party Members": "Randomize",
            "Equipment Abilities": "Randomize",
            "Fullcourse Abilities": "Replace",
            "Base Sora Stats": "Balanced",
            "Level Up Stats": "Boosted",
            "Equipment Stats": "Balanced",
            "Keyblade Enhance Stats": "Boosted",
            "Food Effect Stats": "Balanced",
        },
        exceptions={"Enemy Chaos": True, "Party Chaos": True, "Allow All Stats": True},
        hint={"type": hint, "checks": ["Proof of Fantasy", "Secret Report 1"]},
        clear_condition={"conditions": {"KEY_ITEM17\0": 7}},
    )
    return config, items, metadata


def test_native_reward_bundles_preserve_secondary_rewards_without_ap_markers():
    from ..seedgen.generator import runtime_reward_bundles
    items = {"VBonus": {"VB001": {"Ability1": "ETresAbilityKind::AIR_RECOVERY\0",
                                  "Bonus1": "AP_ITEM_000", "Bonus2": "ITEM_POTION"}},
             "Event": {"FOREIGN": {"Reward": "ITEM_AP_I02061_FOREIGN"}}}
    defaults = {"VBonus": {"VB001": {"Bonus1": "ETresVictoryBonusKind::HP_UP"}}}
    slot = {"bridge": {"locations": [
        {"source_table": "VBonus", "source_id": "VB001", "bridge_key": "VBonus:VB001"},
        {"source_table": "Event", "source_id": "FOREIGN", "bridge_key": "Event:FOREIGN"}]}}
    bundles = runtime_reward_bundles(items, defaults, slot)
    assert bundles[0]["randomized_tokens"] == ["ETresAbilityKind::AIR_RECOVERY", "ITEM_POTION"]
    assert bundles[0]["vanilla_tokens"] == ["ETresVictoryBonusKind::HP_UP"]
    assert bundles[1]["randomized_tokens"] == []


def test_website_cli_generates_pak_with_resource_fingerprint(templates, tmp_path):
    config, items, metadata = inputs(templates)
    metadata["slot_data"].update(protocol_name="kh3_ap_file_bridge", protocol_version=1)
    metadata["slot_data"]["bridge"].update(protocol_name="kh3_ap_file_bridge", protocol_version=1)
    script = Path(__file__).resolve().parents[3] / "tools/kh3/randomizer.py"
    command = [sys.executable, str(script), "exact", "--seed", metadata["seed"],
               "--randomizer-root", str(templates), "--output", str(tmp_path / "out")]
    for option, payload in (("config", config), ("randomized-options", items),
                            ("archipelago-metadata", metadata)):
        path = tmp_path / f"{option}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        command.extend(["--" + option, str(path)])
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(command, cwd=templates, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    pak = Path(json.loads(result.stdout)["pak"])
    fingerprint = json.loads(PakReader(pak).read(FINGERPRINT_PATH))
    assert fingerprint["implementation"] == "python"
    assert len(fingerprint["source_sha256"]) == 64


@pytest.mark.parametrize("table", list(PATHS))
def test_cooked_templates_roundtrip_without_losing_opaque_properties(templates, table):
    path = templates / (PATHS[table] + ".uasset")
    asset = Asset.load(path)
    primary, sidecar = asset.encode()
    assert primary == path.read_bytes()
    assert sidecar == path.with_suffix(".uexp").read_bytes()


def test_asset_rejects_truncation_and_rebases_new_names_and_nested_rows(templates):
    path = templates / (PATHS["Completion"] + ".uasset")
    primary, sidecar = path.read_bytes(), path.with_suffix(".uexp").read_bytes()
    with pytest.raises(AssetError):
        Asset(primary[:32], sidecar)
    asset = Asset(primary, sidecar)
    original = copy.deepcopy(asset.rows["CompletionItems_001"])
    row = copy.deepcopy(original)
    values = prefix(row, "CompletionItems").value
    prefix(values[0], "Name").set("世界 🗝")
    prefix(values[0], "Amount").set(42)
    asset.rows["NewCompletionRow"] = row
    rebuilt = Asset(*asset.encode())
    assert len(rebuilt.names) > len(Asset(primary, sidecar).names)
    assert prefix(prefix(rebuilt.rows["NewCompletionRow"], "CompletionItems").value[0], "Name").value == "世界 🗝"
    assert prefix(prefix(rebuilt.rows["CompletionItems_001"], "CompletionItems").value[0], "Amount").value == 1


def test_new_name_hashes_match_existing_unreal_name_map(templates):
    asset = Asset.load(templates / (PATHS["Items"] + ".uasset"))
    # Validate both packed CRC halves against the independent cooked name map.
    for value, raw in zip(asset.names, asset.name_bytes):
        if value.isascii() and value:
            assert name_hash(value) == struct.unpack("<I", raw[-4:])[0]
    assert Reader(fstring("Sora 世界 🗝")).string() == "Sora 世界 🗝"


@pytest.mark.parametrize(
    "seed,expected",
    [
        ("AP_171313-1-PythonTest:equipment-abilities", [717928081, 58962694, 649237229, 615597923, 307370705]),
        ("AP_12345-1-Sora", [1139852226, 1836968947, 1628048922, 951571298, 539349940]),
        ("世界 🗝", [2135445100, 923202921, 1016294587, 1299352967, 1781633994]),
        ("PythonTest", [912608325, 1426733188, 1537994879, 1733186225, 1158812019]),
    ],
)
def test_seed_rng_matches_independent_dotnet_reference_including_int32_overflow(seed, expected):
    rng = SeedRandom(seed)
    assert [rng.next() for _ in expected] == expected


def test_category_hints_cover_all_thirteen_reports_in_one_remote_world_without_self_hints():
    locations = [
        dict(
            ap_location_id=n,
            recipient_slot=1,
            location_player_slot=2,
            item_name=f"Secret Report {n}",
            source_table="Remote",
            source_id=str(n),
            is_remote_location=True,
            world="Hyrule",
            location_game="A Link to the Past",
            location_player_name="Riku",
            location_description=f"Check {n}",
        )
        for n in range(1, 14)
    ]
    metadata = dict(slot=1, placement_summary={"locations": locations})
    hints = make_hints("all-reports", {"hint": {"type": "Category"}}, metadata)
    targets = metadata["secret_report_hints"]["reports"]
    assert {p["location"] for records in targets.values() for p in records} == set(range(1, 14))
    for report, records in targets.items():
        assert all(p["item_name"] != f"Secret Report {report}" for p in records)
    assert all("13 important checks" in text for texts in hints.values() for text in texts if text)


def test_invalid_generation_preserves_an_existing_output_pak(templates, tmp_path):
    config, items, metadata = inputs(templates)
    first, _ = generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    previous = first.read_bytes()
    items["Event"]["EVENT_001"]["RandomizedItem"] = "invalid-token"
    metadata["slot_data"]["stylesanity"] = 99
    with pytest.raises(ValueError, match="Stylesanity"):
        generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    assert first.read_bytes() == previous


@pytest.mark.parametrize("heart_count", [3, 9, 30])
def test_generated_heart_piece_capacity_supports_goal(templates, tmp_path, heart_count):
    config, items, metadata = inputs(templates)
    config["clear_condition"]["conditions"] = {"KEY_ITEM17\0": heart_count}
    metadata["slot_data"]["heart_piece_count"] = heart_count
    pak, _ = generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    archive = PakReader(pak)
    base = "KINGDOM HEARTS III/" + PATHS["Items"]
    inventory = Asset(archive.read(base + ".uasset"), archive.read(base + ".uexp"))
    hearts = [row for row in inventory.rows.values() if get(row, "m_KeyName").value == "KEY_ITEM17"]
    assert len(hearts) == 1
    assert get(hearts[0], "m_MaxNum").value >= heart_count
    base = "KINGDOM HEARTS III/" + PATHS["Completion"]
    completion = Asset(archive.read(base + ".uasset"), archive.read(base + ".uexp"))
    conditions = prefix(completion.rows["CompletionItems_001"], "CompletionItems").value
    assert len(conditions) == 1
    assert prefix(conditions[0], "Name").value == "KEY_ITEM17"
    assert prefix(conditions[0], "Amount").value == heart_count


def test_equipment_edits_preserve_numbered_weapon_keys_and_unselected_resistances(templates, tmp_path):
    config, items, metadata = inputs(templates)
    config["pools"]["Equipment Abilities"] = "Replace"
    generated, _ = generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    reader = PakReader(generated)
    path = "KINGDOM HEARTS III/" + PATHS["EquipItem"]
    equipment = Asset(reader.read(path + ".uasset"), reader.read(path + ".uexp"))
    assert get(equipment.rows["I03011"], "m_KeyName").value == "WEP_KEYBLADE_SO_10"
    assert get(equipment.rows["I03031"], "m_KeyName").value == "WEP_DONALD_10"
    assert get(equipment.rows["I03051"], "m_KeyName").value == "WEP_GOOFY_10"
    assert get(equipment.rows["I03045"], "m_AttrResistPhysical").value == 0
    assert get(equipment.rows["I03045"], "m_AttrResistThunder").value == 5


def test_passive_expectations_match_generated_stat_rolls_and_ability_shuffle(templates, tmp_path):
    from tools.kh3.export_seed_expectations import read_expectations

    config, items, metadata = inputs(templates)
    defaults = json.loads((templates / "wwwroot/DefaultKH3.json").read_text(encoding="utf-8-sig"))
    expected = copy.deepcopy(items)
    randomize_options(metadata["seed"], expected, config, defaults)
    pak, _ = generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    equipment, stats = read_expectations(pak)
    assert equipment["seed"] == metadata["seed"]
    assert equipment["abilities"] == {
        row: {field: token.rstrip("\0") for field, token in fields.items()}
        for row, fields in expected["EquipItem"].items()
    }
    for category, rows in stats.items():
        assert rows == {
            row: {field: int(value) for field, value in fields.items()} for row, fields in expected[category].items()
        }


@pytest.mark.parametrize(
    "stylesanity,keyblade_levels,ultima,hint",
    [(0, 0, 0, "Verbose"), (1, 1, 1, "Vague: By Category"), (0, 2, 4, "None")],
)
def test_complete_generation_runs_in_frozen_client_without_processes_or_downloads(
    templates, tmp_path, monkeypatch, stylesanity, keyblade_levels, ultima, hint
):
    config, items, metadata = inputs(
        templates, stylesanity=stylesanity, keyblade_levels=keyblade_levels, ultima=ultima, hint=hint
    )
    config.update(exp_multiplier=(0.5, 5.0, 20.0)[keyblade_levels],
                  battle_level_sync=bool(stylesanity))
    metadata["slot_data"].update(
        kh3_randomizer=copy.deepcopy(config), color_randomization=keyblade_levels,
        music_shuffle=keyblade_levels + 1, include_custom_music=stylesanity,
        form_themes=stylesanity,
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(PakGenerator.subprocess, "run", lambda *a, **kw: pytest.fail("external process launched"))
    monkeypatch.setattr(PakGenerator.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("network requested"))
    pak, spoiler = generate_seed(templates, tmp_path, metadata["seed"], config, items, metadata)
    archive = PakReader(pak)
    assert pak.name == "pakchunk9-PythonTest-randomizer-AP_171313-1_99_P.pak"
    assert spoiler.is_file()
    runtime = json.loads(archive.read(METADATA_PATH))
    assert "placement_summary" not in runtime
    from ..RuntimeSeed import runtime_documents

    for filename, document in runtime_documents(runtime).items():
        assert json.loads(archive.read("KINGDOM HEARTS III/Content/Archipelago/" + filename)) == document
        assert archive.entries["KINGDOM HEARTS III/Content/Archipelago/" + filename].codec == 0
    settings = json.loads(archive.read("KINGDOM HEARTS III/Content/Archipelago/runtime_seed_settings.json"))
    assert settings["slot_data"]["kh3_randomizer"]["exp_multiplier"] == config["exp_multiplier"]
    assert settings["slot_data"]["kh3_randomizer"]["battle_level_sync"] == bool(stylesanity)
    assert settings["party_randomization_initially_enabled"] is True
    assert settings["party_randomize_on_room_change"] is True
    for option in ("color_randomization", "music_shuffle", "include_custom_music", "form_themes"):
        assert settings["slot_data"][option] == metadata["slot_data"][option]
    full_metadata = json.loads((tmp_path / f"{metadata['seed']}.generated-metadata.json").read_bytes())
    assert full_metadata["placement_summary"] == metadata["placement_summary"]
    assert runtime["randomizer_backend"] == json.loads(archive.read(FINGERPRINT_PATH))
    assert runtime["randomizer_backend"]["implementation"] == "python"
    assert "enemy_runtime_remap" not in runtime
    assert "boss_runtime_remap" not in runtime
    assert settings["native_randomization"]["version"] == 1
    assert archive.entries["KINGDOM HEARTS III/Content/Archipelago/runtime_seed_settings.json"].codec == 0
    assert runtime["party_randomize_on_room_change"] is True
    for table in PATHS:
        base = "KINGDOM HEARTS III/" + PATHS[table]
        if base + ".uasset" in archive.entries:
            asset = Asset(archive.read(base + ".uasset"), archive.read(base + ".uexp"))
            if table == "ChrInit":
                abilities = get(get(asset.exports[-1].properties, "m_PlayerSora").value, "m_EquipAbility").value
                assert ("ETresAbilityKind::AP_STYLE_CHANGE" in abilities) == (stylesanity == 0)
                assert len(abilities) == (17 if stylesanity == 0 else 16)
            elif table == "WeaponEnhance":
                assert len(asset.rows) == 170
                assert all(get(row, "m_bInitAchieved").value == (keyblade_levels == 0) for row in asset.rows.values())
            elif table == "SynthesisItem":
                assert "IS_58" not in asset.rows
                assert get(asset.rows["IS_59"], "m_FlagIndex").value == 59
            elif table == "LuckyMark":
                assert list(asset.rows) == ["1", "90"]
                assert get(asset.rows["90"], "CompleteRewardFlag").value is True
            elif table == "Completion":
                conditions = prefix(asset.rows["CompletionItems_001"], "CompletionItems").value
                assert len(conditions) == 1
                assert prefix(conditions[0], "Name").value == "KEY_ITEM17"
                assert prefix(conditions[0], "Amount").value == 7
            elif table == "WorldFlags":
                flags = {prefix(row, "FlagName").value: prefix(row, "FlagValue").value for row in asset.rows.values()}
                assert flags["Gameflow_HE"] == 0 and flags["Gameflow_EW"] == 2000
                assert set(flags.values()) == {0, 2000}
            elif table == "Items":
                shop = asset.rows["I07001"]
                assert get(shop, "m_BuyPrice").value == 321
                assert get(shop, "m_IconAsset").value.endswith("ap_kh2.ap_kh2")
                camp = asset.rows["I02061"]
                assert get(camp, "m_KeyName").value == "ITEM_AP_I02061_FOREIGN"
                assert "ITEM_AP_I02061_FOREIGN" in asset.rows
                shard = next(row for row in asset.rows.values() if get(row, "m_KeyName").value == "MAT_ITEM57")
                assert (get(shard, "m_Comment").value == "Ultima Weapon Shard") == (ultima == 0)
    for name in ("kh3_item", "kh3_mobile"):
        locres = Locres(archive.read(f"KINGDOM HEARTS III/Content/Localization/Game/en/{name}.locres"))
        name_id, _ = text_ids("ITEM_AP_I02061_FOREIGN")
        key = name_id.removeprefix("kh3_item.battle.")
        assert locres.strings[locres.namespaces["kh3_item.battle"][key][1]] == "Riku's Hookshot 🗝"
    assert metadata["placement_summary"]  # Caller input is unchanged.


def test_hints_never_hint_their_own_report_and_include_remote_targets(templates):
    config, _, metadata = inputs(templates)
    values = make_hints(metadata["seed"], config, metadata)
    assert not any("Secret Report 1 is" in text for text in values["SecretReport02"])
    targets = [p for sources in metadata["secret_report_hints"]["reports"].values() for p in sources]
    assert any(p["player"] == 2 and p["location"] == 103 for p in targets)
    assert any("Riku's A Link to the Past world" in text for hints in values.values() for text in hints)


def boss_replacements(templates, result):
    originals = {boss: pawn.rstrip("\0") for boss, pawn in
                 json.loads((templates / "wwwroot/DefaultBosses.json").read_text()).items()}
    replacements = originals.copy()
    for entry in result["boss_runtime_entries"]:
        boss, _, replacement = entry.split("|")
        replacements[boss] = result["boss_runtime_classes"][int(replacement)].split("/")[-1].split(".")[0]
    return originals, replacements


def test_blacklist_names_cover_catalog_and_variants(templates):
    from ..BossCatalog import RANDOMIZABLE_BOSS_IDS, resolve_boss_blacklist
    originals, _ = boss_replacements(templates, boss_remap(templates, "catalog", {}))
    assert RANDOMIZABLE_BOSS_IDS == set(originals) - PINNED_BOSSES
    assert resolve_boss_blacklist(["Demon Tide"]) == {"BOSS_005", "BOSS_020"}
    assert resolve_boss_blacklist(["Marluxia"]) == {"BOSS_024", "BOSS_066"}
    assert resolve_boss_blacklist(["Data Marluxia"]) == {"BOSS_099"}
    with pytest.raises(ValueError, match="Unknown boss_blacklist"):
        boss_remap(templates, "bad-name", {}, ["Typo Boss"])


@pytest.mark.parametrize("exceptions", [{}, {"Default Data Battles": True},
                                         {"Default End Game Battles": True, "Default Yozora Battle": True}])
def test_blacklist_excludes_targets_preserves_pins_and_arena_rules(templates, exceptions):
    from ..BossCatalog import resolve_boss_blacklist
    blacklist = ["Yozora", "Data Ansem", "Demon Tide", "Marluxia"]
    blocked = resolve_boss_blacklist(blacklist)
    for seed in range(100):
        result = boss_remap(templates, f"Blacklist-{seed}", exceptions, blacklist)
        originals, replacements = boss_replacements(templates, result)
        blocked_pawns = {originals[boss] for boss in blocked}
        pinned = set(PINNED_BOSSES)
        if exceptions.get("Default Data Battles"):
            pinned.update(b for b in originals if 93 <= int(b[5:]) <= 105)
        if exceptions.get("Default End Game Battles"):
            pinned.update(b for b in originals if int(b[5:]) in (54, 55, 56, 57, 58, 77))
        if exceptions.get("Default Yozora Battle"):
            pinned.add("BOSS_0106")
        assert all(replacements[b] == originals[b] for b in pinned)
        assert all(p not in blocked_pawns for b, p in replacements.items() if b not in pinned)
        assert all(boss_compatible(b, p) for b, p in replacements.items())
        assert result == boss_remap(templates, f"Blacklist-{seed}", exceptions, reversed(blacklist))


def test_blacklist_allows_duplicates_and_rejects_impossible_pool(templates):
    from ..BossCatalog import RANDOMIZABLE_BOSS_IDS
    blacklist = RANDOMIZABLE_BOSS_IDS - {"BOSS_005", "BOSS_020"}
    originals, replacements = boss_replacements(templates, boss_remap(templates, "duplicates", {}, blacklist))
    assert set(replacements[b] for b in RANDOMIZABLE_BOSS_IDS) <= {originals["BOSS_005"], originals["BOSS_020"]}
    assert len(set(replacements.values())) < len(replacements)
    for blocked in (RANDOMIZABLE_BOSS_IDS, RANDOMIZABLE_BOSS_IDS - {"BOSS_008"}):
        with pytest.raises(ValueError, match="boss_blacklist leaves no compatible replacement"):
            boss_remap(templates, "impossible", {}, blocked)


def test_blacklist_config_reaches_generated_runtime_settings(templates):
    from ..seedgen.remaps import add_runtime_settings
    config, _, metadata = inputs(templates)
    config["boss_blacklist"] = ["Yozora"]
    add_runtime_settings(templates, metadata["seed"], config, metadata)
    assert metadata["native_randomization"]["boss_blacklist"] == ["BOSS_0106"]
    assert "enemy_runtime_remap" not in metadata
    assert "boss_runtime_remap" not in metadata


def test_boss_shuffles_preserve_pins_and_bidirectional_arena_restrictions(templates):
    for seed in ("PythonTest", "AP_12345-1-Sora", "世界 🗝", "max"):
        result = boss_remap(templates, seed, {})
        for entry in result["boss_runtime_entries"]:
            boss, _, replacement = entry.split("|")
            assert boss not in PINNED_BOSSES
            pawn = result["boss_runtime_classes"][int(replacement)].split("/")[-1].split(".")[0]
            assert boss_compatible(boss, pawn)


@pytest.mark.parametrize("pawn", ["e_ex352_Pawn", "e_ex357_Pawn"])
def test_monstropolis_excludes_only_reviewed_data_boss_pairs(pawn):
    assert not boss_compatible("BOSS_010", pawn)
    assert not boss_compatible("BOSS_011", pawn)
    assert boss_compatible("BOSS_021", pawn)
    assert boss_compatible("BOSS_010", "e_ex316_Pawn")  # Story Ansem
    assert boss_compatible("BOSS_010", "e_ex308_Pawn")  # Story Marluxia
    assert boss_compatible("BOSS_010", "e_ex308_Pawn_s10")  # ReMind Marluxia


def test_twilight_town_and_corona_exclude_only_data_xemnas():
    assert not boss_compatible("BOSS_005", "e_ex353_Pawn")
    assert boss_compatible("BOSS_005", "e_ex309_Pawn")  # Story Larxene
    assert boss_compatible("BOSS_005", "e_ex309_Pawn_s10")
    assert boss_compatible("BOSS_021", "e_ex353_Pawn")
    assert boss_compatible("BOSS_006", "e_ex353_Pawn")
    assert boss_compatible("BOSS_024", "e_ex353_Pawn")
    assert not boss_compatible("BOSS_009", "e_ex353_Pawn")
    assert boss_compatible("BOSS_009", "e_ex309_Pawn")
    assert boss_compatible("BOSS_009", "e_ex309_Pawn_s10")


@pytest.mark.parametrize("data_id", [*range(351, 363), 367])
def test_marshmallow_excludes_all_data_bosses(data_id):
    pawn = f"e_ex{data_id}_Pawn"
    assert not boss_compatible("BOSS_011", pawn)
    assert boss_compatible("BOSS_021", pawn)


def test_boss_arena_bans_preserve_generated_boss_pool(templates):
    originals = json.loads((templates / "wwwroot/DefaultBosses.json").read_text(encoding="utf-8-sig"))
    originals = {boss: pawn.rstrip("\0") for boss, pawn in originals.items()}
    for seed in range(100):
        result = boss_remap(templates, f"MonstropolisAudit-{seed}", {})
        replacements = originals.copy()
        for entry in result["boss_runtime_entries"]:
            boss, _, replacement = entry.split("|")
            replacements[boss] = result["boss_runtime_classes"][int(replacement)].split("/")[-1].split(".")[0]
        assert replacements["BOSS_010"] not in ("e_ex352_Pawn", "e_ex357_Pawn")
        assert replacements["BOSS_005"] != "e_ex353_Pawn"
        assert replacements["BOSS_009"] != "e_ex353_Pawn"
        data_pawns = {pawn for boss, pawn in originals.items() if 93 <= int(boss[5:]) <= 105}
        assert replacements["BOSS_011"] not in data_pawns
        assert sorted(replacements.values()) == sorted(originals.values())
        assert all(boss_compatible(boss, pawn) for boss, pawn in replacements.items())


def test_generation_and_fingerprint_load_from_zipimport(templates, tmp_path, monkeypatch):
    package = "kh3_seedgen_zip_test"
    archive_path = tmp_path / "modules.zip"
    source = Path(__file__).parents[1]
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(package + "/__init__.py", "")
        archive.write(source / "Pak.py", package + "/Pak.py")
        archive.write(source / "RuntimeSeed.py", package + "/RuntimeSeed.py")
        archive.write(source / "RaceMode.py", package + "/RaceMode.py")
        archive.write(source / "BossCatalog.py", package + "/BossCatalog.py")
        archive.write(source / "data/runtime_json_limits.json", package + "/data/runtime_json_limits.json")
        for path in (source / "seedgen").glob("*.py"):
            archive.write(path, package + "/seedgen/" + path.name)
    monkeypatch.syspath_prepend(str(archive_path))
    try:
        module = importlib.import_module(package + ".seedgen.generator")
        config, items, metadata = inputs(templates, hint="None")
        pak, _ = module.generate_seed(templates, tmp_path / "out", metadata["seed"], config, items, metadata)
        assert json.loads(PakReader(pak).read(FINGERPRINT_PATH))["implementation"] == "python"
    finally:
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]
