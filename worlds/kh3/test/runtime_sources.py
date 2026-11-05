"""Read native implementation components and isolate source-contract functions."""

from tools.kh3.native_integration import native_project_root
import re


def read_composition_sources() -> str:
    app_root = (
        native_project_root() / 'KH3APProbeCpp/kh3ap/app'
    )
    paths = [app_root / "composition.cpp"]
    paths.extend(sorted((app_root / "composition").glob("*.cpp")))
    paths.extend(sorted((app_root / "composition").glob("*.h")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def read_options_menu_sources() -> str:
    ui_root = (
        native_project_root() / 'KH3APProbeCpp/kh3ap/features/ui'
    )
    paths = [ui_root / "options_menu_service.cpp"]
    paths.extend(sorted((ui_root / "options_menu").glob("*.cpp")))
    paths.extend(sorted((ui_root / "options_menu").glob("*.h")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def cpp_function_body(source: str, name: str) -> str:
    """Find a definition independently of qualification, layout and file order."""
    signatures = re.finditer(
        rf"^\s*(?:\[\[nodiscard\]\]\s*)?(?:static\s+)?(?:auto|void\s*\*?)\s+"
        rf"(?:(?:__stdcall|__fastcall)\s+)?(?:\w+::)*{re.escape(name)}\s*\(",
        source,
        re.MULTILINE,
    )
    tokens = re.compile(r'//[^\n]*|/\*.*?\*/|L?"(?:\\.|[^"\\])*"|L?\'(?:\\.|[^\'\\])*\'|[(){};]', re.DOTALL)
    for signature in signatures:
        remaining = tokens.finditer(source, signature.end())
        parentheses = 1
        for token in remaining:
            if token.group() == "(":
                parentheses += 1
            elif token.group() == ")":
                parentheses -= 1
                if parentheses == 0:
                    break
        depth = 0
        for token in remaining:
            value = token.group()
            if value == ";" and depth == 0:
                break
            if value == "{":
                depth += 1
            elif value == "}":
                depth -= 1
                if depth == 0:
                    return source[signature.start():token.end()]
    raise AssertionError(f"C++ function definition not found: {name}")


def read_vbonus_sources() -> str:
    grants_root = (
        native_project_root() / 'KH3APProbeCpp/kh3ap/features/grants/vbonus'
    )
    components = (
        "vbonus_grant_call_helpers.cpp", "vbonus_memory.cpp", "vbonus_bank_functions.cpp",
        "vbonus_display.cpp", "vbonus_hud.cpp", "vbonus_banks.cpp",
        "vbonus_abilities.cpp", "vbonus_commands.cpp", "vbonus_inventory.cpp",
        "vbonus_diagnostics.cpp", "vbonus_grant_internal.h", "vbonus_layout.h",
    )
    return "\n".join((grants_root / name).read_text(encoding="utf-8") for name in components)


def read_autosave_sources() -> str:
    diagnostics_root = (
        native_project_root() / 'KH3APProbeCpp/kh3ap/diagnostics'
    )
    paths = [diagnostics_root / "kh3_autosave_probe.cpp"]
    paths.extend(sorted((diagnostics_root / "autosave").glob("*.cpp")))
    paths.extend(sorted((diagnostics_root / "autosave").glob("*.h")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)
