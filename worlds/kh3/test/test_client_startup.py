from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
KH3_WORLD_ROOT = REPOSITORY_ROOT / "worlds" / "kh3"
WORLD_LOAD_PATHS_ENV = "AP_WORLD_LOAD_PATHS"


def test_direct_entrypoint_loads_only_kh3_and_generic_worlds() -> None:
    environment = os.environ.copy()
    environment[WORLD_LOAD_PATHS_ENV] = '["existing-world"]'
    environment["SKIP_REQUIREMENTS_UPDATE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import os

                previous = os.environ.get("AP_WORLD_LOAD_PATHS")
                import KH3Client
                import worlds

                loaded = {source.path for source in worlds.loaded_world_sources}
                assert loaded == {"generic", "kh3"}, loaded
                assert os.environ.get("AP_WORLD_LOAD_PATHS") == previous
                print(next(source.resolved_path for source in worlds.loaded_world_sources if source.path == "kh3"))
                """
            ),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert Path(result.stdout.strip()).resolve() == KH3_WORLD_ROOT.resolve()


@pytest.mark.parametrize("previous_value", [None, '["existing-world"]'])
def test_launcher_scopes_kh3_world_filter_to_child_start(
    monkeypatch: pytest.MonkeyPatch,
    previous_value: str | None,
) -> None:
    import worlds.kh3 as kh3_world

    observed: dict[str, object] = {}

    def capture_launch(function, name: str | None = None, args: tuple[str, ...] = ()) -> None:
        observed["function"] = function
        observed["name"] = name
        observed["args"] = args
        observed["world_load_paths"] = os.environ[WORLD_LOAD_PATHS_ENV]

    monkeypatch.delenv("APPIMAGE", raising=False)
    if previous_value is None:
        monkeypatch.delenv(WORLD_LOAD_PATHS_ENV, raising=False)
    else:
        monkeypatch.setenv(WORLD_LOAD_PATHS_ENV, previous_value)
    monkeypatch.setattr(kh3_world, "launch_component", capture_launch)

    kh3_world.launch_client("--name", "Sora")

    assert observed["name"] == "KH3Client"
    assert observed["args"] == ("--name", "Sora")
    requested = json.loads(str(observed["world_load_paths"]))
    assert [Path(path).resolve() for path in requested] == [KH3_WORLD_ROOT.resolve()]
    if previous_value is None:
        assert WORLD_LOAD_PATHS_ENV not in os.environ
    else:
        assert os.environ[WORLD_LOAD_PATHS_ENV] == previous_value
