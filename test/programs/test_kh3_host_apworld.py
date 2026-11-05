"""Verify the slim archive through AP's real loader, without loose KH3 files."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("host_apworld_builder", ROOT / "scripts/kh3_apworld_archive.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

SNAPSHOT = r'''
import hashlib, importlib.util, json, os, sys, tempfile, zipfile
from pathlib import Path
import Utils
source, user_root = map(Path, sys.argv[1:])
Utils.user_path = lambda *parts: str(user_root.joinpath(*parts))
os.environ['AP_WORLD_LOAD_PATHS'] = json.dumps([str(source)])
from worlds import kh3
from worlds.LauncherComponents import components
from test.general import setup_multiworld
options = [
    {'level_up_limit': 1},
    {'include_space': 1, 'include_grindy_space_missions': 1, 'include_space_superbosses': 1,
     'adversary_checks': 1,
     'music_chest_count': 5, 'music_chest_vanilla_conversion': 32,
     'synthesis_pool': 'randomize', 'photo_missions_pool': 'randomize',
     'keyblade_upgrade_rewards_pool': 'randomize', 'moogle_shop_checks': 1},
]
fingerprints = []
for race, overrides in ((False, options[0]), (False, options[1]), (True, options[0])):
    mw = setup_multiworld(kh3.KH3World, seed=10092026, options=overrides)
    mw.is_race = race
    mw.seed_name = 'host-package-test'
    world = mw.worlds[1]
    pool = sorted((item.name, item.code, int(item.classification)) for item in mw.itempool)
    for location in mw.get_unfilled_locations(1):
        location.item = world.create_item('Potion')
    kh3.KH3World.stage_pre_output(mw)
    slot_data = world.fill_slot_data()
    with tempfile.TemporaryDirectory() as directory:
        world.generate_output(directory)
        with zipfile.ZipFile(next(Path(directory).glob('*.apkh3'))) as archive:
            assert archive.testzip() is None
            payload = json.loads(archive.read(kh3.KH3_CONTAINER_PAYLOAD))
        assert payload['slot_data'] == slot_data
    data = {'pool': pool, 'payload': payload, 'datapackage': kh3.KH3World.get_data_package_data()}
    fingerprints.append(hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
print(json.dumps({'origin': kh3.__file__, 'host_only': kh3.KH3_HOST_ONLY,
                  'has_client': importlib.util.find_spec('worlds.kh3.Client') is not None,
                  'components': sorted(component.display_name for component in components
                                       if component.display_name.startswith('KH3 ')),
                  'fingerprints': fingerprints}))
'''


class KH3HostApworldTests(unittest.TestCase):
    world_relevant = True

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.world = ROOT / "worlds/kh3"
        cls.archive = cls.root / "user/worlds/kh3.apworld"
        builder.build_world_apworld(cls.world, "Kingdom Hearts III", cls.archive, variant="host")

    def snapshot(self, source: Path) -> dict:
        env = {**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1"}
        result = subprocess.run([sys.executable, "-c", SNAPSHOT, str(source), str(self.root / "user")],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_host_archive_contains_only_generation_files(self):
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            for module in ("CharacterUnlocks.py", "MoMSongCatalog.py", "RaceMode.py",
                           "kh02/__init__.py", "kh02/locations.py", "kh02/objectives.py"):
                self.assertIn("kh3/" + module, names)
            self.assertNotIn("kh3/kh02/runtime_install.py", names)
            notices = {
                "kh3/LICENSE",
                "kh3/COPYRIGHT.txt",
                "kh3/LICENSE-Archipelago.txt",
                "kh3/LICENSE-KH3RandomizerClient.txt",
                "kh3/THIRD_PARTY_NOTICES.txt",
                "kh3/UPSTREAM-KH3RandomizerClient-CREDITS.txt",
            }
            self.assertTrue(all(name.endswith((".py", ".json", ".md")) or name in notices for name in names))
            for name in notices:
                self.assertIn(name, names)
                self.assertEqual((self.world / name.removeprefix("kh3/")).read_bytes(), archive.read(name))
            self.assertFalse(any(name.startswith(("kh3/runtime/", "kh3/gui/", "kh3/randomizer/",
                                                  "kh3/seedgen/", "kh3/client_support/", "kh3/transport/",
                                                  "kh3/bridge_support/", "kh3/diagnostics/")) for name in names))
            self.assertNotIn("kh3/Client.py", names)
            self.assertNotIn("kh3/PakGenerator.py", names)
            self.assertFalse(any(name.startswith("kh3/installer_support/") for name in names))
            source_manifest = json.loads((self.world / "archipelago.json").read_text())
            manifest = json.loads(archive.read("kh3/archipelago.json"))
            self.assertEqual(source_manifest["game"], manifest["game"])
            self.assertEqual(source_manifest["world_version"], manifest["world_version"])
            self.assertEqual("GPL-3.0-only", manifest["license"])
            self.assertIn(b"GNU GENERAL PUBLIC LICENSE", archive.read("kh3/LICENSE"))
            self.assertIn(b"Version 3, 29 June 2007", archive.read("kh3/LICENSE"))
            self.assertTrue((ROOT / "LICENSE").read_bytes().startswith(b"MIT License"))
            self.assertEqual((ROOT / "LICENSE").read_bytes(), archive.read("kh3/LICENSE-Archipelago.txt"))
            for runtime_license in (
                "runtime/Mods/KH3APProbeCpp/LICENSE", "runtime/KH02/02-GoA-Licenses/LICENSE"
            ):
                self.assertTrue((self.world / runtime_license).read_bytes().startswith(b"MIT License"))
            self.assertEqual("host", manifest["package_variant"])
            # Generation modules are copied unchanged from the player source.
            for name in names:
                if name.endswith(".py"):
                    self.assertEqual((self.world / name.removeprefix("kh3/")).read_bytes(), archive.read(name))

    def test_real_loader_generates_matching_seed_output_without_player_components(self):
        host = self.snapshot(self.archive)
        player = self.snapshot(self.world)
        self.assertIn(str(self.archive), host["origin"])
        self.assertTrue(host["host_only"])
        self.assertFalse(host["has_client"])
        self.assertEqual([], host["components"])
        self.assertFalse(player["host_only"])
        self.assertEqual(["KH3 Client", "KH3 Pak Generator"], player["components"])
        self.assertEqual(player["fingerprints"], host["fingerprints"])

    def test_host_archive_completes_normal_archipelago_generation(self):
        players = self.root / "players"
        players.mkdir()
        (players / "Sora.yaml").write_text(
            "name: Sora\ngame: Kingdom Hearts III\nKingdom Hearts III:\n"
            "  level_up_limit: 1\n  adversary_checks: 1\n  include_space: 1\n"
            "  synthesis_pool: randomize\n  photo_missions_pool: randomize\n"
            "  keyblade_upgrade_rewards_pool: randomize\n",
            encoding="utf-8")
        output = self.root / "generated"
        script = r'''
import io, json, os, sys
from pathlib import Path
import Utils
source, user_root, players, output = map(Path, sys.argv[1:])
Utils.user_path = lambda *parts: str(user_root.joinpath(*parts))
os.environ['AP_WORLD_LOAD_PATHS'] = json.dumps([str(source)])
import settings
settings.no_gui = True
settings.skip_autosave = True
import Generate
from worlds import kh3, failed_world_loads
from worlds.LauncherComponents import components
assert kh3.KH3_HOST_ONLY and str(source) in kh3.__file__
assert not failed_world_loads
assert not any(component.display_name.startswith('KH3 ') for component in components)
args = Generate.mystery_argparse(['--seed', '10092026', '--multi', '1', '--spoiler', '0',
                                 '--player_files_path', str(players), '--outputpath', str(output)])
generation_args, seed = Generate.main(args)
from Main import main
main(generation_args, seed)
from MultiServer import Context
from worlds.kh3.SlotData import decode_slot_data
import zipfile
with zipfile.ZipFile(next(output.glob('AP_*.zip'))) as archive:
    server = Context.decompress(archive.read(next(n for n in archive.namelist() if n.endswith('.archipelago'))))
    with zipfile.ZipFile(io.BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('.apkh3'))))) as player_archive:
        assert player_archive.testzip() is None
        payload = json.loads(player_archive.read('kh3_seed.json'))
assert payload['slot_data'] == json.loads(json.dumps(server['slot_data'][1]))
runtime = decode_slot_data(payload['slot_data'])
rows = runtime['bridge']['locations']
assert runtime['adversary_checks'] == 1
assert sum(row['type'] == 'adversary' for row in rows) == 48
assert len({row['ap_location_id'] for row in rows}) == len(rows)
assert {row['ap_location_id'] for row in rows} == set(server['locations'][1])
for row in rows:
    item, recipient, flags = server['locations'][1][row['ap_location_id']]
    assert (row['assigned_item_id'], row['assigned_item_player']) == (item, recipient)
    assert row['assigned_item_classification'] & 7 == flags
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.archive), str(self.root / "user"),
                                 str(players), str(output)], cwd=ROOT,
                                env={**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1"},
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        with zipfile.ZipFile(next(output.glob("AP_*.zip"))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(any(name.endswith(".archipelago") for name in archive.namelist()))
            seed = next(name for name in archive.namelist() if name.endswith(".apkh3"))
            with zipfile.ZipFile(io.BytesIO(archive.read(seed))) as player_archive:
                payload = json.loads(player_archive.read("kh3_seed.json"))
            self.assertEqual("Sora", payload["slot_name"])


if __name__ == "__main__":
    unittest.main()
