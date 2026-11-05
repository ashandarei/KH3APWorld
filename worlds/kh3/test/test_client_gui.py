from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def run_isolated_python(source: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SKIP_REQUIREMENTS_UPDATE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_client_gui_import_does_not_require_gui_dependencies() -> None:
    result = run_isolated_python(
        """
        import sys

        class RejectGuiImports:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "kvui" or fullname.startswith(("kivy", "kivymd")):
                    raise ImportError(f"unexpected GUI dependency import: {fullname}")
                return None

        sys.meta_path.insert(0, RejectGuiImports())
        import worlds.kh3.ClientGui as client_gui
        import worlds.kh3.client_support.presentation as presentation

        assert callable(client_gui.run_kh3_gui)
        assert presentation.ClientStatusSnapshot
        assert not any(
            name == "kvui" or name.startswith(("kivy", "kivymd"))
            for name in sys.modules
        )
        print("KH3_CLIENT_GUI_HEADLESS_IMPORT_OK")
        """
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "KH3_CLIENT_GUI_HEADLESS_IMPORT_OK" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows client UI smoke test")
def test_client_save_cleanup_confirmation_and_junction_guard() -> None:
    result = run_isolated_python(
        """
        import asyncio
        import _winapi
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from worlds.kh3.Client import KH3Context
        from worlds.kh3.gui.manager import KH3Manager

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        async def smoke(root):
            saves = root / "SaveGames"
            native = saves / "kh3sv2/data/KHIII_slot0.bin"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"synthetic native")
            sidecar = saves / "RandomizerEvents.sav"
            sidecar.write_bytes(b"synthetic sidecar")
            kept = []
            for name in ("other.sav", "unrelated.bin", "ModLoaderSaves.sav", "backup/KHIII_slot0.bin"):
                path = saves / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"keep")
                kept.append(path)
            # Use the actual context and Tk frontend, with every filesystem path
            # isolated and no server, game launch, or background bridge/status loop.
            ctx = KH3Context(None, None, bridge_root=str(root / "bridge"), integrated_bridge=False,
                kh3_dir=str(root / "game"), save_dir=str(saves),
                ue4ss_log=str(root / "UE4SS.log"), probe_config=str(root / "probe.json"),
                kh3_mods_dir=str(root / "mods"))
            app = KH3Manager(ctx)
            ctx.ui = app
            view = app.views["KH3 Config"]

            async def until(predicate):
                for _ in range(500):
                    app.root.update()
                    if predicate():
                        return
                    await asyncio.sleep(.01)
                raise AssertionError("Client UI action timed out")

            def dialog_text():
                dialog = next(iter(app.dialogs))
                return "\\n".join(str(w.cget("text")) for w in descendants(dialog) if "text" in w.keys())

            def click(text):
                dialog = next(iter(app.dialogs))
                next(w for w in descendants(dialog)
                    if w.winfo_class() == "TButton" and w.cget("text") == text).invoke()

            try:
                assert view.paths["Save Dir"].get() == str(saves)
                view.delete_saves_button.invoke()
                await until(lambda: bool(app.dialogs))
                assert str(saves) in dialog_text()
                assert "Unrecognized files and folders" in dialog_text()
                click("Cancel")
                await until(lambda: not app.tasks)
                assert native.exists() and sidecar.exists()

                view.delete_saves_button.invoke()
                await until(lambda: bool(app.dialogs))
                click("Delete Save Files")
                await until(lambda: bool(app.dialogs) and "Deleted 2 save files" in dialog_text())
                assert not native.exists() and not sidecar.exists()
                assert all(path.read_bytes() == b"keep" for path in kept)
                click("OK")
                await until(lambda: not app.tasks)

                # Redirect the configured root after startup: the UI must not
                # resolve away the junction before applying the shared guard.
                saves.rename(root / "retained")
                outside = root / "outside"
                outside.mkdir()
                external = outside / "KHIII_slot0.bin"
                external.write_bytes(b"outside")
                _winapi.CreateJunction(str(outside), str(saves))
                try:
                    view.delete_saves_button.invoke()
                    await until(lambda: bool(app.dialogs))
                    assert "Could Not Delete Save Files" in dialog_text()
                    assert "redirected" in dialog_text()
                    assert external.read_bytes() == b"outside"
                    click("OK")
                    await until(lambda: not app.tasks)
                finally:
                    saves.rmdir()
            finally:
                app.stop()
                ctx.keep_alive_task.cancel()
                await asyncio.gather(ctx.keep_alive_task, return_exceptions=True)

        with TemporaryDirectory() as temporary, \\
             patch("Utils.persistent_load", return_value={}), \\
             patch("Utils.persistent_store"), \\
             patch.object(KH3Context, "warn_about_steam_cloud_if_needed"), \\
             patch("worlds.kh3.platform_support.process_status.is_process_running", return_value=False):
            asyncio.run(smoke(Path(temporary).resolve()))
        print("KH3_CLIENT_SAVE_CLEANUP_UI_OK")
        """
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "KH3_CLIENT_SAVE_CLEANUP_UI_OK" in result.stdout
