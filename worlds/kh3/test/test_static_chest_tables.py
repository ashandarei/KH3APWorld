import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ..client_support.static_chest_tables import validate_static_chest_tables
from ..Pak import pack_directory


class TestStaticChestTables(unittest.TestCase):
    def test_garden_validation_requires_all_suppression_tables(self):
        from ..client_support.goa_download import KH3_GOA_MOD_FILE_NAME
        manifest_path = Path(__file__).resolve().parents[1] / "data" / "static_chest_tables.json"
        manifest = json.loads(manifest_path.read_text())
        # Test the client's pak/hash validation with synthetic payloads; Unreal
        # assets are authored and verified by the TresGame/Garden build.
        payloads = {name: name.encode() for name in manifest["files"]}
        manifest["files"] = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
        with TemporaryDirectory() as temporary, patch(
            "worlds.kh3.client_support.static_chest_tables.pkgutil.get_data",
            return_value=json.dumps(manifest).encode(),
        ):
            game = Path(temporary) / "game"
            destination = game / "Content" / "Paks" / "~mods" / KH3_GOA_MOD_FILE_NAME
            inputs = Path(temporary) / "inputs"
            for name in manifest["files"]:
                path = inputs / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payloads[name])
            pack_directory(inputs, destination, mount_point="../../../KINGDOM HEARTS III/")
            validate_static_chest_tables(game)
            first = inputs / next(iter(manifest["files"]))
            original = first.read_bytes()
            first.write_bytes(original + b"outdated")
            pack_directory(inputs, destination, mount_point="../../../KINGDOM HEARTS III/")
            with self.assertRaisesRegex(RuntimeError, "Garden needs updating"):
                validate_static_chest_tables(game)
            first.write_bytes(original)
            # A partial/old Garden must stop launch before vanilla rewards leak.
            first.unlink()
            pack_directory(inputs, destination, mount_point="../../../KINGDOM HEARTS III/")
            with self.assertRaisesRegex(RuntimeError, "Garden needs updating"):
                validate_static_chest_tables(game)
