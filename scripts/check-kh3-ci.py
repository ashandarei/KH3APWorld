"""Constrained changed-file quality checks for KH3 pull requests."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
KH3_PATHSPECS = (":(top)KH3Client.py", ":(top,glob)worlds/kh3/**/*.py")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

MAX_NEW_MODULE_LINES = 800
MAX_NEW_CALLABLE_LINES = 100
MAX_NEW_CLASS_METHODS = 30


def run_git(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def changed_python_files(base: str, head: str, *, added_only: bool = False) -> list[str]:
    diff_filter = "A" if added_only else "ACMR"
    output = run_git(
        "diff",
        f"--diff-filter={diff_filter}",
        "--name-only",
        "-z",
        f"{base}...{head}",
        "--",
        *KH3_PATHSPECS,
    )
    return sorted(path for path in output.decode("utf-8").split("\0") if path and (REPOSITORY_ROOT / path).is_file())


def changed_lines(base: str, head: str, path: str) -> set[int]:
    patch = run_git(
        "diff",
        "--unified=0",
        "--no-color",
        f"{base}...{head}",
        "--",
        path,
    ).decode("utf-8", errors="replace")
    lines: set[int] = set()
    for patch_line in patch.splitlines():
        match = HUNK_HEADER.match(patch_line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        lines.update(range(start, start + count))
    return lines


def annotation(message: str, *, path: str | None = None, line: int | None = None) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        metadata = []
        if path:
            metadata.append(f"file={path}")
        if line:
            metadata.append(f"line={line}")
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        suffix = " " + ",".join(metadata) if metadata else ""
        print(f"::error{suffix}::{escaped}")
    else:
        location = f"{path}:{line}: " if path and line else f"{path}: " if path else ""
        print(f"{location}{message}", file=sys.stderr)


def relative_diagnostic_path(filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def run_changed_line_ruff(base: str, head: str, files: list[str]) -> bool:
    if not files:
        print("No changed KH3 Python files require Ruff checks.")
        return True

    line_map = {path: changed_lines(base, head, path) for path in files}
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--config",
        "ruff.toml",
        "--select",
        "E4,E7,E9,F,I",
        "--output-format",
        "json",
        *files,
    ]
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=REPOSITORY_ROOT, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        print(result.stdout, end="")
        print(result.stderr, file=sys.stderr, end="")
        return False

    try:
        diagnostics = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as error:
        annotation(f"Ruff returned invalid JSON: {error}")
        return False

    relevant = []
    for diagnostic in diagnostics:
        path = relative_diagnostic_path(diagnostic["filename"])
        start = int(diagnostic["location"]["row"])
        end = int(diagnostic["end_location"]["row"])
        touches_change = bool(line_map.get(path, set()).intersection(range(start, end + 1)))
        if touches_change:
            relevant.append((path, start, diagnostic))

    for path, line, diagnostic in relevant:
        annotation(f"{diagnostic['code']} {diagnostic['message']}", path=path, line=line)
    if relevant:
        print(f"Ruff found {len(relevant)} issue(s) on changed KH3 lines.", file=sys.stderr)
        return False

    print(f"Ruff changed-line checks passed for {len(files)} KH3 Python file(s).")
    return True


def callable_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return node.name


def _is_call_expression(expression: ast.expr | None) -> bool:
    if isinstance(expression, ast.Await):
        expression = expression.value
    return isinstance(expression, ast.Call)


def _is_thin_private_delegate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    name = node.name
    if not name.startswith("_") or name.startswith("__") or name.startswith("_cmd_"):
        return False
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if len(body) != 1:
        return False
    statement = body[0]
    if isinstance(statement, ast.Return):
        return _is_call_expression(statement.value)
    return isinstance(statement, ast.Expr) and _is_call_expression(statement.value)


def private_delegate_definitions(source: str, *, filename: str) -> list[tuple[str, str, int]]:
    """Return ``(qualified_name, name, line)`` for thin private delegates."""

    tree = ast.parse(source, filename=filename)
    definitions: list[tuple[str, str, int]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor API
            self.scope.append(node.name)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

        def _visit_callable(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualified_name = ".".join((*self.scope, node.name))
            if _is_thin_private_delegate(node):
                definitions.append((qualified_name, node.name, node.lineno))
            self.scope.append(node.name)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API
            self._visit_callable(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor API
            self._visit_callable(node)

    Visitor().visit(tree)
    return definitions


def find_new_zero_caller_private_delegates(
    base_sources: dict[str, str],
    head_sources: dict[str, str],
    changed_paths: list[str],
) -> list[tuple[str, str, int]]:
    """Find newly introduced thin private delegates with no identifier use."""

    base_definitions = {
        (path, qualified_name)
        for path, source in base_sources.items()
        for qualified_name, _name, _line in private_delegate_definitions(source, filename=path)
    }
    combined_head_source = "\n".join(head_sources.values())
    findings: list[tuple[str, str, int]] = []
    for path in changed_paths:
        source = head_sources.get(path)
        if source is None:
            continue
        for qualified_name, name, line in private_delegate_definitions(source, filename=path):
            if (path, qualified_name) in base_definitions:
                continue
            identifier_pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
            if len(re.findall(identifier_pattern, combined_head_source)) == 1:
                findings.append((path, qualified_name, line))
    return findings


def source_at_ref(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8")


def python_sources_at_ref(ref: str) -> dict[str, str]:
    paths = run_git("ls-tree", "-r", "--name-only", ref, "--", "KH3Client.py", "worlds/kh3")
    sources: dict[str, str] = {}
    for path in paths.decode("utf-8").splitlines():
        if not path.endswith(".py"):
            continue
        source = source_at_ref(ref, path)
        if source is not None:
            sources[path] = source
    return sources


def run_private_delegate_checks(base: str, head: str, files: list[str]) -> bool:
    production_files = [path for path in files if not path.startswith("worlds/kh3/test/")]
    if not production_files:
        print("No changed KH3 production files require private-delegate checks.")
        return True

    head_sources = python_sources_at_ref(head)
    base_sources = {path: source for path in production_files if (source := source_at_ref(base, path)) is not None}
    try:
        findings = find_new_zero_caller_private_delegates(base_sources, head_sources, production_files)
    except SyntaxError as error:
        annotation(f"Cannot analyze private delegates: {error.msg}", path=error.filename, line=error.lineno)
        return False

    for path, qualified_name, line in findings:
        annotation(
            f"New thin private delegate {qualified_name} has no repository caller; call the service directly or add a caller.",
            path=path,
            line=line,
        )
    if findings:
        print(f"Found {len(findings)} new zero-caller private delegate(s).", file=sys.stderr)
        return False

    print(f"Private-delegate checks passed for {len(production_files)} KH3 production file(s).")
    return True


def run_new_module_checks(added_files: list[str]) -> bool:
    new_modules = [
        path for path in added_files if path.startswith("worlds/kh3/") and not path.startswith("worlds/kh3/test/")
    ]
    success = True

    for path in new_modules:
        source = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            annotation(f"Cannot parse new KH3 module: {error.msg}", path=path, line=error.lineno)
            success = False
            continue

        module_lines = len(source.splitlines())
        if module_lines > MAX_NEW_MODULE_LINES:
            annotation(
                f"New KH3 production modules are limited to {MAX_NEW_MODULE_LINES} lines; found {module_lines}.",
                path=path,
                line=1,
            )
            success = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callable_lines = (node.end_lineno or node.lineno) - node.lineno + 1
                if callable_lines > MAX_NEW_CALLABLE_LINES:
                    annotation(
                        f"New KH3 callables are limited to {MAX_NEW_CALLABLE_LINES} lines; "
                        f"{callable_name(node)} has {callable_lines}.",
                        path=path,
                        line=node.lineno,
                    )
                    success = False
            elif isinstance(node, ast.ClassDef):
                method_count = sum(
                    isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) for statement in node.body
                )
                if method_count > MAX_NEW_CLASS_METHODS:
                    annotation(
                        f"New KH3 classes are limited to {MAX_NEW_CLASS_METHODS} direct methods; "
                        f"{node.name} has {method_count}.",
                        path=path,
                        line=node.lineno,
                    )
                    success = False

    if added_files:
        format_command = [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--config",
            "ruff.toml",
            "--check",
            *added_files,
        ]
        print("Running:", " ".join(format_command))
        format_result = subprocess.run(format_command, cwd=REPOSITORY_ROOT)
        success = format_result.returncode == 0 and success

    if new_modules:
        complexity_command = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "C901",
            "--config",
            "lint.mccabe.max-complexity=10",
            "--output-format",
            "github",
            *new_modules,
        ]
        print("Running:", " ".join(complexity_command))
        complexity_result = subprocess.run(complexity_command, cwd=REPOSITORY_ROOT)
        success = complexity_result.returncode == 0 and success

    if not added_files:
        print("No newly added KH3 Python files require format or structural checks.")
    elif success:
        print(
            f"New-file checks passed ({len(added_files)} formatted file(s), {len(new_modules)} production module(s))."
        )
    return success


def lint_command(arguments: argparse.Namespace) -> int:
    files = changed_python_files(arguments.base, arguments.head)
    added_files = changed_python_files(arguments.base, arguments.head, added_only=True)
    print("Changed KH3 Python files:", *(files or ["(none)"]), sep="\n  ")
    changed_line_success = run_changed_line_ruff(arguments.base, arguments.head, files)
    new_module_success = run_new_module_checks(added_files)
    private_delegate_success = run_private_delegate_checks(arguments.base, arguments.head, files)
    return 0 if changed_line_success and new_module_success and private_delegate_success else 1


def failing_cases(path: Path) -> set[str]:
    if not path.is_file():
        raise ValueError(f"pytest JUnit report does not exist: {path}")
    root = ElementTree.parse(path).getroot()
    testcases = list(root.iter("testcase"))
    if not testcases:
        raise ValueError(f"pytest JUnit report contains no test cases: {path}")
    return {
        f"{case.get('classname', '<unknown>')}::{case.get('name', '<unknown>')}"
        for case in testcases
        if case.find("failure") is not None or case.find("error") is not None
    }


def compare_pytest_command(arguments: argparse.Namespace) -> int:
    try:
        base_failures = failing_cases(arguments.base_results)
        head_failures = failing_cases(arguments.head_results)
    except (ElementTree.ParseError, OSError, ValueError) as error:
        annotation(str(error))
        return 1

    new_failures = sorted(head_failures - base_failures)
    if new_failures:
        annotation(
            "KH3 pytest introduced failures that are not present at the pull-request base:\n"
            + "\n".join(f"- {failure}" for failure in new_failures)
        )
        return 1

    print(
        f"KH3 pytest non-regression passed: {len(head_failures)} head failure(s), "
        f"{len(base_failures)} base failure(s), 0 newly introduced."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint = subparsers.add_parser("lint", help="Lint changed lines and check newly added modules.")
    lint.add_argument("--base", required=True, help="Pull-request base commit.")
    lint.add_argument("--head", required=True, help="Pull-request head commit.")
    lint.set_defaults(function=lint_command)

    compare = subparsers.add_parser("compare-pytest", help="Reject pytest failures absent from the base run.")
    compare.add_argument("--base-results", required=True, type=Path)
    compare.add_argument("--head-results", required=True, type=Path)
    compare.set_defaults(function=compare_pytest_command)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    return arguments.function(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
