from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from .. import PakGenerator
from ..client_support import olympus_coliseum_compat as compat_support
from ..client_support.olympus_coliseum_compat import (
    OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX,
    OLYMPUS_COLISEUM_COMPAT_PAK_NAME,
    OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES,
    OLYMPUS_COLISEUM_SOURCE_PAK_NAME,
    OLYMPUS_COLISEUM_SOURCE_PAK_SHA256,
    ensure_olympus_coliseum_compat,
    reconcile_olympus_coliseum_compat,
)


class TestOlympusColiseumCompat(unittest.TestCase):
    def test_persistent_chests_keep_companion_with_vanilla_cups_and_rebuild_on_mode_change(self):
        with TemporaryDirectory() as temp_dir:
            mods = Path(temp_dir)
            (mods / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).write_bytes(b"source")
            active = mods / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            active.write_bytes(b"old companion without chest override")
            calls = []

            def builder(source, output, **modes):
                calls.append(modes)
                output.write_bytes(b"C" * OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES)
                return output

            slot = {"olympus_coliseum_pool": 1, "bridge": {"chest_persistence_version": 1}}
            with patch.object(compat_support, "_file_sha256", return_value=OLYMPUS_COLISEUM_SOURCE_PAK_SHA256):
                first = ensure_olympus_coliseum_compat(mods, slot, game_running=False, build_companion=builder)
                self.assertEqual(first.status, "built")
                self.assertEqual(calls, [{"suppress_cup_rewards": False}])
                self.assertTrue(list(mods.glob("*.pre-update-*.bak")))
                again = ensure_olympus_coliseum_compat(mods, slot, game_running=True, build_companion=builder)
                self.assertEqual(again.status, "active")
                self.assertEqual(len(calls), 1)
                slot["olympus_coliseum_pool"] = 2
                running = ensure_olympus_coliseum_compat(mods, slot, game_running=True, build_companion=builder)
                self.assertEqual(running.status, "restart_required")
                self.assertEqual(len(calls), 1)
                changed = ensure_olympus_coliseum_compat(mods, slot, game_running=False, build_companion=builder)
                self.assertEqual(changed.status, "built")
                self.assertEqual(calls[-1], {"suppress_cup_rewards": True})

    def test_failed_upgrade_retains_old_companion_and_requires_retry(self):
        with TemporaryDirectory() as temp_dir:
            mods = Path(temp_dir)
            (mods / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).write_bytes(b"source")
            active = mods / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            active.write_bytes(b"old companion")
            slot = {"olympus_coliseum_pool": 2, "bridge": {"chest_persistence_version": 1}}
            with patch.object(compat_support, "_file_sha256", return_value=OLYMPUS_COLISEUM_SOURCE_PAK_SHA256):
                result = ensure_olympus_coliseum_compat(
                    mods, slot, game_running=False, build_companion=Mock(side_effect=RuntimeError("bad table")))
            self.assertEqual(result.status, "build_failed")
            self.assertFalse(result.ready)
            self.assertEqual(active.read_bytes(), b"old companion")
            self.assertEqual(reconcile_olympus_coliseum_compat(mods, slot, game_running=False).status, "outdated")

    def test_persistent_chests_without_coliseum_do_not_require_companion(self):
        with TemporaryDirectory() as temp_dir:
            result = ensure_olympus_coliseum_compat(
                Path(temp_dir), {"olympus_coliseum_pool": 1, "bridge": {"chest_persistence_version": 1}},
                game_running=False, build_companion=Mock(side_effect=AssertionError("should not build")))
            self.assertTrue(result.ready)
            self.assertEqual(result.status, "inactive")

    def test_vanilla_disables_active_reward_companion(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            active_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            active_path.write_bytes(b"compat")

            result = reconcile_olympus_coliseum_compat(
                mods_dir,
                {"olympus_coliseum_pool": 1},
                game_running=False,
            )

            self.assertEqual(result.status, "disabled")
            self.assertTrue(result.changed)
            self.assertFalse(active_path.exists())
            disabled_path = mods_dir / (
                OLYMPUS_COLISEUM_COMPAT_PAK_NAME
                + OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX
            )
            self.assertEqual(disabled_path.read_bytes(), b"compat")

    def test_randomize_and_junk_reenable_disabled_reward_companion(self) -> None:
        for mode in (2, 3):
            with self.subTest(mode=mode), TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir)
                disabled_path = mods_dir / (
                    OLYMPUS_COLISEUM_COMPAT_PAK_NAME
                    + OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX
                )
                disabled_path.write_bytes(b"compat")
                compat_support._receipt_path(mods_dir).write_text(json.dumps({
                    "version": compat_support.OLYMPUS_COLISEUM_COMPAT_VERSION,
                    "suppress_cup_rewards": True, "suppress_chest_rewards": True,
                    "pak_sha256": compat_support._file_sha256(disabled_path),
                }), encoding="utf-8")

                result = reconcile_olympus_coliseum_compat(
                    mods_dir,
                    {"olympus_coliseum_pool": mode},
                    game_running=False,
                )

                self.assertEqual(result.status, "enabled")
                self.assertTrue(result.changed)
                self.assertFalse(disabled_path.exists())
                self.assertEqual(
                    (mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME).read_bytes(),
                    b"compat",
                )

    def test_randomize_reports_missing_companion(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = reconcile_olympus_coliseum_compat(
                Path(temp_dir),
                {"olympus_coliseum_pool": "randomize"},
                game_running=False,
            )

            self.assertEqual(result.status, "missing")
            self.assertFalse(result.ready)
            self.assertFalse(result.changed)

    def test_running_game_defers_a_required_rename(self) -> None:
        cases = (
            (1, OLYMPUS_COLISEUM_COMPAT_PAK_NAME),
            (
                2,
                OLYMPUS_COLISEUM_COMPAT_PAK_NAME
                + OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX,
            ),
        )
        for mode, installed_name in cases:
            with self.subTest(mode=mode), TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir)
                installed_path = mods_dir / installed_name
                installed_path.write_bytes(b"compat")

                result = reconcile_olympus_coliseum_compat(
                    mods_dir,
                    {"olympus_coliseum_pool": mode},
                    game_running=True,
                )

                self.assertEqual(result.status, "restart_required")
                self.assertFalse(result.ready)
                self.assertTrue(installed_path.is_file())

    def test_unknown_seed_leaves_files_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            active_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            active_path.write_bytes(b"compat")

            result = reconcile_olympus_coliseum_compat(
                mods_dir,
                {},
                game_running=False,
            )

            self.assertEqual(result.status, "unmanaged")
            self.assertTrue(active_path.is_file())

    def test_randomize_builds_missing_companion_from_verified_full_mod(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            source_path = mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
            source_path.write_bytes(b"verified full mod")
            calls: list[tuple[Path, Path]] = []

            def build_companion(source: Path, output: Path, **reward_modes) -> Path:
                calls.append((source, output))
                output.write_bytes(b"C" * OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES)
                return output

            with patch.object(
                compat_support,
                "_file_sha256",
                return_value=OLYMPUS_COLISEUM_SOURCE_PAK_SHA256,
            ):
                result = ensure_olympus_coliseum_compat(
                    mods_dir,
                    {"olympus_coliseum_pool": 2},
                    game_running=False,
                    build_companion=build_companion,
                )

            output_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            self.assertEqual(result.status, "built")
            self.assertTrue(result.ready)
            self.assertTrue(result.changed)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], source_path)
            self.assertEqual(calls[0][1].name, output_path.name)
            self.assertEqual(output_path.stat().st_size, OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES)

    def test_missing_or_unsupported_full_mod_never_invokes_builder(self) -> None:
        for source_exists, source_hash, expected_status in (
            (False, None, "source_missing"),
            (True, "0" * 64, "unsupported_source"),
        ):
            with self.subTest(expected_status=expected_status), TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir)
                if source_exists:
                    (mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).write_bytes(b"wrong mod")
                builder = Mock()
                with patch.object(compat_support, "_file_sha256", return_value=source_hash):
                    result = ensure_olympus_coliseum_compat(
                        mods_dir,
                        {"olympus_coliseum_pool": "junk"},
                        game_running=False,
                        build_companion=builder,
                    )

                self.assertEqual(result.status, expected_status)
                self.assertFalse(result.ready)
                builder.assert_not_called()

    def test_updated_coliseum_source_reaches_validating_builder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            source_path = mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
            source_path.write_bytes(b"updated full mod")
            output_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME

            def build_companion(source: Path, output: Path, **reward_modes) -> Path:
                self.assertEqual(source, source_path)
                output.write_bytes(b"C" * OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES)
                return output

            with patch.object(compat_support, "_file_sha256", return_value=
                              "A32E44BA76FFB6E085AB93ED972694C0B93889313F2AD23A2313133D55001EC9"):
                result = ensure_olympus_coliseum_compat(
                    mods_dir, {"olympus_coliseum_pool": "randomize"},
                    game_running=False, build_companion=build_companion,
                )
            self.assertEqual(result.status, "built")
            self.assertTrue(result.ready)
            self.assertEqual(result.path, output_path)
            self.assertEqual(source_path.read_bytes(), b"updated full mod")

    def test_updated_coliseum_source_still_requires_valid_reward_tables(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            source_path = mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
            source_path.write_bytes(b"updated full mod")
            with patch.object(compat_support, "_file_sha256", return_value=
                              "A32E44BA76FFB6E085AB93ED972694C0B93889313F2AD23A2313133D55001EC9"):
                result = ensure_olympus_coliseum_compat(
                    mods_dir, {"olympus_coliseum_pool": "randomize"}, game_running=False,
                    build_companion=Mock(side_effect=RuntimeError("Expected exactly 23 reward DataTables")),
                )
            self.assertEqual(result.status, "build_failed")
            self.assertFalse(result.ready)
            self.assertIn("23 reward DataTables", result.message)
            self.assertFalse((mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME).exists())

    def test_running_game_never_builds_missing_companion(self) -> None:
        cases = (
            (1, True, "restart_required"),
            (2, True, "restart_required"),
        )
        for mode, game_running, expected_status in cases:
            with self.subTest(mode=mode), TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir)
                (mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).write_bytes(b"full mod")
                builder = Mock()

                result = ensure_olympus_coliseum_compat(
                    mods_dir,
                    {"olympus_coliseum_pool": mode},
                    game_running=game_running,
                    build_companion=builder,
                )

                self.assertEqual(result.status, expected_status)
                builder.assert_not_called()

    def test_builder_failure_is_reported_without_claiming_readiness(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            (mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).write_bytes(b"verified full mod")
            with patch.object(
                compat_support,
                "_file_sha256",
                return_value=OLYMPUS_COLISEUM_SOURCE_PAK_SHA256,
            ):
                result = ensure_olympus_coliseum_compat(
                    mods_dir,
                    {"olympus_coliseum_pool": 2},
                    game_running=False,
                    build_companion=Mock(side_effect=RuntimeError("repak failed")),
                )

            self.assertEqual(result.status, "build_failed")
            self.assertFalse(result.ready)
            self.assertIn("repak failed", result.message)

    def test_pak_generator_invokes_python_builder_and_validates_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
            output_path = Path(temp_dir) / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            source_path.write_bytes(b"full mod")

            def build(source, output, *, suppress_cup_rewards):
                output.write_bytes(b"C" * OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES)

            with patch("worlds.kh3.seedgen.coliseum.build_compatibility", side_effect=build) as builder, \
                    patch.object(PakGenerator.subprocess, "run", side_effect=AssertionError("external tool")):
                result = PakGenerator.build_olympus_coliseum_compat_pak(source_path, output_path)

            self.assertEqual(result, output_path.resolve())
            builder.assert_called_once_with(source_path.resolve(), output_path.resolve(), suppress_cup_rewards=True)

    def test_pak_generator_surfaces_python_coliseum_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
            source.write_bytes(b"unsupported mod")
            with self.assertRaisesRegex(PakGenerator.KH3PakGenerationError, "Unsupported Olympus Coliseum pak SHA-256"):
                PakGenerator.build_olympus_coliseum_compat_pak(source, Path(temp_dir) / OLYMPUS_COLISEUM_COMPAT_PAK_NAME)


if __name__ == "__main__":
    unittest.main()
