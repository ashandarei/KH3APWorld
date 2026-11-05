"""Generate fresh AP seeds and audit KH3 paks using the Python backend only.

Run from the repository root, for example:
    python tools/kh3/stress_python_seedgen.py --count 1000 --workers 4 --output build/kh3-python-stress-1000
All inputs, paks, logs, per-case results and an aggregate report are retained.
External commands and downloads are forbidden inside each generation worker.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[2]
GAME = "Kingdom Hearts III"


def settings_for(index: int, player: int = 1) -> dict:
    """Deterministic option sampling with explicit boundary cases."""
    rng = random.Random(171313000 + index * 101 + player)
    choose = rng.choice
    options = {
        "heart_piece_count": choose([3, 7, 30]),
        "worlds_required": choose([0, 3, 7]),
        "keyblade_levels": choose(["start_at_max", "shuffled", "keyblade_upgrade_sanity"]),
        "stylesanity": choose(["off", "on"]),
        "ultima_weapon_mode": ["shards", "shop", "yozora", "randomized_pool", "banished"][index % 5],
        "ultima_weapon_shard_count": choose([2, 7, 13]),
        "lucky_emblems_per_check": choose(["vanilla milestones", 1, 2, 3, 7, 10, 30, 90]),
        "lucky_emblem_limit": choose(list(range(10, 91, 10))),
        "level_up_limit": choose([1, 35, 50, 99]),
        "moogle_synthesis_limit": choose([0, 15, 67]),
        "moogle_shop_checks": choose([0, 1, 10, 20]),
        "moogle_shop_price": choose([0, 100, 1000, 10000]),
        "hint_selection_type": ["none", "vague_by_check", "vague_by_category", "verbose"][index % 4],
        "trap_percentage": choose([0, 25, 100]),
        "ingredients": choose(["off", "ingredientsanity", "ingredientsanity_plus"]),
        "classic_kingdom_checks": choose(["off", "high_score"]),
        "color_randomization": choose(["off", "coordinated", "wild"]),
        "music_shuffle": choose(["vanilla", "shuffle_worlds", "shuffle_battle_themes", "chaos"]),
        "battle_level_mode": choose(["off", "sora_level"]),
        "portal_unlocks": choose(["keyblades", "party_members"]),
    }
    for name in ("base_sora", "level_up", "keyblade_enhance", "equipment", "food_effect"):
        options[name + "_stats_pool"] = choose(["vanilla", "balanced", "boosted"])
    for name in ("equipment_abilities", "fullcourse_abilities", "synthesis", "photo_missions", "keyblade_upgrade_rewards"):
        options[name + "_pool"] = choose(["vanilla", "randomize", "junk"])
    for name in ("enemies", "bosses", "party_members"):
        options[name + "_pool"] = choose(["vanilla", "randomize"])
    for name in ("hundred_acre_wood", "unreality", "data_battle_rewards", "lucky_emblems", "battlegates",
                 "flantastic_flans", "verum_rex", "festival_dance", "frozen_slider", "flash_tracer_course_a"):
        options[name + "_pool"] = choose(["randomize", "junk"])
    for name in ("default_base_abilities", "start_with_wall_run", "start_with_magic", "start_with_links",
                 "start_with_attraction_flow", "allow_all_stats", "allow_negative_stats", "default_data_battles",
                 "default_end_game_battles", "default_yozora_battle", "enemy_chaos", "party_chaos", "chestsanity",
                 "include_space", "include_grindy_space_missions", "include_space_superbosses", "easier_mini_ufo",
                 "faster_raging_vulture", "faster_lich_sequence", "dark_baymax_phase_1_skip", "frozen_chase_skip",
                 "faster_crab_collection", "faster_sora_collection", "guardians_of_light_skip", "fun_10k_heartless",
                 "skip_guardians_and_mickey_walk", "fast_union_cross", "fast_dance_minigame", "fast_beat_of_lead",
                 "max_level_ship", "skip_world_intro_cutscenes", "auto_skip_cutscenes"):
        options[name] = choose([False, True])
    return options


def forbidden(*args, **kwargs):
    raise RuntimeError("Stress test forbids external processes and downloads; Python only")


def audit(pak_path: Path, work: Path, expected_slot: int = 1) -> dict:
    from worlds.kh3.Pak import PakReader
    from worlds.kh3.seedgen.assets import Asset, get
    from worlds.kh3.seedgen.generator import METADATA_PATH, FINGERPRINT_PATH, backend_fingerprint
    from worlds.kh3.seedgen.localization import Locres, text_ids
    from worlds.kh3.BossCatalog import resolve_boss_blacklist
    from worlds.kh3.seedgen.tables import PATHS, QOL_ASSETS, mode, prefix

    reader = PakReader(pak_path)
    assert reader.mount_point == "../../../"
    payload = {name: reader.read(name) for name in reader.entries}
    runtime = json.loads(payload[METADATA_PATH])
    fingerprint = json.loads(payload[FINGERPRINT_PATH])
    templates = work / "bundled-randomizer"
    assert fingerprint == backend_fingerprint(templates)
    assert fingerprint["implementation"] == "python"
    assert runtime["randomizer_backend"] == fingerprint
    assert "placement_summary" not in runtime
    seed = runtime["seed"]
    full = json.loads((work / f"{seed}.generated-metadata.json").read_text(encoding="utf-8"))
    config = json.loads((work / f"{seed}.json").read_text(encoding="utf-8"))
    items = json.loads((work / f"{seed}.randomized-options.json").read_text(encoding="utf-8"))
    assert full["seed"] == seed and full["slot"] == expected_slot
    assert json.loads(payload["SpoilerLog.json"])["SeedName"] == seed
    assert not any("GoaSeedOptions" in path or "GoASeedOptions" in path for path in payload)
    staged = templates / "wwwroot/pak/Seeds" / hashlib.sha256(seed.encode()).hexdigest()[:12]
    staged_files = {path.relative_to(staged).as_posix(): path for path in staged.rglob("*") if path.is_file()}
    assert set(staged_files) == set(payload)
    assert all(staged_files[name].read_bytes() == data for name, data in payload.items())

    tables = {}
    for table, path in PATHS.items():
        base = "KINGDOM HEARTS III/" + path
        assert base + ".uasset" in payload and base + ".uexp" in payload, table
        asset = Asset(payload[base + ".uasset"], payload[base + ".uexp"])
        assert asset.encode() == (payload[base + ".uasset"], payload[base + ".uexp"]), table
        tables[table] = asset
    slots = runtime["slot_data"]
    sora = get(tables["ChrInit"].exports[-1].properties, "m_PlayerSora").value
    abilities = get(sora, "m_EquipAbility").value
    assert ("ETresAbilityKind::AP_STYLE_CHANGE" in abilities) == (int(slots["stylesanity"]) == 0)
    assert len(abilities) == (17 if int(slots["stylesanity"]) == 0 else 16)
    assert len(tables["WeaponEnhance"].rows) == 170
    assert all(get(row, "m_bInitAchieved").value == (int(slots["keyblade_levels"]) == 0)
               for row in tables["WeaponEnhance"].rows.values())
    assert "IS_58" not in tables["SynthesisItem"].rows
    assert get(tables["SynthesisItem"].rows["IS_59"], "m_FlagIndex").value == 59
    assert set(tables["LuckyMark"].rows) == set(items["LuckyMark"])
    for milestone, row in tables["LuckyMark"].rows.items():
        token = items["LuckyMark"][milestone]["Reward"].rstrip("\0")
        assert get(row, "RewardTreasureName").value == ("AP_ITEM_000" if token == "ARCHIPELAGO_ITEM" else token)
        assert get(row, "CompleteRewardFlag").value == (int(milestone) == 90)
    conditions = prefix(tables["Completion"].rows["CompletionItems_001"], "CompletionItems").value
    assert {prefix(row, "Name").value: prefix(row, "Amount").value for row in conditions} == {
        key.rstrip("\0"): count for key, count in config["clear_condition"]["conditions"].items()}
    flags = {prefix(row, "FlagName").value: prefix(row, "FlagValue").value for row in tables["WorldFlags"].rows.values()}
    assert flags["Gameflow_HE"] == 0 and flags["Gameflow_EW"] == 2000

    qol = {name: bool(prefix(row, "Active").value) for name, row in tables["QoL"].rows.items()}
    for key, path in QOL_ASSETS.items():
        assert ("KINGDOM HEARTS III/" + path in payload) == qol[key]
        if qol[key]:
            for extension in (Path(path).suffix, ".uexp"):
                source = Path(path).with_suffix(extension)
                assert payload["KINGDOM HEARTS III/" + source.as_posix()] == (templates / source).read_bytes()
    for name in ("kh3_item", "kh3_mobile"):
        raw = payload[f"KINGDOM HEARTS III/Content/Localization/Game/en/{name}.locres"]
        resource = Locres(raw)
        assert resource.encode() == raw
    item_locres = Locres(payload["KINGDOM HEARTS III/Content/Localization/Game/en/kh3_item.locres"])
    rows_by_key = defaultdict(list)
    for row in tables["Items"].rows.values():
        rows_by_key[get(row, "m_KeyName").value].append(row)
    placements = full["placement_summary"]["locations"]
    shops = 0
    for placement in placements:
        token = placement.get("shop_item_token")
        if not token:
            continue
        shops += 1
        row = rows_by_key[token][0]
        assert get(row, "m_BuyPrice").value == int(placement["shop_price"])
        assert get(row, "m_bCanbeSoldOut").value and get(row, "m_MaxNum").value == 1
        name_id, _ = text_ids(token)
        assert get(row, "m_NameTextID").value == name_id
        index = item_locres.namespaces["kh3_item.battle"][name_id.removeprefix("kh3_item.battle.")][1]
        assert placement["item_name"] in item_locres.strings[index]
    assert shops == int(slots["moogle_shop_checks"])
    # Native AP placeholder Name properties must survive enum-only reward slots.
    for name, fields in items["LevelUp"].items():
        for field, token in fields.items():
            if not field.startswith("Ability"):
                continue
            index = "ABC".index(field[-1]) + 1
            native = token.startswith("ETresAbilityKind::")
            expected = "None" if native else "AP_ITEM_000" if token == "ARCHIPELAGO_ITEM" else token.rstrip("\0")
            assert prefix(tables["LevelUpAlt"].rows[name], f"abilitySlot{index}").value == expected

    hint_placements = full["placement_summary"].get("hint_locations", placements)
    targets = {(int(p["location_player_slot"]), int(p["ap_location_id"])): p for p in hint_placements}
    reports = runtime["secret_report_hints"]["reports"]
    assert set(reports) == {str(n) for n in range(1, 14)}
    hinted = set()
    remote_hints = 0
    for number, records in reports.items():
        for record in records:
            key = (record["player"], record["location"])
            source = targets[key]
            assert int(source["recipient_slot"]) == expected_slot and source["item_name"] == record["item_name"]
            assert record["item_name"] != f"Secret Report {number}"
            assert key not in hinted
            hinted.add(key)
            remote_hints += record["player"] != expected_slot
    if config["hint"]["type"] == "None":
        assert not hinted
    else:
        expected = {(int(p["location_player_slot"]), int(p["ap_location_id"])) for p in hint_placements
                    if int(p["recipient_slot"]) == expected_slot and p["item_name"] in config["hint"]["checks"]}
        assert hinted == expected

    exceptions = config["exceptions"]
    # Encounter generation is tested by the compiled native parity suite. This
    # Python-only battery verifies the exact inputs delivered to that generator.
    assert "enemy_runtime_remap" not in runtime and "boss_runtime_remap" not in runtime
    assert runtime["native_randomization"] == dict(version=1,
        enemy_chaos=bool(exceptions.get("Enemy Chaos", False)),
        keep_data=bool(exceptions.get("Default Data Battles", False)),
        keep_endgame=bool(exceptions.get("Default End Game Battles", False)),
        keep_yozora=bool(exceptions.get("Default Yozora Battle", False)),
        boss_blacklist=sorted(resolve_boss_blacklist(config.get("boss_blacklist", ()))))
    settings_path = "KINGDOM HEARTS III/Content/Archipelago/runtime_seed_settings.json"
    assert reader.entries[settings_path].codec == 0
    assert json.loads(payload[settings_path])["native_randomization"] == runtime["native_randomization"]
    for key, pool in (("enemy", "Enemies"), ("boss", "Bosses"), ("party", "Party Members")):
        assert runtime[key + "_randomization_initially_enabled"] == (mode(config["pools"][pool]) == 2)
    assert runtime["party_randomize_on_room_change"] == bool(exceptions["Party Chaos"])
    return {"entries": len(payload), "assets": len(tables), "shops": shops, "hint_targets": len(hinted),
            "remote_hints": remote_hints, "native_generation_version": runtime["native_randomization"]["version"],
            "fingerprint": fingerprint, "seed": seed}


def run_case(arguments: tuple) -> dict:
    index, output, repeat = arguments
    case = Path(output) / "cases" / f"{index:04}"
    case.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = {"case": index, "status": "failed", "options": settings_for(index), "players": 2 if index % 5 == 0 else 1}
    try:
        sys.path.insert(0, str(ROOT))
        os.environ["AP_WORLD_LOAD_PATHS"] = json.dumps([str(ROOT / "worlds/kh3")])
        import Utils
        home = case / "runtime-home"
        home.mkdir(exist_ok=True)
        Utils.user_path.cached_path = str(home)
        import Generate
        from Main import main as generate
        from worlds.kh3 import PakGenerator
        import subprocess
        import urllib.request
        import yaml
        subprocess.run = forbidden
        subprocess.Popen = forbidden
        os.system = forbidden
        urllib.request.urlopen = forbidden
        urllib.request.urlretrieve = forbidden
        os.environ["PATH"] = ""
        players = case / "players"
        players.mkdir(exist_ok=True)
        for player in range(1, result["players"] + 1):
            name = "Sora世界" if player == 1 and index % 8 == 0 else "PythonTest" if player == 1 else "Riku"
            payload = {"name": name, "game": GAME, GAME: settings_for(index, player)}
            (players / f"{player}.yaml").write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        with (case / "generation.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            logging.getLogger().handlers.clear()
            logging.basicConfig(stream=log, level=logging.WARNING)
            rolled, seed = Generate.main(Generate.mystery_argparse([
                "--nogui", "--multi", str(result["players"]), "--player_files_path", str(players),
                "--weights_file_path", "unused-weights.yaml", "--meta_file_path", "unused-meta.yaml",
                "--outputpath", str(case / "ap"), "--spoiler", "0", "--seed", str(1713130000 + index)]))
            world = generate(rolled, seed)
            assert world.can_beat_game(), "Generated Archipelago placement is not beatable"
            result["ap_beatable"] = True
            result["ap_seconds"] = time.perf_counter() - started
            archive, = (case / "ap").glob("*.zip")
            # Exercise both entry points used by the web worker and player client.
            if index % 2:
                pak, = PakGenerator.generate_kh3_paks_from_ap_zip(archive, case / "paks", requested_slot=1)
                result["entry_point"] = "ap_zip"
            else:
                with zipfile.ZipFile(archive) as zipped:
                    names = [name for name in zipped.namelist() if name.endswith(".apkh3")]
                    container_name = next(name for name in names if "_P1_" in name)
                    container = case / Path(container_name).name
                    container.write_bytes(zipped.read(container_name))
                pak = PakGenerator.generate_kh3_pak_from_container(container, case / "paks")
                result["entry_point"] = "apkh3"
            result["pak_seconds"] = time.perf_counter() - started - result["ap_seconds"]
            audit_started = time.perf_counter()
            result.update(audit(pak.pak_path, case / "paks/work"))
            result["audit_seconds"] = time.perf_counter() - audit_started
            result["bytes"] = pak.pak_path.stat().st_size
            result["sha256"] = hashlib.sha256(pak.pak_path.read_bytes()).hexdigest()
            result["pak"] = str(pak.pak_path)
            if repeat:
                # Repeat the complete Python entry point, including fresh template preparation.
                if result["entry_point"] == "ap_zip":
                    repeated, = PakGenerator.generate_kh3_paks_from_ap_zip(archive, case / "paks", requested_slot=1)
                else:
                    repeated = PakGenerator.generate_kh3_pak_from_container(container, case / "paks")
                assert hashlib.sha256(repeated.pak_path.read_bytes()).hexdigest() == result["sha256"], "Nondeterministic pak"
                result["deterministic_repeat"] = True
        result["status"] = "passed"
    except Exception:
        result["error"] = traceback.format_exc()
    result["seconds"] = time.perf_counter() - started
    (case / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def collect_paks(output: Path, report: dict) -> None:
    """Put the validated paks in one convenient directory with a hash manifest."""
    destination = output / "paks"
    destination.mkdir()
    manifest = []
    for result in report["cases"]:
        if result["status"] != "passed":
            continue
        source = Path(result["pak"])
        target = destination / source.name
        if target.exists():
            raise ValueError(f"Duplicate final pak filename: {target.name}")
        shutil.copy2(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != result["sha256"]:
            raise ValueError(f"Final pak copy failed hash verification: {target.name}")
        manifest.append({"case": result["case"], "filename": target.name, "seed": result["seed"],
                         "bytes": result["bytes"], "sha256": result["sha256"]})
    assert len(list(destination.glob("*.pak"))) == report["passed"]
    (output / "pak_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report["pak_directory"] = str(destination)
    report["pak_manifest"] = str(output / "pak_manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--repeat-every", type=int, default=10)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new empty output directory to keep every run independently reviewable")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool, (output / "progress.jsonl").open("w", encoding="utf-8") as progress:
        jobs = {pool.submit(run_case, (index, str(output), args.repeat_every > 0
                                     and index % args.repeat_every == (index // args.repeat_every) % args.repeat_every)): index
                for index in range(args.start, args.start + args.count)}
        for future in as_completed(jobs):
            result = future.result()
            results.append(result)
            progress.write(json.dumps(result, ensure_ascii=False) + "\n")
            progress.flush()
            passed = sum(r["status"] == "passed" for r in results)
            if len(results) % 10 == 0 or result["status"] != "passed":
                print(f"{len(results)}/{args.count} finished: {passed} passed, {len(results) - passed} failed; {time.perf_counter() - started:.1f}s", flush=True)
                if result["status"] != "passed":
                    print(f"Case {result['case']}: {result['error']}", flush=True)
    passed = [r for r in results if r["status"] == "passed"]
    failures = [r for r in results if r["status"] != "passed"]
    coverage = defaultdict(Counter)
    for result in passed:
        for option, value in result["options"].items():
            coverage[option][str(value)] += 1
    report = {"backend": "python only; subprocesses and downloads forbidden in generation workers",
              "requested": args.count, "passed": len(passed), "failed": len(failures),
              "elapsed_seconds": time.perf_counter() - started, "workers": args.workers,
              "deterministic_repeats": sum(r.get("deterministic_repeat", False) for r in passed),
              "two_player_seeds": sum(r["players"] == 2 for r in passed),
              "entry_points": dict(Counter(r["entry_point"] for r in passed)),
              "unique_pak_hashes": len({r["sha256"] for r in passed}),
              "option_coverage": {k: dict(v) for k, v in sorted(coverage.items())},
              "failures": failures, "cases": sorted(results, key=lambda r: r["case"])}
    if passed:
        report.update(total_pak_bytes=sum(r["bytes"] for r in passed),
                      size_min=min(r["bytes"] for r in passed), size_max=max(r["bytes"] for r in passed),
                      mean_case_seconds=sum(r["seconds"] for r in passed) / len(passed),
                      assets_audited=sum(r["assets"] for r in passed),
                      pak_entries_audited=sum(r["entries"] for r in passed),
                      remote_hint_targets=sum(r["remote_hints"] for r in passed),
                      fingerprints=[json.loads(value) for value in sorted({json.dumps(r["fingerprint"], sort_keys=True)
                                                                           for r in passed})])
    collect_paks(output, report)
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# KH3 Python generator stress test", "", f"{len(passed)}/{args.count} cases passed; {len(failures)} failed.", "",
             f"Generated from fresh, beatable Archipelago placements using {args.workers} Python workers.",
             f"Elapsed: {report['elapsed_seconds']:.1f} seconds. Repeat determinism checks: {report['deterministic_repeats']}.",
             f"Two-player seeds: {report['two_player_seeds']}. Entry points: {report['entry_points']}.", "",
             "Every pak is read in full for index/data hashes, bounds and decompression; its complete payload is compared to staging.",
             "All 19 cooked edited assets are parsed and reencoded byte-identically. Checks cover goals, styles, keyblade levels,",
             "synthesis, lucky-emblem rewards, native level rewards, shop prices/localization, hints, QoL asset inclusion,",
             "enemy category/pinning rules, boss arena restrictions and runtime options.", "",
             "The validated paks are collected under paks/ with hashes in pak_manifest.json.",
             "All inputs and logs are retained under cases/<case>/; report.json contains timings and option coverage.",
             "This is an offline integrity and logic test. Loading these paks in KH3 was not tested."]
    if failures:
        lines.extend(["", "Failures:", *[f"- Case {r['case']}: see cases/{r['case']:04}/result.json" for r in failures]])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("cases", "failures", "option_coverage")}, indent=2), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
