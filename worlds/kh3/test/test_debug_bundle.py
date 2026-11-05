"""Debug export retains recovery evidence without consuming live pipe traffic."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from ..bridge_support.checkpoint import Checkpoint
from ..bridge_support.diagnostic_history import history_for
from ..client_support import debug_bridge_snapshot as capture
from ..client_support import debug_bundle as bundle
from ..client_support.debug_kh02 import capture_movie_evidence
from ..kh02.installation import EXE as KH02_EXE
from ..transport.pipe_transport import ACK, SET, SNAPSHOTS, register, unregister


@pytest.fixture
def export(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "_collect_kh3_crash_artifacts", lambda *a, **kw: [])
    monkeypatch.setattr(bundle, "_kh3_process_summary", lambda: {"running": False})
    monkeypatch.setattr(bundle, "snapshot_uobject_pool", lambda *a: {"status": "not_running"})
    monkeypatch.setattr(bundle.Utils, "user_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    monkeypatch.setattr(bundle, "_client_log_files", lambda: [])

    def run(bridge, **options):
        result = bundle.export_kh3_debug_bundle(
            kh3_dir=tmp_path / "game", bridge_dir=bridge,
            kh3_mods_dir=tmp_path / "mods", save_dir=tmp_path / "saves",
            ue4ss_log=None, output_dir=tmp_path / "exports", **options)
        with ZipFile(result["bundle_path"]) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    return run


def ledger_with_journal(root):
    root.mkdir(exist_ok=True)
    path = root / "ap_items.json"
    path.write_text(json.dumps({"items": {}, "padding": "x" * 70000}), encoding="utf-8")
    checkpoint = Checkpoint(path)
    entry = {"index": 0, "status": "applied", "item_name": "Potion"}
    payload = {"items": {"0": entry}, "updated_at_utc": "2026-09-19T12:00:00Z", "padding": "x" * 70000}
    checkpoint.write(json.dumps(payload, separators=(",", ":")).encode())
    return checkpoint, payload


def test_player_movie_failure_export_retains_provider_reason_and_redacts(tmp_path, export):
    win64 = tmp_path / "game/Binaries/Win64"
    provider = tmp_path / "source/Binaries/Win64"
    win64.mkdir(parents=True)
    (provider / "kh02_bridge").mkdir(parents=True)
    (win64 / "kh02_portal_config.json").write_text(json.dumps({
        "source_executable": str(provider / KH02_EXE), "source_platform": "steam"}))
    failure = {"schema": "kh3ap.kh02_movie_failure.v1", "source_pid": 45,
               "control_request_id": "17", "movie": "Rebellis02/ja/cs_dw025a_mv.usm",
               "source_state": 5, "error": "movie_bridge_source_failed"}
    (win64 / "kh02_movie_failure.json").write_text(json.dumps(failure))
    result = {"pid": 45, "id": "17", "status": "failed", "error": "movie_file_size",
              "AUTH_PASSWORD": "must-never-leak"}
    source_result = provider / "kh02_bridge/movie_probe_result.json"
    source_result.write_text(json.dumps(result))
    (provider / "kh02_bridge/unrelated-secret.json").write_text('"must-not-collect"')
    before = source_result.read_bytes()
    archive = export(None)
    assert json.loads(archive["kh02/host_movie_failure.json"]) == failure
    captured = json.loads(archive["kh02/provider_movie_result.json"])
    assert captured["error"] == "movie_file_size"
    assert b"must-never-leak" not in archive["kh02/provider_movie_result.json"]
    assert not any("unrelated-secret" in name for name in archive)
    assert source_result.read_bytes() == before
    summary = json.loads(archive["debug_summary.json"])["kh02_movie_evidence"]
    assert summary["provider_result_matches_failure"] is True
    # A subsequent movie must not be misattributed to the retained host failure.
    result["id"] = "18"
    source_result.write_text(json.dumps(result))
    assert capture_movie_evidence(win64)[0]["provider_result_matches_failure"] is False


def test_movie_evidence_is_bounded_and_tolerates_missing_or_malformed_files(tmp_path):
    summary, files = capture_movie_evidence(tmp_path)
    assert files == {}
    assert summary["files"]["kh02/host_movie_failure.json"]["status"] == "missing"
    (tmp_path / "kh02_movie_failure.json").write_bytes(b"x" * (256 * 1024 + 1))
    (tmp_path / "kh02_portal_config.json").write_text("{")
    summary, files = capture_movie_evidence(tmp_path)
    assert not files
    assert summary["files"]["kh02/host_movie_failure.json"]["status"] == "too_large"
    assert summary["files"]["kh02/portal_config.json"]["status"] == "unavailable"


@pytest.mark.parametrize("registered", [False, True])
def test_offline_export_recovers_journal_and_marks_legacy_mailboxes(tmp_path, export, registered):
    root = tmp_path / "seed_Player"
    checkpoint, expected = ledger_with_journal(root)
    (root / "level_up_progress.json").write_text(json.dumps({
        "schema": "kh3ap.level_up_progress.v1", "owner": "dll", "seed_name": "seed", "slot": 1,
        "highest_level": 2, "completed": ["2"], "record_count": 1, "complete": True,
    }))
    (root / "runtime_context.json").write_text('{"map":"old"}')
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    pipe = register(root) if registered else None
    try:
        archive = export(root)
        state = json.loads(archive["pipe/transport_snapshot.json"])
        assert state["status"] == ("disconnected" if registered else "unavailable")
        if not registered:
            assert state["reason"] == "no_registered_session"
        assert "runtime_context.json" not in state.get("channels", {})
        summary = json.loads(archive["debug_summary.json"])
        assert summary["legacy_disk_mailboxes"] == ["runtime_context.json"]
        details = summary["bridge_capture"]["files"][checkpoint.path.name]
        assert details["status"] == "recovered"
        assert details["recovered_records"] == 1
        assert json.loads(archive[details["recovered_archive"]]) == expected
        for name, value in before.items():
            if name.endswith(".wal"):
                assert f"bridge/{root.name}/{name}" not in archive
            else:
                assert archive[f"bridge/{root.name}/{name}"] == value
        assert details["raw_journals_exported"] is False
        assert before == {path.name: path.read_bytes() for path in root.iterdir()}
    finally:
        if pipe:
            unregister(pipe)


def test_connected_capture_includes_all_pending_receipts_without_ack_or_disk_io(tmp_path, monkeypatch):
    pipe = register(tmp_path)
    try:
        pipe.connected, pipe.error, pipe.epoch = True, "", 123
        pipe.write("runtime_item_grant.json", {"request_id": "grant-7"})
        pipe.accept([(SET, "runtime_context.json", 1, b'{"map":"Toy Box"}'),
                     (SET, "runtime_receipt_ack_event.json", 2, b'{"request_id":"grant-5"}'),
                     (SET, "runtime_receipt_ack_event.json", 3, b'{"request_id":"grant-6"}')])
        pipe.acks.append((ACK, "runtime_loot_event.json", 9, b""))
        for i in range(100):
            pipe.history.append("runtime_fight_events.jsonl", {"index": i})
        pending, acks = dict(pipe.pending), list(pipe.acks)
        before = list(pipe.events["runtime_receipt_ack_event.json"])
        with monkeypatch.context() as guards:
            guards.setattr(Path, "open", Mock(side_effect=AssertionError("Snapshot must not use disk")))
            state = pipe.diagnostic_snapshot()
        assert state["status"] == "connected"
        assert state["channels"]["runtime_context.json"]["payload"]["map"] == "Toy Box"
        assert state["pending_outbound"][0]["payload"]["request_id"] == "grant-7"
        assert [r["revision"] for r in state["unacknowledged_events"]["runtime_receipt_ack_event.json"]] == [2, 3]
        assert state["pending_acknowledgments"][0]["revision"] == 9
        tail = state["journal_tails"]["runtime_fight_events.jsonl"]
        assert tail["retained_count"] == 100 and tail["exported_count"] == 64
        assert tail["payloads"][0] == {"index": 36}
        assert pipe.pending == pending and pipe.acks == acks
        assert list(pipe.events["runtime_receipt_ack_event.json"]) == before
        state["channels"]["runtime_context.json"]["payload"]["map"] = "changed"
        assert pipe.read("runtime_context.json")["map"] == "Toy Box"
    finally:
        unregister(pipe)


@pytest.mark.parametrize("closed", [False, True])
def test_disconnected_export_keeps_context_only_as_historical(tmp_path, export, closed):
    root = tmp_path / "seed"
    root.mkdir()
    pipe = register(root)
    try:
        pipe.epoch = 42
        pipe.accept([(SET, "runtime_context.json", 1, b'{"map":"Toy Box"}')])
        if closed:
            pipe.close()
        else:
            with pipe.lock:
                pipe._retain_disconnected_snapshots()
                for name in SNAPSHOTS:
                    pipe.values.pop(name, None)
                pipe._retain_disconnected_snapshots()  # another failed retry must preserve it
        archive = export(root)
        state = json.loads(archive["pipe/transport_snapshot.json"])
        assert state["status"] == ("closed" if closed else "disconnected")
        assert "runtime_context.json" not in state["channels"]
        old = state["last_disconnected_runtime_snapshots"]
        assert old["status"] == "historical_not_live" and old["epoch"] == 42
        assert old["channels"]["runtime_context.json"]["payload"] == {"map": "Toy Box"}
        assert pipe.read("runtime_context.json") is None
    finally:
        unregister(pipe)


def test_export_without_bridge_or_game_succeeds(export):
    archive = export(None)
    assert json.loads(archive["pipe/transport_snapshot.json"])["reason"] == "no_bridge_directory"
    assert json.loads(archive["debug_summary.json"])["bridge_capture"]["files"] == {}


def test_export_materializes_memory_histories_and_current_receipts_only_on_demand(tmp_path, export):
    root = tmp_path / "seed"
    root.mkdir()
    # A stale disk projection must not override the newer native IPC snapshot.
    (root / "item_delivery_ledger.json").write_text('{"items":{}}')
    pipe = register(root)
    try:
        name = "current_state_write_polyhook_events.jsonl"
        pipe.accept([(SET, name, 1, b'{"state_class":"GameOver"}'),
                     (SET, "item_delivery_ledger.json", 2,
                      b'{"items":{"0":{"index":0,"status":"applied","item_name":"Potion"}}}')])
        history_for(root).append("location_check_transport.jsonl", {"event": "confirmed"})
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        archive = export(root)
        assert json.loads(archive[f"diagnostics/seed/{name}"])["state_class"] == "GameOver"
        assert json.loads(archive["diagnostics/seed/location_check_transport.jsonl"])["event"] == "confirmed"
        assert json.loads(archive["bridge-state/seed/item_delivery_ledger.json"])["items"]["0"]["item_name"] == "Potion"
        summary = json.loads(archive["debug_summary.json"])
        assert summary["item_delivery_ledger"]["total_count"] == 1
        assert summary["bridge_capture"]["files"]["item_delivery_ledger.json"]["source"] == "pipe_snapshot"
        assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    finally:
        unregister(pipe)


def test_local_socket_credentials_are_not_exported(tmp_path, export):
    root = tmp_path / "seed"
    root.mkdir()
    (root / "ipc_endpoint.json").write_text('{"token":"private-token"}')
    (root / "ipc_endpoint.lock").touch()
    archive = export(root)
    assert not any(name.endswith(("ipc_endpoint.json", "ipc_endpoint.lock")) for name in archive)
    assert all(b"private-token" not in value for value in archive.values())


def test_default_export_never_discovers_or_copies_crashes(tmp_path, monkeypatch, export):
    discover = Mock(side_effect=AssertionError("Default export must not scan crash directories"))
    monkeypatch.setattr(bundle, "_collect_kh3_crash_artifacts", discover)
    root = tmp_path / "seed"
    root.mkdir()
    # Also reject dumps and arbitrary attachments misplaced in the bridge tree.
    (root / "unexpected.dmp").write_bytes(b"MDMPprivate-memory")
    (root / "attachment.zip").write_bytes(b"private-archive")
    (root / "opaque.log").write_bytes(b"\x00private-memory")
    archive = export(root)
    discover.assert_not_called()
    assert not any(name.endswith((".dmp", ".zip", "opaque.log")) for name in archive)
    assert not any(b"private-" in data for data in archive.values())
    summary = json.loads(archive["debug_summary.json"])
    assert summary["crash_reports"] == summary["crash_dumps"] == []
    assert not summary["privacy"]["crash_artifacts_opt_in"]
    assert len(summary["privacy"]["omitted_files"]) == 3


def test_opt_in_preserves_dump_bytes_but_redacts_crash_text(tmp_path, monkeypatch, export):
    report = tmp_path / "UE4CC-test"
    report.mkdir()
    dump = report / "UE4Minidump.dmp"
    dump.write_bytes(b"MDMPprivate-memory")
    context = report / "CrashContext.runtime-xml"
    context.write_text('<Crash><CommandLine>-AUTH_PASSWORD=private-token -EpicPortal</CommandLine></Crash>')
    monkeypatch.setattr(bundle, "_collect_kh3_crash_artifacts", lambda *a, **kw: [
        {"kind": "unreal_report", "path": report, "files": [dump, context]}])
    archive = export(None, include_crash_dumps=True)
    assert archive["crashes/unreal/UE4CC-test/UE4Minidump.dmp"] == dump.read_bytes()
    assert b"private-token" not in archive["crashes/unreal/UE4CC-test/CrashContext.runtime-xml"]
    assert "private-token" in context.read_text()
    summary = json.loads(archive["debug_summary.json"])
    assert summary["privacy"]["raw_dumps_are_unredacted"]
    assert summary["privacy"]["sharing"] == "private_support_only"


def test_crash_collection_uses_local_day_and_file_dates_not_directory_dates(tmp_path, monkeypatch):
    local_zone = timezone(timedelta(hours=-6))

    class LocalClock(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 11, 6, 12)

        @classmethod
        def fromtimestamp(cls, timestamp):
            return datetime.fromtimestamp(timestamp, local_zone).replace(tzinfo=None)

    monkeypatch.setattr(bundle, "datetime", LocalClock)
    midnight = datetime(2026, 11, 6, tzinfo=local_zone).timestamp()
    game = tmp_path / "game"
    win64 = game / "Binaries" / "Win64"
    win64.mkdir(parents=True)
    local_appdata = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    def write(path, timestamp):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        os.utime(path, (timestamp, timestamp))

    for name, timestamp in (("yesterday", midnight - 1), ("midnight", midnight),
                            ("today", midnight + 60), ("late", midnight + 86399),
                            ("tomorrow", midnight + 86400)):
        write(win64 / f"crash_{name}.dmp", timestamp)
    write(local_appdata / "CrashDumps" / "KH3today.dmp", midnight + 100)
    reports = local_appdata / "KINGDOM HEARTS III" / "Saved" / "Crashes"
    old_report = reports / "old"
    write(old_report / "UE4Minidump.dmp", midnight - 1)
    os.utime(old_report, (midnight + 10, midnight + 10))
    mixed_report = reports / "mixed"
    write(mixed_report / "UE4Minidump.dmp", midnight - 1)
    write(mixed_report / "CrashContext.runtime-xml", midnight + 10)
    artifacts = bundle._collect_kh3_crash_artifacts(game, max_crash_dumps=None)
    files = {path for artifact in artifacts for path in artifact["files"]}
    assert files == {win64 / "crash_midnight.dmp", win64 / "crash_today.dmp", win64 / "crash_late.dmp",
                     local_appdata / "CrashDumps" / "KH3today.dmp", mixed_report / "CrashContext.runtime-xml"}
    assert len(artifacts) > 2
    assert bundle._collect_kh3_crash_artifacts(game, max_crash_dumps=2) == artifacts[:2]


def test_all_text_export_routes_redact_without_mutating_sources(tmp_path, monkeypatch, export):
    root = tmp_path / "seed"
    checkpoint, _ = ledger_with_journal(root)
    payload = {"status": "failed", "password": "private-password", "EpicAccountId": "private-account",
               "path": r"C:\Users\Private Person\game\main.dll"}
    encoded = json.dumps(payload)
    checkpoint.write(encoded.encode())
    (root / "arbitrary.json").write_text(encoded)
    client_log = tmp_path / "client.log"
    client_log.write_text('-AUTH_PASSWORD=private-command -AUTH_TYPE=exchangecode\n')
    monkeypatch.setattr(bundle, "_client_log_files", lambda: [client_log])
    monkeypatch.setattr(bundle, "_kh3_process_summary", lambda: payload)
    win64 = tmp_path / "game" / "Binaries" / "Win64"
    win64.mkdir(parents=True)
    (win64 / "UE4SS.log").write_text(encoded)
    (win64 / "UE4SS-settings.ini").write_text('token=private-config\n')
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "ChoicesMade.sav").write_text(encoded)
    (saves / "PlayerStart.sav").write_bytes(b"\x00private-opaque-save")
    pipe = register(root)
    try:
        pipe.accept([(SET, "runtime_context.json", 1, encoded.encode())])
        history_for(root).append("location_check_transport.jsonl", payload)
        before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        archive = export(root)
        for data in archive.values():
            assert b"private-" not in data and b"Private Person" not in data
        assert json.loads(archive["bridge-state/seed/ap_items.json"])["status"] == "failed"
        assert json.loads(archive["pipe/transport_snapshot.json"])["channels"]["runtime_context.json"]["payload"]["status"] == "failed"
        assert "logs/UE4SS.log" in archive and "logs/client/1-client.log" in archive
        assert "runtime/Win64/UE4SS-settings.ini" in archive
        assert "diagnostics/seed/location_check_transport.jsonl" in archive
        assert "randomizer-saves/ChoicesMade.sav" in archive
        assert "randomizer-saves/PlayerStart.sav" not in archive
        assert {path: path.read_bytes() for path in before} == before
        assert pipe.read("runtime_context.json")["password"] == "private-password"
    finally:
        unregister(pipe)


@pytest.mark.parametrize("damage", ["torn", "corrupt", "stale", "orphan"])
def test_export_reports_damaged_journal_without_exporting_raw_bytes(tmp_path, export, damage):
    root = tmp_path / "seed"
    checkpoint, expected = ledger_with_journal(root)
    data = checkpoint.wal.read_bytes()
    if damage == "torn":
        checkpoint.wal.write_bytes(data + b"\x10\x00")
    elif damage == "corrupt":
        checkpoint.wal.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif damage == "stale":
        checkpoint.path.write_text('{"items":{},"reset":true}')
    else:
        checkpoint.path.unlink()
    archive = export(root)
    summary = json.loads(archive["debug_summary.json"])
    details = summary["bridge_capture"]["files"][checkpoint.path.name]
    assert f"bridge/seed/{checkpoint.wal.name}" not in archive
    assert details["raw_journals_exported"] is False
    if damage in {"corrupt", "orphan"}:
        assert details["status"] == ("invalid" if damage == "corrupt" else "orphan_journal")
        assert "recovered_archive" not in details
    else:
        assert details["journal_status"] == ("incomplete_tail_ignored" if damage == "torn" else "stale_ignored")
        assert json.loads(archive[details["recovered_archive"]]) == (expected if damage == "torn" else {"items": {}, "reset": True})


@pytest.mark.parametrize("always_changing", [False, True])
def test_checkpoint_capture_retries_racing_writer_and_bounds_attempts(tmp_path, monkeypatch, always_changing):
    path = tmp_path / "ap_status.json"
    path.write_bytes(b'{"revision":0}')
    original = Path.read_bytes
    count = 0

    def read(candidate):
        nonlocal count
        value = original(candidate)
        if candidate == path:
            count += 1
            if always_changing or count == 1:
                candidate.write_text(json.dumps({"revision": count, "padding": "x" * count}))
        return value

    monkeypatch.setattr(Path, "read_bytes", read)
    raw, value, details = capture._capture_checkpoint(tmp_path, path.name)
    assert count == (3 if always_changing else 2)
    if always_changing:
        assert value is None and details["status"] == "unavailable"
        assert "changed during capture" in details["error"]
    else:
        assert value == raw[path.name] == original(path)
        assert details["status"] == "recovered"


def test_zip_uses_captured_base_and_journal_after_disk_replacement(tmp_path, monkeypatch, export):
    root = tmp_path / "seed"
    checkpoint, expected = ledger_with_journal(root)
    capture_original = bundle.capture_bridge_debug_state

    def racing_capture(bridge):
        result = capture_original(bridge)
        checkpoint.compact(b'{"items":{},"new":true}')
        return result

    monkeypatch.setattr(bundle, "capture_bridge_debug_state", racing_capture)
    archive = export(root)
    prefix = f"bridge/{root.name}/{checkpoint.path.name}"
    assert prefix + ".wal" not in archive
    assert json.loads(archive[prefix])["items"] == {}
    assert "new" not in json.loads(archive[prefix])
    assert json.loads(archive[f"bridge-state/{root.name}/{checkpoint.path.name}"]) == expected


def test_client_logs_include_active_and_recent_restart_context(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.Utils, "user_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for i in range(4):
        (log_dir / f"KH3Client_{i}.txt").write_text(str(i))
    (log_dir / "unrelated.txt").write_text("unrelated")
    active = tmp_path / "KH3Client_active.txt"
    handler = logging.FileHandler(active)
    monkeypatch.setattr(bundle.logger, "handlers", [handler])
    try:
        handler.emit(logging.LogRecord("Client", logging.INFO, "", 1, "delivery retained", (), None))
        files = bundle._client_log_files()
        assert active in files and len(files) == 4
        assert "delivery retained" in active.read_text()
        assert all(path.name.startswith("KH3Client") for path in files)
    finally:
        handler.close()
