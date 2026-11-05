from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .. import ExternalBridge, external_bridge_cli


class TestExternalBridgeCli(unittest.TestCase):
    def test_parser_preserves_arguments_choices_and_defaults(self) -> None:
        synthetic_save_dir = Path(tempfile.gettempdir()) / "kh3-cli-synthetic-saves"
        with patch.object(
            external_bridge_cli._bridge_backend, "default_save_directory", return_value=synthetic_save_dir
        ):
            parser = external_bridge_cli.build_parser()

        args = parser.parse_args(["synthetic-bridge"])
        self.assertEqual(args.bridge_dir, "synthetic-bridge")
        self.assertEqual(args.backend, "manual")
        self.assertEqual(args.poll_interval, 1.0)
        self.assertEqual(args.save_dir, str(synthetic_save_dir))
        self.assertEqual(args.process_name, "KINGDOM HEARTS III.exe")
        self.assertIsNone(args.pid)

        backend_action = next(action for action in parser._actions if action.dest == "backend")
        self.assertEqual(
            backend_action.choices,
            ("manual", "command-file", "observed-state", "save-files", "runtime-savepoint", "runtime-loot-events"),
        )

    def test_create_backend_preserves_dispatch_and_constructor_values(self) -> None:
        reporter = external_bridge_cli.ConsoleReporter()
        args = SimpleNamespace(
            backend="save-files",
            poll_interval=0.25,
            save_dir="synthetic-saves",
            process_name="synthetic.exe",
            pid=123,
        )
        backend = object()
        constructor = Mock(return_value=backend)
        with patch.object(external_bridge_cli._bridge_backend, "SaveFilesBackend", constructor):
            result = external_bridge_cli.create_backend(args, reporter)

        self.assertIs(result, backend)
        constructor.assert_called_once_with(
            Path("synthetic-saves"),
            0.25,
            reporter=reporter,
        )

    def test_main_dispatches_and_preserves_status_and_exit_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            save_dir = root / "saves"
            session = Mock()
            status_writer = Mock()
            backend = Mock()
            backend.run.return_value = 17
            with (
                patch.object(external_bridge_cli._bridge_backend, "default_save_directory", return_value=save_dir),
                patch.object(
                    external_bridge_cli._bridge_backend, "KH3BridgeSession", return_value=session
                ) as session_type,
                patch.object(
                    external_bridge_cli._bridge_backend,
                    "BridgeRuntimeStatusWriter",
                    return_value=status_writer,
                ) as status_type,
                patch.object(external_bridge_cli, "create_backend", return_value=backend) as create_backend,
            ):
                result = external_bridge_cli.main([str(bridge_dir), "--backend", "save-files"])

        self.assertEqual(result, 17)
        session_type.assert_called_once_with(bridge_dir)
        session.initialize.assert_called_once_with()
        status_type.assert_called_once_with(
            session,
            "save-files",
            details={
                "configured_save_dir": str(save_dir.resolve()),
                "save_dir": str(save_dir.resolve()),
                "save_dir_source": "configured",
            },
        )
        status_writer.mark_starting.assert_called_once_with()
        status_writer.mark_running.assert_called_once_with()
        backend.run.assert_called_once_with(session, status_writer)
        status_writer.mark_stopped.assert_called_once_with()
        create_backend.assert_called_once()

    def test_main_marks_error_and_reraises_backend_failure(self) -> None:
        backend = Mock()
        backend.run.side_effect = RuntimeError("synthetic failure")
        session = Mock()
        status_writer = Mock()
        synthetic_save_dir = Path(tempfile.gettempdir()) / "kh3-cli-synthetic-saves"
        with (
            patch.object(
                external_bridge_cli._bridge_backend,
                "default_save_directory",
                return_value=synthetic_save_dir,
            ),
            patch.object(external_bridge_cli._bridge_backend, "KH3BridgeSession", return_value=session),
            patch.object(
                external_bridge_cli._bridge_backend,
                "BridgeRuntimeStatusWriter",
                return_value=status_writer,
            ),
            patch.object(external_bridge_cli, "create_backend", return_value=backend),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                external_bridge_cli.main(["synthetic-bridge"])

        status_writer.mark_error.assert_called_once_with("synthetic failure")
        status_writer.mark_stopped.assert_not_called()

    def test_imports_do_not_parse_argv_or_exit(self) -> None:
        source = """
import argparse
import sys
from unittest.mock import patch
sys.argv = ["synthetic", "--invalid-on-import"]
with patch.object(argparse.ArgumentParser, "parse_args", side_effect=AssertionError("parsed argv")):
    import worlds.kh3.ExternalBridge
    import worlds.kh3.external_bridge_cli
"""
        env = dict(os.environ)
        env["KH3_SAVE_DIR"] = str(Path(tempfile.gettempdir()) / "kh3-cli-synthetic-saves")
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_bridge_entrypoint_delegates(self) -> None:
        cli = Mock()
        cli.main.return_value = 23
        cli.build_parser.return_value = object()
        with patch.object(ExternalBridge, "_load_external_bridge_cli", return_value=cli):
            self.assertEqual(ExternalBridge.main(["synthetic-bridge"]), 23)
            self.assertIs(ExternalBridge.build_parser(), cli.build_parser.return_value)

        cli.main.assert_called_once_with(["synthetic-bridge"])
        cli.build_parser.assert_called_once_with()

    def test_external_bridge_supports_package_execution(self) -> None:
        env = dict(os.environ)
        env["KH3_SAVE_DIR"] = str(Path(tempfile.gettempdir()) / "kh3-cli-synthetic-saves")
        result = subprocess.run(
            [sys.executable, "-m", "worlds.kh3.external_bridge_cli", "--help"],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("KH3 external bridge runner.", result.stdout)
        self.assertIn("--backend", result.stdout)

    def test_exit_decision_lives_in_cli_module(self) -> None:
        with patch.object(external_bridge_cli, "main", return_value=29):
            with self.assertRaises(SystemExit) as raised:
                external_bridge_cli.launch(["synthetic-bridge"])
        self.assertEqual(raised.exception.code, 29)


if __name__ == "__main__":
    unittest.main()
