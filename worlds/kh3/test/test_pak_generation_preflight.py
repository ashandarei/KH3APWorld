from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from .. import PakGenerator


class PakGenerationPreflightTest(unittest.TestCase):
    def test_generation_needs_no_external_sdk_tools_or_network(self) -> None:
        with patch.dict(os.environ, {"PATH": "", "KH3_DOTNET_PATH": "/missing/dotnet"}, clear=True), \
                patch.object(PakGenerator.subprocess, "run", side_effect=AssertionError("external process")), \
                patch.object(PakGenerator, "_download_file", side_effect=AssertionError("tool download")):
            tools = PakGenerator.ensure_kh3_generation_tools_available()
        self.assertEqual(tools["randomizer"]["implementation"], "python")
        self.assertEqual(tools["pak"]["implementation"], "python")

    def test_invalid_inputs_fail_before_tool_setup_or_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix, generate in (
                (".apkh3", PakGenerator.generate_kh3_pak_from_container),
                (".zip", PakGenerator.generate_kh3_paks_from_ap_zip),
            ):
                with self.subTest(suffix=suffix):
                    source = root / f"truncated{suffix}"
                    source.write_bytes(b"not a zip")
                    output = root / f"output{suffix}"
                    with patch.object(PakGenerator, "ensure_kh3_generation_tools_available") as tools:
                        with self.assertRaises(PakGenerator.KH3PakGenerationError):
                            generate(source, output_dir=output)
                    tools.assert_not_called()
                    self.assertFalse(output.exists())

    def test_invalid_container_manifest_is_a_player_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.apkh3"
            for manifest in ([], None, "a manifest", b"{broken"):
                with self.subTest(manifest=manifest):
                    with zipfile.ZipFile(source, "w") as archive:
                        archive.writestr("archipelago.json", manifest if isinstance(manifest, bytes) else json.dumps(manifest))
                        archive.writestr(PakGenerator.KH3_CONTAINER_PAYLOAD, json.dumps({"format": "kh3_ap_seed"}))
                    with self.assertRaises(PakGenerator.KH3PakGenerationError):
                        PakGenerator.load_kh3_container_data(source)
