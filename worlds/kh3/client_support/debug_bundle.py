"""Headless KH3 diagnostic bundle collection with only randomizer save sidecars."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import Utils

from ..platform_support.game_discovery import (
    KH3_EXECUTABLE_NAME,
    kh3_ue4ss_log_from_root,
    kh3_ue4ss_runtime_dirs_from_root,
    kh3_win64_dir_from_root,
)
from ..platform_support.process_status import get_process_module_snapshot, get_process_status
from ..platform_support.uobject_config import inspect_uobject_config
from ..platform_support.uobject_pool import snapshot_uobject_pool
from ..transport.socket_transport import ENDPOINT_FILE, ENDPOINT_LOCK
from ..bridge_support.diagnostic_history import history_for
from .debug_bridge_snapshot import CHECKPOINT_FILES, LEGACY_MAILBOX_NAMES, capture_bridge_debug_state
from .debug_redaction import DebugBundleArchive, DebugRedactor
from .debug_kh02 import capture_movie_evidence

logger = logging.getLogger("Client")

RANDOMIZER_DEBUG_SAVE_NAMES = (
    "ChoicesMade.sav",
    "PlayerStart.sav",
    "RandomizerRetry.sav",
    "WorldSaveDone.sav",
)
RANDOMIZER_DEBUG_SAVE_PATTERNS = (
    "FlagData*.sav",
    "SavePoint*.sav",
)


def _zip_write_path(zip_file: DebugBundleArchive, source_path: Path, archive_path: str) -> bool:
    try:
        return zip_file.write(source_path, archive_path)
    except OSError as exc:
        logger.info("Could not add %s to KH3 debug bundle: %s", source_path, exc)
        return False


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _unique_existing_files(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        files.append(path)
    return files


def _randomizer_debug_save_files(save_dir: Path) -> list[Path]:
    """Return only known randomizer-owned top-level save sidecars.

    Do not replace this with a general ``*.sav`` or recursive scan: the KH3
    vanilla slot/system files must never enter a routine debug bundle.
    """
    if not save_dir.is_dir():
        return []
    candidates = [save_dir / name for name in RANDOMIZER_DEBUG_SAVE_NAMES]
    for pattern in RANDOMIZER_DEBUG_SAVE_PATTERNS:
        try:
            candidates.extend(save_dir.glob(pattern))
        except OSError as exc:
            logger.info("Could not scan KH3 randomizer save sidecars in %s: %s", save_dir, exc)
    return sorted(
        (path for path in _unique_existing_files(candidates) if not path.is_symlink()),
        key=lambda path: path.name.casefold(),
    )


def _file_summary(path: Path, *, include_sha256: bool = False) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    summary = {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if include_sha256:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            summary["sha256"] = digest.hexdigest().upper()
        except OSError as exc:
            summary["sha256_error"] = str(exc)
    return summary


def _kh3_local_appdata_crash_roots() -> list[Path]:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return []
    local_root = Path(local_appdata).expanduser()
    return [
        local_root / "KINGDOM HEARTS III" / "Saved" / "Crashes",
        local_root / "KH_3" / "Saved" / "Crashes",
    ]


def _kh3_direct_crash_dump_candidates(kh3_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for runtime_dir in kh3_ue4ss_runtime_dirs_from_root(kh3_dir):
        if runtime_dir.is_dir():
            candidates.extend(runtime_dir.glob("crash_*.dmp"))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        wer_crash_dir = Path(local_appdata).expanduser() / "CrashDumps"
        if wer_crash_dir.is_dir():
            candidates.extend(wer_crash_dir.glob("KINGDOM HEARTS III*.dmp"))
            candidates.extend(wer_crash_dir.glob("KH3*.dmp"))
    return _unique_existing_files(candidates)


def _kh3_unreal_crash_report_file_candidates(report_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in ("CrashContext.runtime-xml", "*.runtime-xml", "UE4Minidump*.dmp", "*.dmp", "*.log"):
        candidates.extend(report_dir.glob(pattern))
    return _unique_existing_files(candidates)


def _crash_file_is_from_today(path: Path, today: date) -> bool:
    timestamp = _safe_mtime(path)
    try:
        return timestamp > 0 and datetime.fromtimestamp(timestamp).date() == today
    except (OSError, OverflowError, ValueError):
        return False


def _collect_kh3_crash_artifacts(kh3_dir: Path, *, max_crash_dumps: int | None) -> list[dict[str, Any]]:
    today = datetime.now().date()
    artifacts = [
        {"kind": "dump", "path": path, "mtime": _safe_mtime(path), "files": [path]}
        for path in _kh3_direct_crash_dump_candidates(kh3_dir)
        if _crash_file_is_from_today(path, today)
    ]
    for crash_root in _kh3_local_appdata_crash_roots():
        if not crash_root.is_dir():
            continue
        try:
            report_dirs = [path for path in crash_root.iterdir() if path.is_dir()]
        except OSError as exc:
            logger.info("Could not scan KH3 crash report directory %s: %s", crash_root, exc)
            continue
        for report_dir in report_dirs:
            files = [path for path in _kh3_unreal_crash_report_file_candidates(report_dir)
                     if _crash_file_is_from_today(path, today)]
            if files:
                artifacts.append(
                    {
                        "kind": "unreal_report",
                        "path": report_dir,
                        "mtime": max(_safe_mtime(path) for path in files),
                        "files": files,
                    }
                )
    artifacts.sort(key=lambda artifact: float(artifact.get("mtime") or 0.0), reverse=True)
    return artifacts if max_crash_dumps is None else artifacts[: max(0, max_crash_dumps)]


def _crash_report_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": artifact.get("kind"),
        "path": str(artifact.get("path")),
        "files": [_file_summary(path) for path in artifact.get("files", [])],
    }


def _kh3_runtime_file_summaries(kh3_dir: Path) -> dict[str, dict[str, Any]]:
    win64_dir = kh3_win64_dir_from_root(kh3_dir)
    relative_paths = [
        Path("UE4SS.dll"),
        Path("UE4SS-settings.ini"),
        Path("override.txt"),
        Path("dwmapi.dll"),
        Path("xinput1_3.dll"),
        Path("ue4ss") / "UE4SS.dll",
        Path("ue4ss") / "UE4SS-settings.ini",
        Path("ue4ss") / "MemberVariableLayout.ini",
        Path("ue4ss") / "VTableLayout.ini",
        Path("ue4ss") / "Mods" / "mods.txt",
        Path("UE4SS_Signatures") / "FName_Constructor.notlua",
        Path("UE4SS_Signatures") / "GUObjectArray.lua",
        Path("CustomGameConfigs") / "Kingdom Hearts 3" / "MemberVariableLayout.ini",
        Path("CustomGameConfigs") / "Kingdom Hearts 3" / "UE4SS-settings.ini",
        Path("CustomGameConfigs") / "Kingdom Hearts 3" / "VTableLayout.ini",
        Path("KINGDOM HEARTS III") / "MemberVariableLayout.ini",
        Path("KINGDOM HEARTS III") / "UE4SS-settings.ini",
        Path("KINGDOM HEARTS III") / "VTableLayout.ini",
        Path("KINGDOM HEARTS III") / "Mods" / "mods.txt",
        Path("KINGDOM HEARTS III") / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll",
        Path("Mods") / "mods.txt",
        Path("Mods") / "KH3APProbeCpp" / "config.lua",
        Path("Mods") / "KH3APProbeCpp" / "dlls" / "main.dll",
    ]
    for runtime_dir in kh3_ue4ss_runtime_dirs_from_root(kh3_dir):
        if runtime_dir == win64_dir:
            continue
        try:
            relative_dir = runtime_dir.relative_to(win64_dir)
        except ValueError:
            continue
        if relative_dir in {Path("ue4ss"), Path("KINGDOM HEARTS III")}:
            continue
        for name in ("UE4SS.log", "MemberVariableLayout.ini", "UE4SS-settings.ini", "VTableLayout.ini"):
            relative_paths.append(relative_dir / name)
        relative_paths.append(relative_dir / "Mods" / "mods.txt")
    return {
        relative_path.as_posix(): _file_summary(win64_dir / relative_path, include_sha256=True)
        for relative_path in relative_paths
    }


def _kh3_process_summary() -> dict[str, Any]:
    summary = get_process_status(KH3_EXECUTABLE_NAME)
    pid = summary.get("pid")
    if summary.get("running") and isinstance(pid, int):
        summary["runtime_module_snapshot"] = get_process_module_snapshot(pid)
    else:
        summary["runtime_module_snapshot"] = {
            "status": "process_not_running",
            "modules": [],
        }
    return summary


def _summarize_item_delivery_ledger(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    entries = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    status_counts: dict[str, int] = {}
    replay_policy_counts: dict[str, int] = {}
    latest_applied: list[dict[str, Any]] = []
    for entry in entries.values():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "pending")
        replay_policy = str(entry.get("replay_policy") or "none")
        status_counts[status] = status_counts.get(status, 0) + 1
        replay_policy_counts[replay_policy] = replay_policy_counts.get(replay_policy, 0) + 1
        if status == "applied":
            latest_applied.append(
                {
                    "index": entry.get("index"),
                    "item_name": entry.get("item_name"),
                    "location_name": entry.get("location_name"),
                    "replay_policy": replay_policy,
                    "apply_source": entry.get("apply_source"),
                    "last_action_kind": entry.get("last_action_kind"),
                    "applied_at_utc": entry.get("applied_at_utc"),
                    "last_marked_applied_at_utc": entry.get("last_marked_applied_at_utc"),
                }
            )

    def latest_sort_key(entry: dict[str, Any]) -> tuple[str, int]:
        timestamp = str(entry.get("last_marked_applied_at_utc") or entry.get("applied_at_utc") or "")
        try:
            index = int(entry.get("index", -1))
        except (TypeError, ValueError):
            index = -1
        return timestamp, index

    return {
        "total_count": len(entries),
        "status_counts": dict(sorted(status_counts.items())),
        "replay_policy_counts": dict(sorted(replay_policy_counts.items())),
        "latest_applied": sorted(latest_applied, key=latest_sort_key, reverse=True)[:5],
        "updated_at_utc": payload.get("updated_at_utc"),
    }


def _bridge_file_names() -> list[str]:
    return [
        "ap_status.json",
        "uobject_pool_snapshot.json",
        "item_delivery_ledger.json",
        "item_delivery_state.json",
        "item_delivery.json",
        "item_consumer_plan.json",
        "runtime_item_grant.json",
        "runtime_item_grant_state.json",
        "runtime_item_latency_trace.jsonl",
        "runtime_item_apply_event.json",
        "runtime_item_apply_state.json",
            ]


def _runtime_sidecars(win64_dir: Path) -> dict[str, Path]:
    return {
        "Mods/KH3APProbeCpp/runtime_item_apply_event.json": win64_dir
        / "Mods"
        / "KH3APProbeCpp"
        / "runtime_item_apply_event.json",
        "Mods/KH3APProbeCpp/runtime_grant_cpp_profile.jsonl": win64_dir
        / "Mods"
        / "KH3APProbeCpp"
        / "runtime_grant_cpp_profile.jsonl",
    }


def _runtime_text_files(kh3_dir: Path, win64_dir: Path, sidecars: dict[str, Path]) -> list[tuple[Path, str]]:
    relative_names = [
        ("UE4SS-settings.ini", "runtime/Win64/UE4SS-settings.ini"),
        ("override.txt", "runtime/Win64/override.txt"),
        ("ue4ss/UE4SS-settings.ini", "runtime/Win64/ue4ss/UE4SS-settings.ini"),
        ("ue4ss/MemberVariableLayout.ini", "runtime/Win64/ue4ss/MemberVariableLayout.ini"),
        ("ue4ss/VTableLayout.ini", "runtime/Win64/ue4ss/VTableLayout.ini"),
        ("ue4ss/Mods/mods.txt", "runtime/Win64/ue4ss/Mods/mods.txt"),
        (
            "CustomGameConfigs/Kingdom Hearts 3/MemberVariableLayout.ini",
            "runtime/Win64/CustomGameConfigs/Kingdom Hearts 3/MemberVariableLayout.ini",
        ),
        (
            "CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini",
            "runtime/Win64/CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini",
        ),
        (
            "CustomGameConfigs/Kingdom Hearts 3/VTableLayout.ini",
            "runtime/Win64/CustomGameConfigs/Kingdom Hearts 3/VTableLayout.ini",
        ),
        ("KINGDOM HEARTS III/MemberVariableLayout.ini", "runtime/Win64/KINGDOM HEARTS III/MemberVariableLayout.ini"),
        ("KINGDOM HEARTS III/UE4SS-settings.ini", "runtime/Win64/KINGDOM HEARTS III/UE4SS-settings.ini"),
        ("KINGDOM HEARTS III/VTableLayout.ini", "runtime/Win64/KINGDOM HEARTS III/VTableLayout.ini"),
        ("Mods/mods.txt", "runtime/Win64/Mods/mods.txt"),
        ("Mods/KH3APProbeCpp/config.lua", "runtime/Win64/Mods/KH3APProbeCpp/config.lua"),
    ]
    for runtime_dir in kh3_ue4ss_runtime_dirs_from_root(kh3_dir):
        if runtime_dir == win64_dir:
            continue
        try:
            relative_dir = runtime_dir.relative_to(win64_dir)
        except ValueError:
            continue
        if relative_dir in {Path("ue4ss"), Path("KINGDOM HEARTS III")}:
            continue
        for name in ("MemberVariableLayout.ini", "UE4SS-settings.ini", "VTableLayout.ini"):
            relative = (relative_dir / name).as_posix()
            relative_names.append((relative, f"runtime/Win64/{relative}"))
        mods_relative = (relative_dir / "Mods" / "mods.txt").as_posix()
        relative_names.append((mods_relative, f"runtime/Win64/{mods_relative}"))
    files = [(win64_dir / source, archive) for source, archive in relative_names]
    files.extend(
        [
            (
                sidecars["Mods/KH3APProbeCpp/runtime_item_apply_event.json"],
                "runtime/Win64/Mods/KH3APProbeCpp/runtime_item_apply_event.json",
            ),
            (
                sidecars["Mods/KH3APProbeCpp/runtime_grant_cpp_profile.jsonl"],
                "runtime/Win64/Mods/KH3APProbeCpp/runtime_grant_cpp_profile.jsonl",
            ),
        ]
    )
    return files


def _write_bridge_files(
    zip_file: DebugBundleArchive, bridge_dir: Path | None, included: list[str], captured_raw: dict[str, bytes],
) -> None:
    if bridge_dir is None or not bridge_dir.is_dir():
        return
    # Redact the captured bases, never reread them during compression. Binary
    # journals are omitted; recovered state and recovery diagnostics remain.
    for name, value in captured_raw.items():
        archive_path = (Path("bridge") / bridge_dir.name / name).as_posix()
        if zip_file.writestr(archive_path, value):
            included.append(archive_path)
    for path in sorted(bridge_dir.rglob("*")):
        relative = path.relative_to(bridge_dir)
        if (relative.as_posix() in CHECKPOINT_FILES | {ENDPOINT_FILE, ENDPOINT_LOCK}
                or path.suffix == ".tmp" or path.is_symlink() or not path.is_file()):
            continue
        archive_path = (Path("bridge") / bridge_dir.name / relative).as_posix()
        if _zip_write_path(zip_file, path, archive_path):
            included.append(archive_path)


def _write_runtime_files(
    zip_file: DebugBundleArchive,
    kh3_dir: Path,
    win64_dir: Path,
    ue4ss_log: Path,
    sidecars: dict[str, Path],
    included: list[str],
) -> None:
    if ue4ss_log.is_file() and _zip_write_path(zip_file, ue4ss_log, "logs/UE4SS.log"):
        included.append("logs/UE4SS.log")
    for runtime_dir in kh3_ue4ss_runtime_dirs_from_root(kh3_dir):
        alternate_log = runtime_dir / "UE4SS.log"
        if alternate_log == ue4ss_log or not alternate_log.is_file():
            continue
        label = runtime_dir.name or "Win64"
        archive_path = f"logs/UE4SS-{label}.log"
        if _zip_write_path(zip_file, alternate_log, archive_path):
            included.append(archive_path)
    for source_path, archive_path in _runtime_text_files(kh3_dir, win64_dir, sidecars):
        if source_path.is_file() and _zip_write_path(zip_file, source_path, archive_path):
            included.append(archive_path)


def _client_log_files() -> list[Path]:
    """Current KH3 client log plus up to three recent logs for restart context."""
    active = []
    for owner in (logging.getLogger(), logger):
        for handler in owner.handlers:
            if not isinstance(handler, logging.FileHandler):
                continue
            path = Path(handler.baseFilename)
            if not path.name.startswith("KH3Client"):
                continue
            try:
                handler.acquire()
                try:
                    handler.flush()
                finally:
                    handler.release()
            except OSError:
                pass
            active.append(path)
    log_dir = Path(Utils.user_path("logs"))
    recent = sorted(log_dir.glob("KH3Client*.txt"), key=_safe_mtime, reverse=True)[:3] if log_dir.is_dir() else []
    return _unique_existing_files(active + recent)


def _write_randomizer_save_files(
    zip_file: DebugBundleArchive,
    save_files: list[Path],
    included: list[str],
) -> None:
    for save_file in save_files:
        archive_path = (Path("randomizer-saves") / save_file.name).as_posix()
        if _zip_write_path(zip_file, save_file, archive_path):
            included.append(archive_path)


def _write_crash_artifacts(
    zip_file: DebugBundleArchive,
    artifacts: list[dict[str, Any]],
    included: list[str],
) -> None:
    for artifact in artifacts:
        artifact_path = artifact.get("path")
        artifact_files = artifact.get("files", [])
        for report_file in artifact_files:
            if not isinstance(report_file, Path):
                continue
            if artifact.get("kind") == "dump":
                archive_path = f"crashes/{report_file.name}"
            elif artifact.get("kind") == "unreal_report" and isinstance(artifact_path, Path):
                archive_path = (
                    Path("crashes") / "unreal" / artifact_path.name / report_file.relative_to(artifact_path)
                ).as_posix()
            else:
                continue
            try:
                # Only explicitly selected crash artifacts may bypass text redaction.
                if zip_file.write(report_file, archive_path, raw_dump=True):
                    included.append(archive_path)
            except OSError as exc:
                if artifact.get("required"):
                    raise
                logger.info("Could not add crash artifact to KH3 debug bundle: %s", exc)


def export_kh3_debug_bundle(
    *,
    kh3_dir: Path,
    bridge_dir: Path | None,
    kh3_mods_dir: Path,
    save_dir: Path,
    ue4ss_log: Path | None,
    output_dir: Path | None = None,
    max_crash_dumps: int | None = None,
    include_crash_dumps: bool = False,
    capture_passive_dump: bool = False,
) -> dict[str, Any]:
    output_dir = (output_dir or Path(Utils.user_path("kh3", "debug-bundles"))).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / f"kh3-debug-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip"
    win64_dir = kh3_win64_dir_from_root(kh3_dir)
    ue4ss_log = ue4ss_log or kh3_ue4ss_log_from_root(kh3_dir)
    passive_dump = None
    if capture_passive_dump:
        from .passive_dump import capture_kh3_passive_dump
        logger.info("Capturing KH3 crash dump. Leave the fatal-error window open until capture completes.")
        passive_dump = capture_kh3_passive_dump(kh3_dir, output_dir / "passive-dumps")
        dump_path = Path(passive_dump["path"])
        # Keep this explicit capture separate from older, unrelated crash dumps.
        artifacts = [{"kind": "dump", "path": dump_path, "files": [dump_path], "required": True}]
        logger.info("Crash dump captured: %s (%.1f MB). Compressing debug bundle…",
                    dump_path, passive_dump["size"] / 1_000_000)
    elif include_crash_dumps:
        artifacts = _collect_kh3_crash_artifacts(kh3_dir, max_crash_dumps=max_crash_dumps)
    else:
        artifacts = []
    crash_dumps = [
        path
        for artifact in artifacts
        for path in artifact.get("files", [])
        if isinstance(path, Path) and path.suffix.casefold() == ".dmp"
    ]
    installed_paks = (
        sorted(kh3_mods_dir.glob("*.pak"), key=lambda path: path.name.casefold()) if kh3_mods_dir.is_dir() else []
    )
    bridge_summaries = (
        {name: _file_summary(bridge_dir / name) for name in _bridge_file_names()} if bridge_dir is not None else {}
    )
    pipe_snapshot, bridge_capture, captured_raw, recovered = capture_bridge_debug_state(bridge_dir)
    histories = history_for(bridge_dir).snapshot() if bridge_dir is not None else {}
    history_archives = {
        name: {**{key: value for key, value in entry.items() if key != "lines"},
               "archive": f"diagnostics/{bridge_dir.name}/{name}"}
        for name, entry in histories.items() if entry["retained_count"] or entry["dropped_count"]
    }
    pipe_archive = "pipe/transport_snapshot.json"
    for name, details in bridge_capture["files"].items():
        details["redacted_base_archives"] = [(Path("bridge") / bridge_dir.name / raw_name).as_posix()
                                            for raw_name in details["raw_files"] if not raw_name.endswith(".wal")]
        details["raw_journals_exported"] = False
        if name in recovered:
            details["recovered_archive"] = (Path("bridge-state") / bridge_dir.name / name).as_posix()
    ledger_summary = _summarize_item_delivery_ledger(
        json.loads(recovered["item_delivery_ledger.json"]) if "item_delivery_ledger.json" in recovered else None)
    client_logs = _client_log_files()
    sidecars = _runtime_sidecars(win64_dir)
    kh02_summary, kh02_files = capture_movie_evidence(win64_dir)
    randomizer_save_files = _randomizer_debug_save_files(save_dir)
    pool_config = inspect_uobject_config(save_dir)
    pool_snapshot = snapshot_uobject_pool(kh3_dir)
    summary: dict[str, Any] = {
        "format": "kh3_debug_bundle",
        "format_version": 3,
        "privacy": {
            "text_redaction": True,
            "crash_artifacts_opt_in": bool(include_crash_dumps or capture_passive_dump),
            "raw_dumps_are_unredacted": bool(crash_dumps),
            "sharing": "private_support_only" if crash_dumps else "review_redacted_text_before_sharing",
        },
        "passive_dump": passive_dump,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "kh3_dir": str(kh3_dir),
        "win64_dir": str(win64_dir),
        "bridge_dir": str(bridge_dir) if bridge_dir is not None else None,
        "mods_dir": str(kh3_mods_dir),
        "save_dir": str(save_dir),
        "uobject_config": pool_config,
        "uobject_pool": pool_snapshot,
        "ue4ss_log": _file_summary(ue4ss_log),
        "game_process": _kh3_process_summary(),
        "runtime_files": _kh3_runtime_file_summaries(kh3_dir),
        "runtime_sidecar_files": {name: _file_summary(path) for name, path in sidecars.items()},
        "kh02_movie_evidence": kh02_summary,
        "randomizer_save_files": {
            path.name: _file_summary(path, include_sha256=True) for path in randomizer_save_files
        },
        "installed_paks": [_file_summary(path) for path in installed_paks],
        "crash_dumps": [_file_summary(path) for path in crash_dumps],
        "crash_reports": [_crash_report_summary(artifact) for artifact in artifacts],
        "bridge_files": bridge_summaries,
        "bridge_capture": bridge_capture,
        "diagnostic_history": history_archives,
        "pipe_transport": {**{key: pipe_snapshot.get(key) for key in (
            "transport", "endpoint", "status", "connected", "closed", "captured_at_utc", "epoch", "error", "reason",
            "packets_sent", "packets_received")}, "archive": pipe_archive},
        "legacy_disk_mailboxes": [name for name in sorted(LEGACY_MAILBOX_NAMES)
                                  if bridge_dir is not None and (bridge_dir / name).is_file()],
        "client_logs": [_file_summary(path) for path in client_logs],
        "item_delivery_ledger": ledger_summary,
        "notes": [
            "Only explicitly allowlisted randomizer-owned save sidecars are included.",
            "Vanilla KH3 slot and system saves are never scanned or copied into the bundle.",
            "Installed pak files are listed but not copied into the bundle.",
            "Exported text is redacted; source files are unchanged. Review free-form text before sharing.",
            "Existing crash artifacts are opt-in and limited to files modified today in local time.",
            "Binary dumps are unredacted; share them privately with trusted maintainers.",
            "bridge/ contains redacted text copies. Raw recovery journals and other opaque files are omitted.",
            "bridge-state/ contains redacted recovered JSON or captured native IPC views; see source metadata and recovery errors.",
            "Pipe-only files on disk are legacy evidence, not current runtime or pending-command state.",
            "pipe/transport_snapshot.json records available in-memory state; disconnected snapshots are historical.",
            "diagnostics/ contains bounded process-local histories, exported on demand; older records may be dropped.",
            "Existing disk diagnostic histories in bridge/ are historical evidence, not the current memory buffers.",
            "Local socket discovery credentials and the listener lock are excluded.",
            "Captures have separate timestamps; the game is not paused and this is not a full packet recording.",
        ],
    }
    included = []
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        zip_file = DebugBundleArchive(archive, DebugRedactor(save_dir=save_dir))
        for name, value in kh02_files.items():
            zip_file.writestr(name, value)
            included.append(name)
        zip_file.writestr(pipe_archive, json.dumps(pipe_snapshot, ensure_ascii=False, separators=(",", ":")))
        included.append(pipe_archive)
        for name, details in history_archives.items():
            archive_path = details["archive"]
            zip_file.writestr(archive_path, "".join(line + "\n" for line in histories[name]["lines"]))
            included.append(archive_path)
        for name, value in recovered.items():
            archive_path = bridge_capture["files"][name]["recovered_archive"]
            zip_file.writestr(archive_path, value)
            included.append(archive_path)
        for index, path in enumerate(client_logs):
            archive_path = f"logs/client/{index + 1}-{path.name}"
            if _zip_write_path(zip_file, path, archive_path):
                included.append(archive_path)
        config_path = Path(pool_config["path"]) if pool_config.get("path") else None
        if config_path is not None and config_path.is_file() and not config_path.is_symlink():
            if _zip_write_path(zip_file, config_path, "config/Engine.ini"):
                included.append("config/Engine.ini")
        _write_bridge_files(zip_file, bridge_dir, included, captured_raw)
        _write_runtime_files(zip_file, kh3_dir, win64_dir, ue4ss_log, sidecars, included)
        _write_randomizer_save_files(zip_file, randomizer_save_files, included)
        _write_crash_artifacts(zip_file, artifacts, included)
        summary["privacy"]["omitted_files"] = zip_file.omitted
        zip_file.writestr("debug_summary.json", json.dumps(summary, indent=2, sort_keys=True))
        included = archive.namelist()
    logger.info("Exported KH3 debug bundle: %s", bundle_path)
    return {
        "status": "exported",
        "bundle_path": str(bundle_path),
        "included_files": included,
        "crash_dump_count": len(crash_dumps),
        "crash_report_count": len(artifacts),
        "installed_pak_count": len(installed_paks),
        "randomizer_save_count": sum(name.startswith("randomizer-saves/") for name in included),
    }


__all__ = ["export_kh3_debug_bundle"]
