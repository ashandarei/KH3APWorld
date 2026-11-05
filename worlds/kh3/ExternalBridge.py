"""External KH3 bridge transport and native-state consumption."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .Bridge import KH3_WORLD_VERSION
from .bridge_support.files import (
    acknowledge_pipe_event,
    atomic_write_json,
    bridge_exists,
    file_signature,
    load_optional_json_file,
    load_optional_json_view,
    safe_unlink,
)
from .bridge_support.paths import (
    AP_ITEMS_FILE,
    BRIDGE_RUNTIME_STATUS_FILE,
    RUNTIME_AP_MESSAGE_FILE,
    SLOT_DATA_FILE,
    bridge_sidecar_path,
)
from .bridge_support.time import parse_utc_datetime
from .BridgeSession import KH3BridgeSession
from .platform_support import process_status as _process_status
from .platform_support.save_paths import default_save_directory, normalize_save_directory
from .RuntimeProfiler import configure_python_profiler, record_python_profile_sample
from .RuntimeReader import KH3RuntimeSavepointReader
from .transport.pipe_transport import transport_for

_subprocess_stdout_text = _process_status._subprocess_stdout_text
get_process_status = _process_status.get_process_status
_linux_boot_time_seconds = _process_status._linux_boot_time_seconds
_linux_process_started_at_utc = _process_status._linux_process_started_at_utc
_get_process_status_linux_proc = _process_status._get_process_status_linux_proc
_get_process_status_windows_api = _process_status._get_process_status_windows_api

KH3_PROCESS_NAME = os.environ.get("KH3_PROCESS_NAME", "KINGDOM HEARTS III.exe")
KH3_PROCESS_STATUS_CACHE_SECONDS = float(os.environ.get("KH3_PROCESS_STATUS_CACHE_SECONDS", "5"))
KH3_BRIDGE_STATUS_HEARTBEAT_INTERVAL_SECONDS = float(
    os.environ.get("KH3_BRIDGE_STATUS_HEARTBEAT_INTERVAL_SECONDS", "10")
)
GARDEN_OF_ASSEMBLAGE_RUNTIME_MAP_NAMES = ("GoA_01", "GoA_02")


def is_process_running(process_name: str) -> bool:
    if os.name != "nt":
        return bool(get_process_status(process_name).get("running"))
    windows_api_status = _get_process_status_windows_api(process_name)
    if windows_api_status is not None:
        return bool(windows_api_status.get("running"))
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process_name.casefold() in _subprocess_stdout_text(result).casefold()


class BridgeBackend(Protocol):
    """Runnable external bridge backend compatibility contract."""

    def run(self, session: KH3BridgeSession, status_writer: "BridgeRuntimeStatusWriter | None" = None) -> int: ...


class BridgeReporter(Protocol):
    """Output boundary used by bridge backends and the interactive console."""

    def write(self, message: object = "") -> None: ...

    def error(self, message: object, *, include_traceback: bool = False) -> None: ...

    def status(self, session: KH3BridgeSession) -> None: ...

    def locations(self, session: KH3BridgeSession, world_name: str | None) -> None: ...

    def pending_items(self, session: KH3BridgeSession) -> None: ...

    def candidate_analyses(self, session: KH3BridgeSession) -> None: ...

    def confirmation_result(self, result: dict[str, object]) -> None: ...


    def pending_action(self, action: dict[str, object], prefix: str = "  next") -> None: ...


def _default_reporter() -> BridgeReporter:
    return _load_external_bridge_cli().ConsoleReporter()


class BridgeRuntimeStatusWriter:
    """Publish compatibility-sensitive lifecycle and process status snapshots."""

    def __init__(
        self,
        session: KH3BridgeSession,
        backend_name: str,
        details: dict[str, Any] | None = None,
    ):
        self.session = session
        self.backend_name = backend_name
        self.details = dict(details or {})
        self.started_at_utc = datetime.now(timezone.utc).isoformat()
        self.path = bridge_sidecar_path(session.bridge_dir, BRIDGE_RUNTIME_STATUS_FILE)
        self._kh3_process_status: dict[str, Any] = {"running": False}
        self._kh3_process_status_checked_at: datetime | None = None
        self._last_write_signature: str | None = None
        self._last_write_monotonic = 0.0

    def _get_cached_kh3_process_status(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if (
            self._kh3_process_status_checked_at is None
            or now - self._kh3_process_status_checked_at >= timedelta(seconds=KH3_PROCESS_STATUS_CACHE_SECONDS)
        ):
            self._kh3_process_status = get_process_status(KH3_PROCESS_NAME)
            self._kh3_process_status_checked_at = now
        return self._kh3_process_status

    def kh3_process_status(self) -> dict[str, Any]:
        return dict(self._get_cached_kh3_process_status())

    def _write(self, status: str, message: str | None = None, *, force: bool = False) -> None:
        kh3_process_status = self._get_cached_kh3_process_status()
        # Wine's Windows PID differs from both the host PID and Flatpak's PID.
        # The authenticated local listener supplies the native identity used by
        # receipt/check snapshots; retain the host identity for process inspection.
        native_pid = kh3_process_status.get("pid")
        transport = transport_for(self.session.bridge_dir / "runtime_reconciliation_state.json")
        if transport is not None and transport.transport == "local_tcp":
            with transport.lock:
                native_pid = transport.server_process_id if transport.connected and not transport.closed else None
        signature = json.dumps(
            {
                "backend": self.backend_name,
                "status": status,
                "message": message,
                "kh3_process_running": bool(kh3_process_status.get("running")),
                "kh3_process_pid": kh3_process_status.get("pid"),
                "kh3_native_process_pid": native_pid,
                "kh3_process_started_at_utc": kh3_process_status.get("started_at_utc"),
                "details": self.details,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        now = time.monotonic()
        if (
            not force
            and signature == self._last_write_signature
            and now - self._last_write_monotonic < KH3_BRIDGE_STATUS_HEARTBEAT_INTERVAL_SECONDS
        ):
            return
        atomic_write_json(
            self.path,
            {
                "protocol_name": self.session.slot_data["bridge"]["protocol_name"],
                "protocol_version": self.session.slot_data["bridge"]["protocol_version"],
                "world_version": KH3_WORLD_VERSION,
                "backend": self.backend_name,
                "status": status,
                "bridge_dir": str(self.session.bridge_dir),
                "pid": os.getpid(),
                "started_at_utc": self.started_at_utc,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "kh3_process_name": KH3_PROCESS_NAME,
                "kh3_process_running": bool(kh3_process_status.get("running")),
                "kh3_process_pid": kh3_process_status.get("pid"),
                "kh3_native_process_pid": native_pid,
                "kh3_process_started_at_utc": kh3_process_status.get("started_at_utc"),
                **self.details,
            },
        )
        self._last_write_signature = signature
        self._last_write_monotonic = now

    def mark_starting(self) -> None:
        self._write("starting", force=True)

    def mark_running(self) -> None:
        self._write("running", force=True)

    def heartbeat(self, message: str | None = None) -> None:
        self._write("running", message)

    def mark_stopped(self) -> None:
        self._write("stopped", force=True)

    def mark_error(self, message: str) -> None:
        self._write("error", message, force=True)


class ManualBackend:
    def __init__(
        self,
        reporter: BridgeReporter | None = None,
        input_reader: Callable[[str], str] = input,
    ):
        self.reporter = reporter or _default_reporter()
        self.input_reader = input_reader

    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        self.reporter.write(
            "KH3 external bridge console. Commands: status, list [world], check <key...>, "
            "goal <complete|incomplete>, clear, items, "
            "candidates, confirm-top <analysis_key>, "
            "confirm <analysis_key> [bridge_key], exit"
        )
        while True:
            if status_writer is not None:
                status_writer.heartbeat("awaiting_manual_command")
            try:
                raw = self.input_reader("kh3-bridge> ").strip()
            except EOFError:
                self.reporter.write()
                return 0

            if not raw:
                continue

            parts = raw.split()
            command = parts[0].lower()
            args = parts[1:]

            try:
                if command in {"exit", "quit"}:
                    return 0
                if command == "status":
                    self.reporter.status(session)
                elif command == "list":
                    self.reporter.locations(session, args[0] if args else None)
                elif command == "check":
                    if not args:
                        self.reporter.write("check requires at least one bridge key or AP location name")
                        continue
                    added = session.add_checks(args)
                    self.reporter.write(f"Added {added} KH3 checks.")
                elif command == "goal":
                    if len(args) != 1 or args[0] not in {"complete", "incomplete"}:
                        self.reporter.write("goal requires 'complete' or 'incomplete'")
                        continue
                    session.set_goal_complete(args[0] == "complete")
                    self.reporter.write(f"Set goal_complete to {args[0] == 'complete'}.")
                elif command == "clear":
                    session.clear_game_state()
                    self.reporter.write("Reset KH3 game_state.json.")
                elif command == "items":
                    self.reporter.pending_items(session)
                elif command == "candidates":
                    self.reporter.candidate_analyses(session)
                elif command == "confirm-top":
                    if len(args) != 1:
                        self.reporter.write("confirm-top requires an analysis key")
                        continue
                    result = session.confirm_treasure_candidate(args[0], None)
                    self.reporter.confirmation_result(result)
                elif command == "confirm":
                    if not args:
                        self.reporter.write("confirm requires an analysis key and optionally a bridge key")
                        continue
                    analysis_key = args[0]
                    bridge_key = args[1] if len(args) > 1 else None
                    result = session.confirm_treasure_candidate(analysis_key, bridge_key)
                    self.reporter.confirmation_result(result)
                else:
                    self.reporter.write(f"Unknown command: {command}")
            except Exception as exc:
                self.reporter.write(exc)


class CommandFileBackend:
    def __init__(self, poll_interval: float = 1.0, reporter: BridgeReporter | None = None):
        self.poll_interval = poll_interval
        self.reporter = reporter or _default_reporter()

    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        commands_path = session.bridge_dir / "bridge_commands.json"
        self.reporter.write(f"Watching {commands_path} for external bridge commands. Press Ctrl+C to stop.")
        last_seq = -1
        try:
            while True:
                if status_writer is not None:
                    status_writer.heartbeat("polling_command_file")
                if commands_path.exists():
                    payload = json.loads(commands_path.read_text(encoding="utf-8"))
                    seq = int(payload.get("seq", 0))
                    if seq > last_seq:
                        self.apply_commands(session, payload.get("commands", []))
                        last_seq = seq
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.reporter.write()
            return 0

    def apply_commands(self, session: KH3BridgeSession, commands: list[dict]) -> None:
        for command in commands:
            name = command.get("cmd")
            if name == "check":
                added = session.add_checks(command.get("locations", []))
                self.reporter.write(f"Added {added} KH3 checks from command file.")
            elif name == "goal":
                complete = bool(command.get("complete"))
                session.set_goal_complete(complete)
                self.reporter.write(f"Set goal_complete to {complete} from command file.")
            elif name == "clear":
                session.clear_game_state()
                self.reporter.write("Reset KH3 game_state.json from command file.")
            elif name == "confirm-top":
                result = session.confirm_treasure_candidate(command["analysis_key"], None)
                self.reporter.confirmation_result(result)
            elif name == "confirm":
                result = session.confirm_treasure_candidate(
                    command["analysis_key"],
                    command.get("bridge_key"),
                )
                self.reporter.confirmation_result(result)


class ObservedStateBackend:
    def __init__(self, poll_interval: float = 1.0, reporter: BridgeReporter | None = None):
        self.poll_interval = poll_interval
        self.reporter = reporter or _default_reporter()

    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        observed_path = session.bridge_dir / "observed_state.json"
        self.reporter.write(f"Watching {observed_path} for observed KH3 state snapshots. Press Ctrl+C to stop.")
        last_seq = -1
        try:
            while True:
                if status_writer is not None:
                    status_writer.heartbeat("polling_observed_state")
                if observed_path.exists():
                    payload = json.loads(observed_path.read_text(encoding="utf-8"))
                    seq = int(payload.get("seq", 0))
                    if seq > last_seq:
                        self.apply_snapshot(session, payload)
                        last_seq = seq
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.reporter.write()
            return 0

    def apply_snapshot(self, session: KH3BridgeSession, payload: dict) -> None:
        changed = session.sync_snapshot(
            payload.get("checked_locations", []),
            bool(payload.get("goal_complete", False)),
        )
        if changed:
            self.reporter.write("Applied observed KH3 state snapshot.")


class SaveFilesBackend:
    """Consume native checks and receipt results and publish AP metadata."""

    def __init__(
        self,
        save_dir: Path,
        poll_interval: float = 1.0,
        stop_event: threading.Event | None = None,
        reporter: BridgeReporter | None = None,
        activity_event: threading.Event | None = None,
    ):
        self.save_dir = normalize_save_directory(save_dir)
        self.configured_save_dir = self.save_dir
        self.poll_interval = poll_interval
        self.stop_event = stop_event
        self.reporter = reporter or _default_reporter()
        self.activity_event = activity_event


    def _should_stop(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def _wait_for_next_poll(self, timeout: float) -> None:
        """Wait for the cadence deadline or a newly published AP item snapshot."""

        timeout = max(0.0, timeout)
        if self.activity_event is not None:
            self.activity_event.wait(timeout)
            self.activity_event.clear()
        elif self.stop_event is not None:
            self.stop_event.wait(timeout)
        else:
            time.sleep(timeout)


    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        configure_python_profiler(session.bridge_dir)
        if status_writer is not None:
            status_writer.details.update(configured_save_dir=str(self.configured_save_dir), save_dir=str(self.save_dir))
        self.reporter.write("Watching DLL-owned KH3 checks and item receipts. Press Ctrl+C to stop.")
        observed = {}

        def consume(channel, action, binding, *dependencies):
            transport = transport_for(session.bridge_dir / channel)
            signature = (binding, tuple(file_signature(session.bridge_dir / name) for name in (channel, *dependencies)))
            if transport is None or observed.get(channel) != signature:
                action(session)
                # Capture the revision before the handler. ACKs can expose the
                # next queued event and must cause another dispatch.
                observed[channel] = signature

        try:
            while not self._should_stop():
                started = time.perf_counter()
                try:
                    if status_writer is not None:
                        status_writer.heartbeat("watching_native_channels")
                    status = load_optional_json_view(session.bridge_dir / BRIDGE_RUNTIME_STATUS_FILE) or {}
                    binding = (status.get("kh3_process_pid"), file_signature(session.bridge_dir / SLOT_DATA_FILE))
                    consume("runtime_receipt_ack_event.json", self._apply_runtime_item_apply_event_if_present, binding)
                    consume("runtime_ap_message_result.json", self._consume_runtime_ap_message_result_if_present, binding)
                    consume("runtime_loot_event.json", self._apply_runtime_loot_event_if_present, binding)
                    consume("runtime_notification_event.json", self._apply_native_notification_if_present, binding)
                    consume("runtime_check_state.json", self._sync_native_checks, binding)
                    consume("runtime_reconciliation_state.json", lambda s: s.sync_native_receipt_catalog(), binding,
                            AP_ITEMS_FILE, "ap_status.json")
                except Exception as exc:
                    self._report_poll_cycle_exception(status_writer, exc)
                finally:
                    record_python_profile_sample("bridge_backend:poll_iteration", (time.perf_counter() - started) * 1000)
                self._wait_for_next_poll(self.poll_interval)
        except KeyboardInterrupt:
            self.reporter.write()
        return 0

    @staticmethod
    def _sync_native_checks(session):
        from .bridge_support.native_check_state import read
        snapshot = read(session)
        if snapshot is not None:
            session.reconcile_snapshot(snapshot["checked_locations"], snapshot["goal_complete"])

    def _report_poll_cycle_exception(self, status_writer, exc):
        message = f"ExternalBridge native-state loop error: {type(exc).__name__}: {exc}"
        if status_writer is not None:
            status_writer.mark_error(message)
        self.reporter.error(message, include_traceback=True)

    def _apply_runtime_loot_event_if_present(self, session):
        # The native worker has already processed the hook. Drain the event
        # transport; only runtime_check_state may update AP check membership.
        path = session.bridge_dir / "runtime_loot_event.json"
        payload = load_optional_json_file(path)
        if payload is not None:
            acknowledge_pipe_event(path, payload)

    @staticmethod
    def _apply_native_notification_if_present(session: KH3BridgeSession) -> None:
        path = session.bridge_dir / "runtime_notification_event.json"
        payload = load_optional_json_file(path)
        if payload is not None:
            session.consume_native_notification_event(payload)
            acknowledge_pipe_event(path, payload)

    def _consume_runtime_ap_message_result_if_present(self, session: KH3BridgeSession) -> None:
        request_path = bridge_sidecar_path(session.bridge_dir, RUNTIME_AP_MESSAGE_FILE)
        result_path = session.bridge_dir / "runtime_ap_message_result.json"
        request_payload = load_optional_json_file(request_path)
        result_payload = load_optional_json_file(result_path)

        if isinstance(request_payload, dict):
            created_at = parse_utc_datetime(request_payload.get("created_at_utc"))
            game_state = session.load_game_state()
            reset_at = parse_utc_datetime(game_state.get("reset_at_utc"))
            if created_at is not None and reset_at is not None and created_at <= reset_at:
                safe_unlink(session.bridge_dir / "runtime_ap_message.json")
                session.append_runtime_grant_profile_event(
                    "runtime_ap_message_discarded",
                    reason="message_created_before_game_state_reset",
                    request_id=request_payload.get("request_id"),
                    bridge_key=request_payload.get("bridge_key"),
                    created_at_utc=request_payload.get("created_at_utc"),
                    game_state_reset_at_utc=game_state.get("reset_at_utc"),
                )
                request_payload = None

        if not isinstance(result_payload, dict):
            return

        request_id = str(result_payload.get("request_id") or "")
        request_status = str(result_payload.get("status") or "")
        current_request_id = (
            str(request_payload.get("request_id") or "")
            if isinstance(request_payload, dict)
            else ""
        )
        if request_id and current_request_id == request_id:
            safe_unlink(session.bridge_dir / "runtime_ap_message.json")
        safe_unlink(session.bridge_dir / "runtime_ap_message_result.json")
        session.append_runtime_grant_profile_event(
            "runtime_ap_message_result_consumed",
            status=request_status,
            request_id=request_id,
            bridge_key=result_payload.get("bridge_key"),
        )


    def _apply_runtime_item_apply_event_if_present(
        self,
        session: KH3BridgeSession,
    ) -> dict[str, Any] | None:
        runtime_item_apply_path = session.bridge_dir / "runtime_receipt_ack_event.json"
        payload = load_optional_json_file(runtime_item_apply_path)
        if payload is None:
            if bridge_exists(runtime_item_apply_path):
                session.append_runtime_grant_profile_event(
                    "runtime_item_apply_event_poll",
                    status="invalid_event",
                    path=str(runtime_item_apply_path),
                )
            return None
        if not isinstance(payload, dict):
            session.append_runtime_grant_profile_event(
                "runtime_item_apply_event_poll",
                status="invalid_event",
                path=str(runtime_item_apply_path),
                payload_type=type(payload).__name__,
            )
            return None
        session.runtime_item_apply_event_key(payload)
        result = session.apply_runtime_item_apply_event(payload)
        if result.get("status") == "duplicate_event":
            acknowledge_pipe_event(runtime_item_apply_path, payload)
            return result
        self.reporter.write(
            "Applied KH3 runtime item event "
            f"indices={result.get('indices') or [result.get('index')]} status={result.get('status')} "
            f"token={result.get('kh3_runtime_token')}"
        )
        for action in result.get("applied_actions", []):
            self.reporter.pending_action(action, prefix="  applied")
        acknowledge_pipe_event(runtime_item_apply_path, payload)
        return result


class RuntimeSavepointBackend:
    def __init__(
        self,
        save_dir: Path,
        process_name: str,
        pid: int | None = None,
        poll_interval: float = 1.0,
        reporter: BridgeReporter | None = None,
    ):
        self.save_dir = save_dir
        self.process_name = process_name
        self.pid = pid
        self.poll_interval = poll_interval
        self.reporter = reporter or _default_reporter()

    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        reader = KH3RuntimeSavepointReader(
            save_dir=self.save_dir,
            process_name=self.process_name,
            pid=self.pid,
        )
        runtime_state_path = session.bridge_dir / "runtime_state.json"
        self.reporter.write(
            f"Watching KH3 runtime savepoint state for {self.process_name if self.pid is None else f'pid {self.pid}'} "
            f"and writing {runtime_state_path}. Press Ctrl+C to stop."
        )
        try:
            while True:
                if status_writer is not None:
                    status_writer.heartbeat("polling_runtime_savepoint")
                snapshot = reader.build_snapshot()
                current = load_optional_json_file(runtime_state_path)
                if current != snapshot:
                    atomic_write_json(runtime_state_path, snapshot)
                    self.reporter.write(
                        f"Resolved KH3 runtime state: {snapshot['active_level_path']} "
                        f"({snapshot['active_player_start']})"
                    )
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.reporter.write()
            return 0


class RuntimeLootEventsBackend:
    def __init__(self, poll_interval: float = 0.5, reporter: BridgeReporter | None = None):
        self.poll_interval = poll_interval
        self.reporter = reporter or _default_reporter()


    def run(self, session: KH3BridgeSession, status_writer: BridgeRuntimeStatusWriter | None = None) -> int:
        return SaveFilesBackend(default_save_directory(), self.poll_interval, reporter=self.reporter).run(session, status_writer)


def _load_external_bridge_cli():
    from . import external_bridge_cli

    return external_bridge_cli


def print_status(session: KH3BridgeSession) -> None:
    _load_external_bridge_cli().print_status(session)


def print_locations(session: KH3BridgeSession, world_name: str | None) -> None:
    _load_external_bridge_cli().print_locations(session, world_name)


def print_pending_items(session: KH3BridgeSession) -> None:
    _load_external_bridge_cli().print_pending_items(session)


def print_candidate_analyses(session: KH3BridgeSession) -> None:
    _load_external_bridge_cli().print_candidate_analyses(session)


def print_candidate_analysis(analysis: dict[str, object], limit: int = 5) -> None:
    _load_external_bridge_cli().print_candidate_analysis(analysis, limit)


def print_confirmation_result(result: dict[str, object]) -> None:
    _load_external_bridge_cli().print_confirmation_result(result)




def print_pending_action(action_entry: dict[str, object], prefix: str = "  next") -> None:
    _load_external_bridge_cli().print_pending_action(action_entry, prefix)


def build_parser():
    return _load_external_bridge_cli().build_parser()


def main(argv: list[str] | None = None) -> int:
    return _load_external_bridge_cli().main(argv)


if __name__ == "__main__":
    _load_external_bridge_cli().launch(sys.argv[1:])
