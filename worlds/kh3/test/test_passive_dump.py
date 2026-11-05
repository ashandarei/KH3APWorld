"""Crash capture is explicit, packaged, and never silently exports a missing dump."""

import asyncio
import json
import subprocess
import sys
import threading
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest

from ..client_support import debug_bundle as bundle
from ..client_support import passive_dump as capture
from ..client_support.debug_redaction import DebugBundleArchive, DebugRedactor
from ..gui.config import ConfigView


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "get_process_status", Mock(return_value={"running": True, "pid": 123}))


def test_capture_requires_windows_and_a_live_game(tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr(capture.subprocess, "run", run)
    monkeypatch.setattr(capture.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Windows only"):
        capture.capture_kh3_passive_dump(tmp_path, tmp_path)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "get_process_status", lambda _: {"running": False})
    with pytest.raises(RuntimeError, match="not running"):
        capture.capture_kh3_passive_dump(tmp_path, tmp_path)
    run.assert_not_called()


def test_capture_uses_packaged_script_and_literal_paths(tmp_path, monkeypatch, windows):
    output = tmp_path / "captured"
    game = tmp_path / "game with spaces & symbols"
    observed = []

    def run(args, **kwargs):
        script = Path(args[args.index("-File") + 1])
        observed.append(script)
        assert script.read_bytes() == resources.files(capture.__package__).joinpath(
            "capture-kh3-passive-dump.ps1").read_bytes()
        assert args[args.index("-GameProcessId") + 1] == "123"
        assert args[args.index("-ExpectedExecutablePath") + 1] == str(
            game / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe")
        assert kwargs["creationflags"] == 0x08000000
        assert kwargs["timeout"] == 120
        output.mkdir()
        path = output / "fresh.dmp"
        path.write_bytes(b"MDMPtest")
        return subprocess.CompletedProcess(args, 0, json.dumps({"path": str(path), "size": 8}).encode(), b"")

    monkeypatch.setattr(capture.subprocess, "run", run)
    result = capture.capture_kh3_passive_dump(game, output)
    assert Path(result["path"]).read_bytes() == b"MDMPtest"
    assert not observed[0].exists()


@pytest.mark.parametrize("failure", ["helper", "timeout", "missing", "invalid", "outside"])
def test_capture_does_not_report_failed_or_invalid_dumps(tmp_path, monkeypatch, windows, failure):
    outside = tmp_path.parent / "unexpected.dmp"
    path = tmp_path / "capture.dmp"
    if failure == "invalid":
        path.write_bytes(b"not a dump")
    result = subprocess.CompletedProcess([], 0, json.dumps({
        "path": str(outside if failure == "outside" else path), "size": 10}).encode(), b"")
    if failure == "helper":
        result = subprocess.CompletedProcess([], 1, b"", b"Process executable does not match")
    run = Mock(return_value=result)
    if failure == "timeout":
        run.side_effect = subprocess.TimeoutExpired([], 120)
    monkeypatch.setattr(capture.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        capture.capture_kh3_passive_dump(tmp_path, tmp_path)


def test_export_includes_fresh_capture_and_retains_raw_dump(tmp_path, monkeypatch):
    dump = tmp_path / "fresh.dmp"
    dump.write_bytes(b"MDMPcurrent")
    details = {"path": str(dump), "size": 11, "pid": 123, "method": "passive_minidump"}
    helper = Mock(return_value=details)
    monkeypatch.setattr(capture, "capture_kh3_passive_dump", helper)
    monkeypatch.setattr(bundle, "_collect_kh3_crash_artifacts", Mock(side_effect=AssertionError("Old dumps")))
    monkeypatch.setattr(bundle, "_kh3_process_summary", lambda: {"running": True, "pid": 123})
    monkeypatch.setattr(bundle, "snapshot_uobject_pool", lambda _: {"status": "unavailable"})
    monkeypatch.setattr(bundle, "_client_log_files", lambda: [])
    result = bundle.export_kh3_debug_bundle(
        kh3_dir=tmp_path / "game", bridge_dir=None, kh3_mods_dir=tmp_path / "mods",
        save_dir=tmp_path / "saves", ue4ss_log=None, output_dir=tmp_path / "exports",
        capture_passive_dump=True)
    with ZipFile(result["bundle_path"]) as archive:
        assert archive.read("crashes/fresh.dmp") == b"MDMPcurrent"
        summary = json.loads(archive.read("debug_summary.json"))
        assert summary["passive_dump"]["size"] == details["size"]
        assert summary["passive_dump"]["path"].endswith("fresh.dmp")
        assert summary["privacy"]["sharing"] == "private_support_only"
        assert len(summary["crash_dumps"]) == 1
    assert dump.exists()
    assert result["crash_dump_count"] == 1


def test_required_dump_write_failure_is_not_silently_skipped(tmp_path):
    with ZipFile(tmp_path / "test.zip", "w") as archive:
        with pytest.raises(OSError):
            bundle._write_crash_artifacts(DebugBundleArchive(archive, DebugRedactor(save_dir=tmp_path)),
                [{"kind": "dump", "required": True,
                "files": [tmp_path / "missing.dmp"]}], [])


@pytest.mark.parametrize("fails", [False, True])
def test_button_runs_off_ui_thread_and_restores_state(fails):
    ui_thread = threading.get_ident()
    view = ConfigView.__new__(ConfigView)

    def operation():
        assert threading.get_ident() != ui_thread
        assert view.app.action_running
        if fails:
            raise RuntimeError("capture failed")

    view.ctx = SimpleNamespace(kh3_run_action_running=False, capture_crash_dump=Mock(side_effect=operation))
    view.app = SimpleNamespace(action_running=False, closed=False, form=AsyncMock())
    view.apply = AsyncMock(return_value=True)
    view.capture_dump_button = Mock()
    asyncio.run(view.capture_crash_dump())
    view.ctx.capture_crash_dump.assert_called_once()
    assert not view.app.action_running
    view.capture_dump_button.state.assert_called_with(["!disabled"])
    assert view.app.form.await_count == int(fails)


@pytest.mark.parametrize("include_dumps", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_export_option_is_forwarded_off_ui_thread(include_dumps, fails):
    ui_thread = threading.get_ident()
    view = ConfigView.__new__(ConfigView)

    def operation(**kwargs):
        assert threading.get_ident() != ui_thread
        assert kwargs == {"include_crash_dumps": include_dumps}
        if fails:
            raise RuntimeError("export failed")

    view.ctx = SimpleNamespace(kh3_run_action_running=False, export_debug_bundle=Mock(side_effect=operation))
    view.app = SimpleNamespace(action_running=False, closed=False, form=AsyncMock())
    view.apply = AsyncMock(return_value=True)
    view.debug_export_options = AsyncMock(return_value=include_dumps)
    asyncio.run(view.export_debug_bundle())
    view.ctx.export_debug_bundle.assert_called_once_with(include_crash_dumps=include_dumps)
    view.debug_export_options.assert_awaited_once()
    assert not view.app.action_running
    assert view.app.form.await_count == int(fails)


def test_closing_export_modal_does_not_apply_settings_or_export():
    view = ConfigView.__new__(ConfigView)
    view.app = SimpleNamespace(closed=False)
    view.ctx = SimpleNamespace(export_debug_bundle=Mock())
    view.debug_export_options = AsyncMock(return_value=None)
    view.apply = AsyncMock()
    asyncio.run(view.export_debug_bundle())
    view.apply.assert_not_awaited()
    view.ctx.export_debug_bundle.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows capture helper")
def test_packaged_native_helper_compiles_and_rejects_wrong_opened_process(tmp_path):
    # Exercise the real C# interop on this test's PowerShell process, with an
    # intentionally wrong expected path. No game process is accessed or dumped.
    resource = resources.files(capture.__package__).joinpath("capture-kh3-passive-dump.ps1").read_text()
    csharp = resource.split("Add-Type -TypeDefinition @'", 1)[1].split("'@", 1)[0]
    script = tmp_path / "check.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\nAdd-Type -TypeDefinition @'" + csharp + "'@\n"
        "try { [Kh3PassiveDump]::Capture([uint32]$PID, 'Z:\\not-kh3.exe', 'Z:\\unused.dmp'); exit 2 }\n"
        "catch { if ($_.Exception.ToString() -notmatch 'Opened process does not match') { throw }; exit 0 }\n",
        encoding="utf-8")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(script)], capture_output=True, timeout=30, creationflags=0x08000000)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
