from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest
from pathlib import Path

from .runtime_sources import cpp_function_body, read_autosave_sources

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE_ROOT = native_project_root() / "KH3APProbeCpp" / "kh3ap"
PROBE_HEADER = PROBE_ROOT / "diagnostics" / "kh3_autosave_probe.h"


class TestRuntimeAutosaveCallstackProbeSourceContract(unittest.TestCase):
    def test_create_file_hook_captures_the_stack_before_the_native_open(self) -> None:
        source = read_autosave_sources()
        hook = cpp_function_body(source, "hooked_create_file_w")

        self.assertLess(
            hook.index("capture_save_file_callstack("),
            hook.index("owner->create_file_w_trampoline_"),
        )
        self.assertIn("path_is_vanilla_kh3_save_file(effective_file_name)", hook)
        self.assertIn("_ReturnAddress()", hook)

    def test_callstack_is_fixed_size_and_normalized_to_main_module_rvas(self) -> None:
        header = PROBE_HEADER.read_text(encoding="utf-8")
        source = read_autosave_sources()

        self.assertIn("save_file_callstack_capacity = 48", header)
        self.assertIn("RtlCaptureStackBackTrace(", source)
        self.assertIn("address - main_module->base", source)
        self.assertIn("main_module_stack_hash", source)
        self.assertNotIn("SymInitialize", source)
        self.assertNotIn("SymFromAddr", source)

    def test_file_event_emits_pre_call_stack_diagnostics(self) -> None:
        source = read_autosave_sources()

        for field_name in (
            "pre_call_observed_at",
            "pre_call_filetime_100ns",
            "caller_rva",
            "captured_frame_count",
            "main_module_frame_count",
            "main_module_stack_hash",
            "main_module_stack_rvas",
        ):
            with self.subTest(field_name=field_name):
                self.assertIn(f'\\"{field_name}\\"', source)

    def test_native_request_probe_is_signature_validated_and_precedes_payload_copy(self) -> None:
        source = read_autosave_sources()

        self.assertIn("kh3_egs_native_save_request_rva = 0x18362c0", source)
        self.assertIn("kh3_steam_native_save_request_rva = 0x14d5820", source)
        self.assertIn("kh3_egs_native_save_request_signature", source)
        self.assertIn("kh3_steam_native_save_request_signature", source)
        self.assertIn("kh3_native_save_request_profiles", source)
        build = (native_project_root() / "KH3APProbeCpp/kh3ap/core/platform/game_build.h").read_text()
        self.assertIn("0xa969000", build.lower())
        self.assertIn("0x66743f34", build.lower())
        self.assertIn("kh3ap::native_build::egs.image_size", source)
        self.assertIn("kh3ap::native_build::egs.timestamp", source)
        self.assertIn("native_save_request_profile_for_image", source)
        self.assertIn("hooked_native_save_request", source)
        self.assertIn('STR("NativeAutoSaveRequest")', source)
        self.assertIn('\\"autosave_probe_auto_save_request\\"', source)
        self.assertNotIn("kh3_native_save_task_constructor_rva", source)
        self.assertNotIn("0x1838660", source)

        hook = cpp_function_body(source, "hooked_native_save_request")
        self.assertLess(
            hook.index("record_native_save_probe_event("),
            hook.index("owner->native_save_request_trampoline_)("),
        )


if __name__ == "__main__":
    unittest.main()
