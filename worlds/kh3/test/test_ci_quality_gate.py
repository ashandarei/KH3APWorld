from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "check-kh3-ci.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("kh3_ci_quality_gate_test", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)
find_new_zero_caller_private_delegates = SCRIPT_MODULE.find_new_zero_caller_private_delegates


def test_private_delegate_guard_rejects_new_zero_caller_wrapper() -> None:
    findings = find_new_zero_caller_private_delegates(
        {},
        {
            "worlds/kh3/example.py": """
class Example:
    def _unused_delegate(self):
        return self.service.run()
""",
        },
        ["worlds/kh3/example.py"],
    )

    assert findings == [("worlds/kh3/example.py", "Example._unused_delegate", 3)]


def test_private_delegate_guard_accepts_wrapper_with_a_caller() -> None:
    source = """
class Example:
    def _used_delegate(self):
        return self.service.run()

    def run(self):
        return self._used_delegate()
"""

    assert (
        find_new_zero_caller_private_delegates(
            {},
            {"worlds/kh3/example.py": source},
            ["worlds/kh3/example.py"],
        )
        == []
    )


def test_private_delegate_guard_ignores_existing_and_dynamic_command_methods() -> None:
    existing = """
class Existing:
    def _compatibility_delegate(self):
        return self.service.run()
"""
    head = (
        existing
        + """

class Commands:
    def _cmd_resync(self):
        return self.service.resync()
"""
    )

    assert (
        find_new_zero_caller_private_delegates(
            {"worlds/kh3/example.py": existing},
            {"worlds/kh3/example.py": head},
            ["worlds/kh3/example.py"],
        )
        == []
    )
