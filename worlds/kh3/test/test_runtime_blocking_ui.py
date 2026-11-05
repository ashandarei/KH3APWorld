from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root


import re
import unittest
from .runtime_sources import read_composition_sources


class TestRuntimeBlockingUiSourceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cpp_root = native_project_root() / "KH3APProbeCpp" / "kh3ap"
        cls.hook_source = (cls.cpp_root / "hooks" / "blocking_ui/blocking_ui_hook_service.cpp").read_text(encoding="utf-8")
        cls.focus_source = (cls.cpp_root / "hooks" / "blocking_ui/blocking_ui_focus_service.cpp").read_text(
            encoding="utf-8"
        )
        cls.composition_source = read_composition_sources()
        cls.native_hook_source = (cls.cpp_root / "hooks" / "hook_service.cpp").read_text(
            encoding="utf-8"
        )
        cls.native_lifecycle_source = (cls.cpp_root / "hooks" / "hook_lifecycle_service.cpp").read_text(
            encoding="utf-8"
        )
        cls.hook_header = (cls.cpp_root / "hooks" / "blocking_ui/blocking_ui_hook_service.h").read_text(
            encoding="utf-8"
        )

    def test_observer_uses_global_exact_name_routes(self) -> None:
        for required_route in (
            "Hook::RegisterProcessEventPostCallback",
            "Hook::RegisterProcessInternalPreCallback",
            "Hook::RegisterProcessLocalScriptFunctionPreCallback",
            "HookProcessInternal();",
            "HookProcessLocalScriptFunction();",
            "reinterpret_cast<FFrame_50_AndBelow*>(&frame)",
            'STR("TresUI")',
            "SetGamePaused",
            "SetPause",
            "bPaused",
            "bPause",
            "script_lifecycle_names=6",
            "process_event_lifecycle_names=6",
            "process_event_pause_names=2",
            "optional route unavailable route=process_internal",
        ):
            with self.subTest(required_route=required_route):
                self.assertIn(required_route, re.sub(r'"\s*"', "", self.hook_source))
        self.assertNotIn("stack.CurrentNativeFunction()", self.hook_source)

    def test_only_meaningful_ui_lifecycle_names_are_observed(self) -> None:
        for required_signal in (
            "OnFocusIn",
            "OnFocusOut",
            "OnOpen",
            "OnClose",
            "OnShow",
            "OnHide",
        ):
            with self.subTest(required_signal=required_signal):
                self.assertIn(required_signal, self.hook_source)
        for noisy_signal in (
            "OnConstruct",
            "OnAnchorIn",
            "OnAnchorOut",
        ):
            with self.subTest(noisy_signal=noisy_signal):
                self.assertNotIn(noisy_signal, self.hook_source)

    def test_focus_handoff_is_bounded_and_has_a_lock_free_idle_path(self) -> None:
        for required_focus_contract in (
            "focus_ownership=identity_set",
            "pending_fast_path=atomic",
            "generation_guard=true",
            "game_state_polling=false",
            "release_pending_.load(std::memory_order_acquire)",
            "pending_release_generation_",
            "settled_generation != lifecycle_generation_",
            "stale focus release discarded",
        ):
            with self.subTest(required_focus_contract=required_focus_contract):
                source = self.hook_source + self.focus_source
                self.assertIn(required_focus_contract, source)
        fast_path_index = self.focus_source.index("if (!release_pending_.load(std::memory_order_acquire))")
        mutex_index = self.focus_source.index(
            "std::lock_guard lock{mutex_};",
            fast_path_index,
        )
        self.assertLess(fast_path_index, mutex_index)

    def test_runtime_wiring_owns_install_and_pending_release_dispatch(self) -> None:
        self.assertIn(
            "config_.blocking_ui_hooks.install_once();",
            self.native_lifecycle_source,
        )
        self.assertIn(
            "m_blocking_ui_hooks.process_pending_focus_release(",
            self.composition_source,
        )
        self.assertNotIn("install_blocking_ui_hooks", self.native_hook_source)
        self.assertNotIn("uninstall_blocking_ui_hooks", self.native_hook_source)


    def test_pause_state_is_only_a_guard_and_never_dispatches_item_delivery(self) -> None:
        source = self.focus_source + self.hook_header + self.composition_source
        self.assertNotIn("process_pending_grant_from_ui_release", source)
        self.assertNotIn("process_pending_grant_from_runtime_ready_event", source)
        self.assertNotIn("process_pending_grant_now", source)


if __name__ == "__main__":
    unittest.main()
