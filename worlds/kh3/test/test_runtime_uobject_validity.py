from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CPP_ROOT = native_project_root() / "KH3APProbeCpp"
KH3AP_ROOT = CPP_ROOT / "kh3ap"
VALIDITY_SOURCE = KH3AP_ROOT / "core" / "objects/uobject_validity.cpp"


class TestRuntimeUObjectValiditySourceContract(unittest.TestCase):
    def test_fast_validity_uses_the_matching_object_array_slot(self) -> None:
        source = VALIDITY_SOURCE.read_text(encoding="utf-8")

        for required_mechanism in (
            "GetInternalIndex()",
            "UObjectArray::GetNumElements()",
            "GetObjectItem()",
            "object_item->GetUObject() == object",
            "UObjectArray::IsValid(",
            "GetOuterPrivate()",
            "GetClassPrivate()",
            "GetSuperStruct()",
            "RF_BeginDestroyed",
            "RF_FinishDestroyed",
            "is_uobject_a_fast_unprotected",
            "try_get_uobject_full_name_fast_unprotected",
            "EXCEPTION_ACCESS_VIOLATION",
        ):
            with self.subTest(required_mechanism=required_mechanism):
                self.assertIn(required_mechanism, source)

        self.assertNotIn("ForEachUObject", source)
        self.assertNotIn("Container::UnrealVC", source)
        self.assertIn("s_max_uobject_outer_depth = 64", source)
        self.assertIn("s_max_uobject_class_depth = 64", source)
        self.assertRegex(
            source,
            r"UObjectArray::IsValid\(\s*const_cast<RC::Unreal::FUObjectItem\*>\(object_item\),\s*false\)",
        )

    def test_runtime_prefers_fast_validity_over_official_full_scan(self) -> None:
        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in KH3AP_ROOT.rglob("*.cpp")
        )

        self.assertNotIn("UObject::IsReal(", runtime_source)
        self.assertGreater(runtime_source.count("is_uobject_valid_fast("), 300)

    def test_fast_validity_source_is_part_of_the_runtime_target(self) -> None:
        cmake_source = (CPP_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("kh3ap/core/objects/uobject_validity.cpp", cmake_source)

    def test_transition_hooks_do_not_read_full_names_directly(self) -> None:
        for relative_path in (
            "hooks/hook_service.cpp",
        ):
            with self.subTest(relative_path=relative_path):
                source = (KH3AP_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("->GetFullName()", source)
                self.assertIn("try_get_uobject_full_name_fast", source)


if __name__ == "__main__":
    unittest.main()
