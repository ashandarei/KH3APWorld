"""Managed KH3 AP run inventory, save snapshots, activation, and deletion."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import tempfile
import threading
import unicodedata
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import Utils

from ..Bridge import BridgeProtocolError
from ..bridge_support.files import atomic_write_json, load_optional_json_file
from ..RuntimeSeed import encode_document, metadata_payload
from .platform_seed_download import kh3_room_page_url_from_slot_data, normalize_kh3_room_page_url
from .save_file_policy import (
    check_save_transfer, checked_path, checked_root, checked_save,
    inspect_save_tree, remove_empty_save_directories, transfer_save,
)
from .seed_guard import SeedGuardService
from .seed_paks import (
    KH3_AP_SEED_PAK_PATTERN,
    infer_kh3_ap_slot_name_from_pak_name,
    kh3_ap_seed_metadata_matches_pak,
)
from .seed_policy_service import KH3_MINIMUM_SEED_PAK_SIZE_BYTES

from .run_models import (
    KH3RunManagerError as KH3RunManagerError,
    KH3RunInfo as KH3RunInfo,
    KH3RunConnectionProfile as KH3RunConnectionProfile,
    KH3RunActivationResult as KH3RunActivationResult,
    KH3RunSuspensionResult as KH3RunSuspensionResult,
    KH3RunEnrollmentResult as KH3RunEnrollmentResult,
    KH3RunDeleteResult as KH3RunDeleteResult,
    _PakIdentity as _PakIdentity,
)


KH3_RUN_MANIFEST_FILE = "run.json"
KH3_RUN_MANIFEST_FORMAT = "kh3_ap_managed_run"
KH3_RUN_MANIFEST_VERSION = 1
KH3_ACTIVE_RUN_FILE = ".active-run.json"
KH3_ACTIVE_RUN_FORMAT = "kh3_ap_active_managed_run"
KH3_ACTIVE_RUN_VERSION = 1

PathSource = Path | Callable[[], Path]


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def format_kh3_run_server_address(server: str, port: str | int) -> str:
    """Build the AP address stored for a run from separate server and port fields."""

    raw_server = str(server or "").strip()
    raw_port = str(port or "").strip()
    if not raw_server:
        raise KH3RunManagerError("Enter the AP server for this KH3 run.")
    if len(raw_server) > 512 or any(character.isspace() for character in raw_server):
        raise KH3RunManagerError("The AP server cannot contain spaces.")
    try:
        port_number = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise KH3RunManagerError("Enter a numeric AP server port.") from exc
    if not 1 <= port_number <= 65535:
        raise KH3RunManagerError("The AP server port must be between 1 and 65535.")

    has_scheme = "://" in raw_server
    parse_target = raw_server if has_scheme else f"ws://{raw_server}"
    try:
        parsed = urllib.parse.urlparse(parse_target)
        embedded_port = parsed.port
    except ValueError as exc:
        raise KH3RunManagerError(
            "The AP server is invalid. Enclose an IPv6 address in square brackets."
        ) from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"ws", "wss"}:
        raise KH3RunManagerError("The AP server may use ws:// or wss:// only.")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise KH3RunManagerError("Enter an AP server hostname or IP address without a path.")
    if embedded_port is not None and embedded_port != port_number:
        raise KH3RunManagerError("The port in the Server field does not match the separate Port field.")

    host = parsed.hostname
    display_host = f"[{host}]" if ":" in host else host
    scheme_prefix = "wss://" if scheme == "wss" else ""
    address = f"{scheme_prefix}{display_host}:{port_number}"
    if len(address) > 512:
        raise KH3RunManagerError("The AP server address is too long.")
    return address


def split_kh3_run_server_address(server_address: str | None) -> tuple[str, str]:
    """Split a remembered AP address into modal-friendly server and port text."""

    raw_address = str(server_address or "").strip()
    if not raw_address:
        return "", "38281"
    has_scheme = "://" in raw_address
    parse_target = raw_address if has_scheme else f"ws://{raw_address}"
    try:
        parsed = urllib.parse.urlparse(parse_target)
        host = parsed.hostname
        port = parsed.port or 38281
    except ValueError:
        return raw_address, "38281"
    if not host or parsed.scheme.casefold() not in {"ws", "wss"}:
        return raw_address, "38281"
    display_host = f"[{host}]" if ":" in host else host
    scheme_prefix = f"{parsed.scheme.casefold()}://" if has_scheme else ""
    return f"{scheme_prefix}{display_host}", str(port)


def normalize_kh3_run_server_host(server_address: str | None) -> str:
    """Return a stable hostname identity while deliberately ignoring the AP port."""

    raw_address = str(server_address or "").strip()
    if not raw_address:
        return ""
    parse_target = raw_address if "://" in raw_address else f"ws://{raw_address}"
    try:
        parsed = urllib.parse.urlparse(parse_target)
        host = str(parsed.hostname or "").strip().rstrip(".")
    except ValueError:
        return ""
    if not host or parsed.scheme.casefold() not in {"ws", "wss"}:
        return ""
    try:
        return ipaddress.ip_address(host).compressed.casefold()
    except ValueError:
        try:
            return host.encode("idna").decode("ascii").casefold()
        except UnicodeError:
            return host.casefold()


def normalize_kh3_connection_slot_name(slot_name: str | None) -> str:
    """Normalize an AP login name for profile matching without changing its display value."""

    return unicodedata.normalize("NFKC", str(slot_name or "").strip()).casefold()


def kh3_run_connection_affinity(server_address: str | None, slot_name: str | None) -> str:
    """Build the hostname + slot key used to isolate saves while allowing port changes."""

    host = normalize_kh3_run_server_host(server_address)
    normalized_slot = normalize_kh3_connection_slot_name(slot_name)
    if not host or not normalized_slot:
        return ""
    return f"{host}\n{normalized_slot}"


def _kh3_run_connection_token(server_address: str | None, slot_name: str | None) -> str:
    affinity = kh3_run_connection_affinity(server_address, slot_name)
    return hashlib.sha256(affinity.encode("utf-8")).hexdigest()[:12] if affinity else ""


class KH3RunManagerService:
    """Own the AP-only run library without touching protected vanilla backups."""

    def __init__(
        self,
        *,
        run_root: PathSource,
        bridge_root: PathSource,
        mods_dir: PathSource,
        save_dir: PathSource,
        process_running: Callable[[], bool],
        now_utc: Callable[[], str] = _utc_now_text,
        platform: str | Callable[[], str] = "",
        legacy_platforms: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._run_root_source = run_root
        self._bridge_root_source = bridge_root
        self._mods_dir_source = mods_dir
        self._save_dir_source = save_dir
        self.process_running = process_running
        self.now_utc = now_utc
        self._platform_source = platform
        self._legacy_platforms_source = legacy_platforms
        self._legacy_platforms: dict[str, str] = {}
        self._inventory_cache: tuple[KH3RunInfo, ...] | None = None
        self._inventory_cache_signature: tuple[Any, ...] | None = None
        self._inventory_cache_lock = threading.RLock()

    @staticmethod
    def _resolve_path(source: PathSource) -> Path:
        value = source() if callable(source) else source
        return Path(value).expanduser().resolve()

    @property
    def platform(self) -> str:
        value = self._platform_source() if callable(self._platform_source) else self._platform_source
        if value not in {"", "steam", "egs"}:
            raise KH3RunManagerError(f"Unknown KH3 platform: {value}")
        return value

    def matches_platform(self, run: KH3RunInfo) -> bool:
        return run.platform == self.platform

    @property
    def run_root(self) -> Path:
        return self._resolve_path(self._run_root_source)

    @property
    def bridge_root(self) -> Path:
        return self._resolve_path(self._bridge_root_source)

    @property
    def mods_dir(self) -> Path:
        return self._resolve_path(self._mods_dir_source)

    @property
    def save_dir(self) -> Path:
        source = self._save_dir_source
        return self._save_root(Path(source() if callable(source) else source))

    @staticmethod
    def _save_root(path: Path) -> Path:
        try:
            return checked_root(path)
        except OSError as exc:
            raise KH3RunManagerError(str(exc)) from exc

    def invalidate_inventory_cache(self) -> None:
        """Discard the cached run list after an in-process filesystem mutation."""

        with self._inventory_cache_lock:
            self._inventory_cache = None
            self._inventory_cache_signature = None

    @staticmethod
    def run_id_for_identity(seed_id: str, player: int, slot_name: str, *, platform: str = "") -> str:
        base = Utils.get_file_safe_name(f"{seed_id}-{player}-{slot_name}").strip(" .")
        return f"{base}--{platform}" if platform else base

    @classmethod
    def run_id_for_connection(
        cls,
        seed_id: str,
        player: int,
        slot_name: str,
        *,
        server_address: str | None,
        connection_slot_name: str | None,
        platform: str = "",
    ) -> str:
        """Build a profile-specific id; the port is intentionally absent from its affinity token."""

        base_run_id = cls.run_id_for_identity(seed_id, player, slot_name, platform=platform)
        token = _kh3_run_connection_token(server_address, connection_slot_name)
        return f"{base_run_id}--{token}" if token else base_run_id

    @classmethod
    def bridge_dir_name_for_run(
        cls,
        seed_id: str,
        slot_name: str,
        run_id: str,
    ) -> str:
        """Return the one live bridge owned by the installed pak identity.

        ``run_id`` remains connection-specific so separate servers retain separate
        save snapshots, but those profiles must never split the runtime bridge.
        """

        return Utils.get_file_safe_name(f"{seed_id}_{slot_name}").strip(" .")

    @staticmethod
    def _pak_key(
        seed_id: str,
        player: int,
        slot_name: str,
        pak_name: str,
    ) -> tuple[str, int, str, str]:
        return seed_id, player, slot_name.casefold(), Path(pak_name).name.casefold()

    @classmethod
    def _pak_key_for_identity(cls, identity: _PakIdentity) -> tuple[str, int, str, str]:
        return cls._pak_key(identity.seed_id, identity.player, identity.slot_name, identity.pak_name)

    @classmethod
    def _pak_key_for_run(cls, run: KH3RunInfo) -> tuple[str, int, str, str]:
        return cls._pak_key(run.seed_id, run.player, run.slot_name, run.pak_name)

    @classmethod
    def pak_identity(cls, pak_path: Path | str, *, require_file: bool = True) -> _PakIdentity:
        path = Path(pak_path).expanduser().resolve()
        match = KH3_AP_SEED_PAK_PATTERN.fullmatch(path.name)
        if match is None:
            raise KH3RunManagerError(
                "Select a generated KH3 AP player pak named "
                "pakchunk9-<slot>-randomizer-AP_<seed>-<player>_99_P.pak."
            )
        if require_file and not path.is_file():
            raise KH3RunManagerError(f"KH3 seed pak does not exist: {path}")
        if require_file:
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise KH3RunManagerError(f"Could not read KH3 seed pak {path}: {exc}") from exc
            if size < KH3_MINIMUM_SEED_PAK_SIZE_BYTES:
                raise KH3RunManagerError(
                    f"KH3 seed pak is only {size} bytes; expected at least "
                    f"{KH3_MINIMUM_SEED_PAK_SIZE_BYTES} bytes. Regenerate the seed pak."
                )
        seed_id = match.group("seed")
        player = int(match.group("player"))
        slot_name = infer_kh3_ap_slot_name_from_pak_name(path)
        if slot_name is None:
            raise KH3RunManagerError(f"Could not read the KH3 slot name from seed pak: {path.name}")
        return _PakIdentity(
            run_id=cls.run_id_for_identity(seed_id, player, slot_name),
            seed_id=seed_id,
            player=player,
            slot_name=slot_name,
            pak_name=path.name,
            path=path,
        )

    @staticmethod
    def _assert_direct_child(root: Path, path: Path, *, label: str) -> Path:
        try:
            root = checked_root(root)
            path = checked_path(root, path)
        except OSError as exc:
            raise KH3RunManagerError(f"Refusing {label}: {exc}") from exc
        if path.parent != root or path == root:
            raise KH3RunManagerError(f"Refusing {label} outside the managed root: {path}")
        if path.is_symlink():
            raise KH3RunManagerError(f"Refusing {label} through a symbolic link: {path}")
        return path

    @staticmethod
    def _manifest_path(run_dir: Path) -> Path:
        return run_dir / KH3_RUN_MANIFEST_FILE

    @property
    def _active_run_path(self) -> Path:
        name = f".active-run.{self.platform}.json" if self.platform else KH3_ACTIVE_RUN_FILE
        return self.run_root / name

    def _read_active_run_id(self) -> str | None:
        payload = load_optional_json_file(self._active_run_path)
        if payload is None and self.platform:
            legacy = load_optional_json_file(self.run_root / KH3_ACTIVE_RUN_FILE)
            if isinstance(legacy, dict) and self._legacy_platforms.get(str(legacy.get("run_id"))) == self.platform:
                payload = legacy
        if not isinstance(payload, dict):
            return None
        if (
            payload.get("format") != KH3_ACTIVE_RUN_FORMAT
            or payload.get("version") != KH3_ACTIVE_RUN_VERSION
        ):
            return None
        run_id = str(payload.get("run_id") or "").strip()
        return run_id or None

    def _write_active_run_id(self, run_id: str) -> None:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise KH3RunManagerError("Could not store the active KH3 run identity.")
        self.run_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._active_run_path, {
            "format": KH3_ACTIVE_RUN_FORMAT,
            "version": KH3_ACTIVE_RUN_VERSION,
            "run_id": run_id,
            "updated_at_utc": self.now_utc(),
        })
        self.invalidate_inventory_cache()

    def _restore_active_run_id(self, run_id: str | None) -> None:
        if run_id:
            self._write_active_run_id(run_id)
            return
        try:
            self._active_run_path.unlink(missing_ok=True)
        except (OSError, KH3RunManagerError) as exc:
            raise KH3RunManagerError(f"Could not clear the active KH3 run identity: {exc}") from exc
        self.invalidate_inventory_cache()

    @staticmethod
    def _managed_pak_path(run_dir: Path, pak_name: str) -> Path:
        return run_dir / "pak" / Path(pak_name).name

    @staticmethod
    def _save_snapshot_dir(run_dir: Path) -> Path:
        return run_dir / "save"

    @staticmethod
    def _iter_gameplay_save_files(save_dir: Path) -> list[Path]:
        try:
            return list(inspect_save_tree(save_dir).files)
        except OSError as exc:
            raise KH3RunManagerError(str(exc)) from exc

    @classmethod
    def _has_gameplay_save_files(cls, save_dir: Path) -> bool:
        return bool(cls._iter_gameplay_save_files(save_dir))

    @staticmethod
    def _remove_empty_directories(root: Path) -> None:
        try:
            remove_empty_save_directories(root)
        except OSError:
            pass

    @classmethod
    def _copy_gameplay_save_files(cls, source: Path, destination: Path) -> int:
        files = cls._iter_gameplay_save_files(source)
        checked_root(destination)
        for path in files:
            check_save_transfer(source, destination, path, destination / path.relative_to(source))
        destination.mkdir(parents=True, exist_ok=True)
        copied = 0
        for source_path in files:
            target_path = destination / source_path.relative_to(source)
            transfer_save(source, destination, source_path, target_path, move=False)
            copied += 1
        return copied

    @classmethod
    def _move_gameplay_save_files(cls, source: Path, destination: Path) -> int:
        """Move a gameplay-save tree and restore moved files if any move fails."""

        files = cls._iter_gameplay_save_files(source)
        moved: list[tuple[Path, Path]] = []
        try:
            checked_root(destination)
            for path in files:
                check_save_transfer(source, destination, path, destination / path.relative_to(source))
            destination.mkdir(parents=True, exist_ok=True)
            for source_path in files:
                target_path = destination / source_path.relative_to(source)
                transfer_save(source, destination, source_path, target_path, move=True)
                moved.append((source_path, target_path))
        except OSError as exc:
            rollback_errors: list[str] = []
            for original_path, moved_path in reversed(moved):
                try:
                    transfer_save(destination, source, moved_path, original_path, move=True)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{moved_path}: {rollback_exc}")
            details = f"; move rollback errors: {', '.join(rollback_errors)}" if rollback_errors else ""
            raise KH3RunManagerError(f"Could not move KH3 saves from {source}: {exc}{details}") from exc
        cls._remove_empty_directories(source)
        return len(moved)

    @staticmethod
    def _atomic_copy_file(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            try:
                temporary_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _same_file_version(source: Path, destination: Path) -> bool:
        if source == destination:
            return True
        try:
            source_stat = source.stat()
            destination_stat = destination.stat()
        except OSError:
            return False
        return (
            source_stat.st_size == destination_stat.st_size
            and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
        )

    def _installed_paks(self) -> list[_PakIdentity]:
        if not self.mods_dir.is_dir():
            return []
        identities: list[_PakIdentity] = []
        for path in sorted(self.mods_dir.glob("*.pak"), key=lambda candidate: candidate.name.casefold()):
            if KH3_AP_SEED_PAK_PATTERN.fullmatch(path.name) is None:
                continue
            identities.append(self.pak_identity(path, require_file=False))
        return identities

    def installed_pak_count(self) -> int:
        return len(self._installed_paks())

    def _managed_runs_for_identity(self, identity: _PakIdentity) -> list[KH3RunInfo]:
        key = self._pak_key_for_identity(identity)
        return [
            run
            for run in self.list_runs()
            if run.managed and self.matches_platform(run) and self._pak_key_for_run(run) == key
        ]

    def _active_managed_run_for_identity(self, identity: _PakIdentity) -> KH3RunInfo | None:
        matching = self._managed_runs_for_identity(identity)
        marked_run_id = self._read_active_run_id()
        marked = next((run for run in matching if run.run_id == marked_run_id), None)
        if marked is not None:
            return marked
        if len(matching) == 1:
            return matching[0]
        base = self.run_id_for_identity(identity.seed_id, identity.player, identity.slot_name, platform=self.platform)
        return next((run for run in matching if run.run_id == base), None)

    def _bridge_dirs_for_identity(self, identity: _PakIdentity) -> tuple[Path, ...]:
        bridge_root = self.bridge_root
        if not bridge_root.is_dir():
            return ()
        expected_name = Utils.get_file_safe_name(f"{identity.seed_id}_{identity.slot_name}")
        matches: list[Path] = []
        for candidate in sorted(bridge_root.iterdir(), key=lambda path: path.name.casefold()):
            if not candidate.is_dir() or candidate.is_symlink() or candidate.name.startswith("."):
                continue
            matched = candidate.name == expected_name
            if not matched:
                try:
                    metadata = load_optional_json_file(candidate / "kh3_ap_seed_metadata.json")
                except (BridgeProtocolError, ValueError, OSError):
                    # Ignore unusable candidate metadata; do not import or
                    # migrate it into the current seed's runtime bridge.
                    continue
                if isinstance(metadata, dict):
                    seed_id = SeedGuardService.extract_seed_id(metadata.get("seed"))
                    slot = metadata.get("slot")
                    slot_name = str(metadata.get("slot_name") or metadata.get("kh3_player_name") or "").strip()
                    matched = seed_id == identity.seed_id and (
                        slot == identity.player or (slot_name and slot_name == identity.slot_name)
                    )
            if matched:
                matches.append(candidate.resolve())
        return tuple(matches)

    def _bridge_dirs_for_run(
        self,
        identity: _PakIdentity,
        *,
        run_id: str,
        bridge_dir_name: str,
    ) -> tuple[Path, ...]:
        """Resolve the pak-owned runtime bridge referenced by this save profile."""

        candidate_name = str(bridge_dir_name or "").strip()
        if candidate_name:
            candidate = self.bridge_root / candidate_name
            if candidate.parent == self.bridge_root and candidate.is_dir() and not candidate.is_symlink():
                return (candidate.resolve(),)
        if run_id == identity.run_id:
            return self._bridge_dirs_for_identity(identity)
        return ()

    @staticmethod
    def _inventory_path_signature(path: Path | os.DirEntry[str]) -> tuple[Any, ...]:
        """Return cheap metadata for one inventory-relevant path without opening it."""

        try:
            status = path.lstat() if isinstance(path, Path) else path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return ("missing",)
        except OSError as exc:
            return ("error", exc.errno, getattr(exc, "winerror", None))
        return ("present", status.st_mode, status.st_size, status.st_mtime_ns)

    @staticmethod
    def _inventory_children(path: Path) -> tuple[tuple[os.DirEntry[str], ...], tuple[Any, ...]]:
        """Retain scandir metadata so Windows inventory refreshes avoid repeated stats."""

        try:
            with os.scandir(path) as entries:
                return tuple(entries), ("directory",)
        except FileNotFoundError:
            return (), ("missing",)
        except NotADirectoryError:
            return (), ("not_directory",)
        except OSError as exc:
            return (), ("error", exc.errno, getattr(exc, "winerror", None))

    def _inventory_source_signature(self) -> tuple[Any, ...]:
        """Describe inventory inputs using shallow stats instead of recursive save scans."""

        self._legacy_platforms = self._legacy_platforms_source() if self._legacy_platforms_source else {}
        run_root = self.run_root
        run_children, run_root_state = self._inventory_children(run_root)
        run_entries: list[tuple[Any, ...]] = []
        for entry in sorted(run_children, key=lambda candidate: candidate.name.casefold()):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            run_dir = Path(entry.path)
            pak_dir = run_dir / "pak"
            pak_children, pak_dir_state = self._inventory_children(pak_dir)
            pak_entries = tuple(
                (path.name, self._inventory_path_signature(path))
                for path in sorted(pak_children, key=lambda candidate: candidate.name.casefold())
                if path.name.casefold().endswith(".pak")
            )
            run_entries.append((
                run_dir.name,
                self._inventory_path_signature(self._manifest_path(run_dir)),
                pak_dir_state,
                pak_entries,
                self._inventory_path_signature(self._save_snapshot_dir(run_dir)),
            ))

        mods_dir = self.mods_dir
        mods_children, mods_root_state = self._inventory_children(mods_dir)
        installed_entries = tuple(
            (path.name, self._inventory_path_signature(path))
            for path in sorted(mods_children, key=lambda candidate: candidate.name.casefold())
            if KH3_AP_SEED_PAK_PATTERN.fullmatch(path.name) is not None
        )

        bridge_root = self.bridge_root
        bridge_children, bridge_root_state = self._inventory_children(bridge_root)
        bridge_entries = tuple(
            (
                path.name,
                self._inventory_path_signature(Path(path.path) / "kh3_ap_seed_metadata.json"),
                self._inventory_path_signature(Path(path.path) / "slot_data.json"),
            )
            for path in sorted(bridge_children, key=lambda candidate: candidate.name.casefold())
            if path.is_dir(follow_symlinks=False) and not path.name.startswith(".")
        )
        return (
            self.platform, tuple(sorted(self._legacy_platforms.items())),
            (
                str(run_root),
                run_root_state,
                self._inventory_path_signature(self._active_run_path),
                tuple(run_entries),
            ),
            (str(mods_dir), mods_root_state, installed_entries),
            (str(bridge_root), bridge_root_state, bridge_entries),
        )

    def _manifest_to_run(
        self,
        run_dir: Path,
        payload: dict[str, Any],
    ) -> KH3RunInfo | None:
        if (
            payload.get("format") != KH3_RUN_MANIFEST_FORMAT
            or payload.get("version") != KH3_RUN_MANIFEST_VERSION
            or payload.get("run_id") != run_dir.name
        ):
            return None
        try:
            seed_id = str(payload["seed_id"])
            player = int(payload["player"])
            slot_name = str(payload["slot_name"])
            pak_name = Path(str(payload["pak_name"])).name
        except (KeyError, TypeError, ValueError):
            return None
        platform = str(payload.get("platform") or self._legacy_platforms.get(run_dir.name, ""))
        if platform not in {"", "steam", "egs"}:
            return None
        base_run_id = self.run_id_for_identity(seed_id, player, slot_name, platform=platform)
        server_address = str(payload.get("server_address") or "").strip()
        connection_slot_name = str(payload.get("connection_slot_name") or "").strip()
        affinity_run_id = self.run_id_for_connection(
            seed_id,
            player,
            slot_name,
            server_address=server_address,
            connection_slot_name=connection_slot_name or slot_name,
            platform=platform,
        )
        legacy_base = self.run_id_for_identity(seed_id, player, slot_name)
        legacy_affinity = self.run_id_for_connection(
            seed_id, player, slot_name, server_address=server_address,
            connection_slot_name=connection_slot_name or slot_name,
        )
        if (
            run_dir.name not in {base_run_id, affinity_run_id, legacy_base, legacy_affinity}
            or KH3_AP_SEED_PAK_PATTERN.fullmatch(pak_name) is None
        ):
            return None
        run_id = run_dir.name
        identity = _PakIdentity(base_run_id, seed_id, player, slot_name, pak_name, run_dir)
        managed_pak_path = self._managed_pak_path(run_dir, pak_name)
        snapshot_dir = self._save_snapshot_dir(run_dir)
        # Do not trust legacy profile manifests here. Older builds appended the
        # connection-affinity hash and could point a restarted client away from
        # the bridge that the already-loaded game probe was still using.
        bridge_dir_name = self.bridge_dir_name_for_run(seed_id, slot_name, run_id)
        bridge_dirs = self._bridge_dirs_for_run(
            identity,
            run_id=run_id,
            bridge_dir_name=bridge_dir_name,
        )
        room_url = normalize_kh3_room_page_url(payload.get("room_url"))
        if room_url is None:
            for bridge_dir in bridge_dirs:
                try:
                    bridge_slot_data = load_optional_json_file(bridge_dir / "slot_data.json")
                except (BridgeProtocolError, ValueError, OSError):
                    # Room URLs are optional inventory annotations. Corrupt or
                    # incompatible metadata must not prevent listing new runs.
                    continue
                room_url = kh3_room_page_url_from_slot_data(bridge_slot_data, server_address)
                if room_url is not None:
                    break
        return KH3RunInfo(
            run_id=run_id,
            seed_id=seed_id,
            player=player,
            slot_name=slot_name,
            pak_name=pak_name,
            managed=True,
            active=False,
            installed=False,
            pak_available=managed_pak_path.is_file(),
            has_save=self._has_gameplay_save_files(snapshot_dir),
            run_dir=run_dir,
            managed_pak_path=managed_pak_path,
            installed_pak_path=None,
            save_snapshot_dir=snapshot_dir,
            bridge_dirs=bridge_dirs,
            added_at_utc=str(payload.get("added_at_utc") or ""),
            last_activated_at_utc=str(payload.get("last_activated_at_utc") or ""),
            server_address=server_address,
            connection_slot_name=connection_slot_name,
            room_url=room_url or "",
            connection_host=normalize_kh3_run_server_host(server_address),
            bridge_dir_name=bridge_dir_name,
            platform=platform,
        )

    def _build_run_inventory(self) -> list[KH3RunInfo]:
        installed_paks = self._installed_paks()
        runs: list[KH3RunInfo] = []
        run_root = self.run_root
        if run_root.is_dir():
            for run_dir in sorted(run_root.iterdir(), key=lambda path: path.name.casefold()):
                if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name.startswith("."):
                    continue
                payload = load_optional_json_file(self._manifest_path(run_dir))
                if not isinstance(payload, dict):
                    continue
                run = self._manifest_to_run(run_dir, payload)
                if run is not None:
                    runs.append(run)

        marker_run_id = self._read_active_run_id()
        installed_assignments: dict[tuple[str, int, str, str], str] = {}
        if len(installed_paks) == 1:
            identity = installed_paks[0]
            key = self._pak_key_for_identity(identity)
            candidates = [run for run in runs if self.matches_platform(run) and self._pak_key_for_run(run) == key]
            marked = next((run for run in candidates if run.run_id == marker_run_id), None)
            if marked is None and len(candidates) == 1:
                marked = candidates[0]
            if marked is None:
                base = self.run_id_for_identity(identity.seed_id, identity.player, identity.slot_name, platform=self.platform)
                marked = next((run for run in candidates if run.run_id == base), None)
            if marked is not None:
                installed_assignments[key] = marked.run_id
        elif len(installed_paks) > 1:
            for identity in installed_paks:
                key = self._pak_key_for_identity(identity)
                candidates = [run for run in runs if self.matches_platform(run) and self._pak_key_for_run(run) == key]
                if len(candidates) == 1:
                    installed_assignments[key] = candidates[0].run_id

        installed_paths = {
            self._pak_key_for_identity(identity): identity.path
            for identity in installed_paks
        }
        active_run_id = (
            next(iter(installed_assignments.values()))
            if len(installed_paks) == 1 and len(installed_assignments) == 1
            else None
        )
        runs = [
            replace(
                run,
                active=run.run_id == active_run_id,
                installed=installed_assignments.get(self._pak_key_for_run(run)) == run.run_id,
                installed_pak_path=(
                    installed_paths.get(self._pak_key_for_run(run))
                    if installed_assignments.get(self._pak_key_for_run(run)) == run.run_id
                    else None
                ),
            )
            for run in runs
        ]

        assigned_keys = set(installed_assignments)
        for identity in installed_paks:
            key = self._pak_key_for_identity(identity)
            if key in assigned_keys:
                continue
            runs.append(KH3RunInfo(
                run_id=self.run_id_for_identity(identity.seed_id, identity.player, identity.slot_name, platform=self.platform),
                seed_id=identity.seed_id,
                player=identity.player,
                slot_name=identity.slot_name,
                pak_name=identity.pak_name,
                managed=False,
                active=False,
                installed=True,
                pak_available=True,
                has_save=False,
                run_dir=None,
                managed_pak_path=None,
                installed_pak_path=identity.path,
                platform=self.platform,
                save_snapshot_dir=None,
                bridge_dirs=self._bridge_dirs_for_identity(identity),
                bridge_dir_name=self.bridge_dir_name_for_run(
                    identity.seed_id,
                    identity.slot_name,
                    identity.run_id,
                ),
            ))

        runs.sort(key=lambda run: (run.slot_name.casefold(), run.seed_id))
        runs.sort(key=lambda run: run.last_activated_at_utc, reverse=True)
        runs.sort(key=lambda run: not run.installed)
        runs.sort(key=lambda run: not run.active)
        return runs

    def list_runs(self, *, force_refresh: bool = False) -> list[KH3RunInfo]:
        """Return the run inventory, rebuilding only when shallow source metadata changes."""

        with self._inventory_cache_lock:
            signature = self._inventory_source_signature()
            if (
                not force_refresh
                and self._inventory_cache is not None
                and signature == self._inventory_cache_signature
            ):
                return list(self._inventory_cache)
            runs = tuple(self._build_run_inventory())
            self._inventory_cache = runs
            self._inventory_cache_signature = signature
            return list(runs)

    def get_run(self, run_id: str) -> KH3RunInfo:
        for run in self.list_runs():
            if run.run_id == run_id:
                return run
        raise KH3RunManagerError(f"KH3 run is no longer available: {run_id}")

    def active_connection_profile(self) -> KH3RunConnectionProfile | None:
        """Read only the active run manifest fields needed before automatic reconnect."""

        run = next((candidate for candidate in self.list_runs() if candidate.active and candidate.managed), None)
        if run is None:
            return None
        return KH3RunConnectionProfile(
            run_id=run.run_id,
            slot_name=run.slot_name,
            connection_slot_name=run.connection_slot_name,
            server_address=run.server_address,
            room_url=run.room_url,
            connection_host=run.connection_host,
            bridge_dir_name=run.bridge_dir_name,
        )

    def _managed_run_for_connection(
        self,
        identity: _PakIdentity,
        *,
        server_address: str,
        connection_slot_name: str,
    ) -> KH3RunInfo | None:
        key = self._pak_key_for_identity(identity)
        affinity = kh3_run_connection_affinity(server_address, connection_slot_name)
        matching = [
            run
            for run in self.list_runs()
            if run.managed
            and self.matches_platform(run)
            and self._pak_key_for_run(run) == key
            and kh3_run_connection_affinity(run.server_address, run.effective_slot_name) == affinity
        ]
        if len(matching) > 1:
            raise KH3RunManagerError(
                f"Multiple managed KH3 runs already use {normalize_kh3_run_server_host(server_address)} "
                f"and slot {connection_slot_name!r}. Delete the duplicate before connecting."
            )
        return matching[0] if matching else None

    def import_pak(
        self,
        pak_path: Path | str,
        *,
        server_address: str | None = None,
        connection_slot_name: str | None = None,
        room_url: str | None = None,
        _target_run_id: str | None = None,
    ) -> KH3RunInfo:
        identity = self.pak_identity(pak_path)
        address = str(server_address or "").strip()
        connection_name = str(connection_slot_name or "").strip()
        if bool(address) != bool(connection_name):
            raise KH3RunManagerError(
                "A managed KH3 connection profile requires both a server address and slot name."
            )
        target_run_id = str(_target_run_id or "").strip()
        if not target_run_id:
            target_run_id = (
                self.run_id_for_connection(
                    identity.seed_id,
                    identity.player,
                    identity.slot_name,
                    server_address=address,
                    connection_slot_name=connection_name,
                    platform=self.platform,
                )
                if address
                else self.run_id_for_identity(identity.seed_id, identity.player, identity.slot_name, platform=self.platform)
            )
        run_root = self.run_root
        run_root.mkdir(parents=True, exist_ok=True)
        run_dir = self._assert_direct_child(run_root, run_root / target_run_id, label="run import")
        run_dir_preexisted = run_dir.exists()
        manifest_path = self._manifest_path(run_dir)
        existing = load_optional_json_file(manifest_path) if run_dir.is_dir() else None
        if run_dir.exists() and existing is None and any(run_dir.iterdir()):
            raise KH3RunManagerError(f"Run storage already exists without a valid manifest: {run_dir}")
        existing_payload = existing if isinstance(existing, dict) else {}
        destination = self._managed_pak_path(run_dir, identity.pak_name)
        now = self.now_utc()
        stored_server_address = address or str(existing_payload.get("server_address") or "").strip()
        stored_connection_slot = connection_name or str(existing_payload.get("connection_slot_name") or "").strip()
        stored_room_url = str(existing_payload.get("room_url") or "").strip()
        if room_url is not None:
            requested_room_url = str(room_url or "").strip()
            stored_room_url = (
                normalize_kh3_room_page_url(requested_room_url) or ""
                if requested_room_url
                else ""
            )
            if requested_room_url and not stored_room_url:
                raise KH3RunManagerError("The connected KH3 room URL is invalid.")
        bridge_dir_name = self.bridge_dir_name_for_run(
            identity.seed_id,
            identity.slot_name,
            target_run_id,
        )
        payload = {
            "format": KH3_RUN_MANIFEST_FORMAT,
            "version": KH3_RUN_MANIFEST_VERSION,
            "run_id": target_run_id,
            "platform": self.platform,
            "seed_id": identity.seed_id,
            "player": identity.player,
            "slot_name": identity.slot_name,
            "pak_name": identity.pak_name,
            "added_at_utc": existing_payload.get("added_at_utc") or now,
            "updated_at_utc": now,
            "last_activated_at_utc": existing_payload.get("last_activated_at_utc") or "",
            "server_address": stored_server_address,
            "connection_slot_name": stored_connection_slot,
            "room_url": stored_room_url,
            "bridge_dir_name": bridge_dir_name,
        }
        try:
            if identity.path != destination and not self._same_file_version(identity.path, destination):
                self._atomic_copy_file(identity.path, destination)
            atomic_write_json(manifest_path, payload)
        except OSError as exc:
            if not run_dir_preexisted:
                shutil.rmtree(run_dir, ignore_errors=True)
            raise KH3RunManagerError(f"Could not import KH3 seed pak into {run_dir}: {exc}") from exc
        for stale_pak in destination.parent.glob("*.pak"):
            if stale_pak != destination:
                try:
                    stale_pak.unlink()
                except OSError:
                    pass
        self.invalidate_inventory_cache()
        return self.get_run(target_run_id)

    def store_seed_metadata(self, run_id: str, metadata: dict[str, Any]) -> Path:
        """Cache validated runtime metadata and its documents for the managed run."""

        run = self.get_run(run_id)
        if not run.managed or run.run_dir is None or run.managed_pak_path is None:
            raise KH3RunManagerError("Import the KH3 run before storing its seed metadata.")
        if not kh3_ap_seed_metadata_matches_pak(metadata, run.managed_pak_path):
            raise KH3RunManagerError(
                f"The KH3 seed metadata does not match managed run {run.slot_name} "
                f"(seed {run.seed_id}, player {run.player})."
            )

        metadata = metadata_payload(metadata)
        documents = {"slot_data.json": metadata.get("slot_data", {})}
        encode_document("kh3_ap_seed_metadata.json", metadata)
        bridge_root = self.bridge_root
        bridge_dir_name = run.bridge_dir_name or self.bridge_dir_name_for_run(
            run.seed_id,
            run.slot_name,
            run.run_id,
        )
        bridge_dir = bridge_root / bridge_dir_name
        self._assert_direct_child(bridge_root, bridge_dir, label="KH3 bridge metadata write")
        metadata_path = bridge_dir / "kh3_ap_seed_metadata.json"
        try:
            bridge_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(metadata_path, metadata)
            for filename, document in documents.items():
                atomic_write_json(bridge_dir / filename, document)
        except OSError as exc:
            raise KH3RunManagerError(f"Could not store KH3 seed metadata in {bridge_dir}: {exc}") from exc
        self.invalidate_inventory_cache()
        return metadata_path

    def ensure_pak_managed(
        self,
        pak_path: Path | str,
        *,
        expected_seed_id: str | None = None,
        server_address: str | None = None,
        connection_slot_name: str | None = None,
        room_url: str | None = None,
    ) -> KH3RunEnrollmentResult:
        """Import an installed pak once, refreshing storage only when that file changes."""

        identity = self.pak_identity(pak_path)
        expected_seed = SeedGuardService.extract_seed_id(expected_seed_id)
        if expected_seed is not None and identity.seed_id != expected_seed:
            raise KH3RunManagerError(
                f"Installed KH3 seed pak is for {identity.seed_id}, not the connected seed {expected_seed}."
            )
        address = str(server_address or "").strip()
        connection_name = str(connection_slot_name or "").strip()
        if bool(address) != bool(connection_name):
            raise KH3RunManagerError(
                "A connected KH3 run requires both its AP server address and slot name."
            )
        existing = (
            self._managed_run_for_connection(
                identity,
                server_address=address,
                connection_slot_name=connection_name,
            )
            if address
            else next((
                run
                for run in self.list_runs()
                if run.managed and self.matches_platform(run) and self._pak_key_for_run(run) == self._pak_key_for_identity(identity)
            ), None)
        )
        if existing is None and address:
            unbound = next((
                run
                for run in self.list_runs()
                if run.managed
                and self.matches_platform(run)
                and run.run_id == self.run_id_for_identity(identity.seed_id, identity.player, identity.slot_name, platform=self.platform)
                and not kh3_run_connection_affinity(run.server_address, run.effective_slot_name)
            ), None)
            if unbound is not None:
                existing = unbound
        target_run_id = (
            existing.run_id
            if existing is not None
            else self.run_id_for_connection(
                identity.seed_id,
                identity.player,
                identity.slot_name,
                server_address=address,
                connection_slot_name=connection_name,
                platform=self.platform,
            )
        )
        if (
            existing is not None
            and existing.managed
            and existing.managed_pak_path is not None
            and existing.pak_name == identity.pak_name
            and self._same_file_version(identity.path, existing.managed_pak_path)
        ):
            stored_manifest = (
                load_optional_json_file(self._manifest_path(existing.run_dir))
                if existing.run_dir is not None
                else None
            )
            stored_bridge_dir_name = (
                str(stored_manifest.get("bridge_dir_name") or "").strip()
                if isinstance(stored_manifest, dict)
                else ""
            )
            bridge_manifest_needs_normalization = stored_bridge_dir_name != existing.bridge_dir_name
            if bridge_manifest_needs_normalization or (
                address
                and (
                    existing.server_address != address
                    or existing.connection_slot_name != connection_name
                    or room_url is not None
                )
            ):
                refreshed = self.import_pak(
                    identity.path,
                    server_address=address or None,
                    connection_slot_name=connection_name or None,
                    room_url=room_url,
                    _target_run_id=target_run_id,
                )
                return KH3RunEnrollmentResult(refreshed, False)
            return KH3RunEnrollmentResult(existing, False)
        return KH3RunEnrollmentResult(self.import_pak(
            identity.path,
            server_address=address or None,
            connection_slot_name=connection_name or None,
            room_url=room_url,
            _target_run_id=target_run_id,
        ), True)

    def update_connection_profile(
        self,
        run_id: str,
        *,
        slot_name: str,
        server: str,
        port: str | int,
        room_url: str,
    ) -> KH3RunInfo:
        """Atomically store the editable login, server, and room-page fields for one run."""

        connection_slot_name = str(slot_name or "").strip()
        if (
            not connection_slot_name
            or len(connection_slot_name) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in connection_slot_name)
        ):
            raise KH3RunManagerError("Enter a valid AP slot name for this KH3 run.")
        server_address = format_kh3_run_server_address(server, port)
        requested_room_url = str(room_url or "").strip()
        normalized_room_url = ""
        if requested_room_url:
            normalized_room_url = normalize_kh3_room_page_url(requested_room_url) or ""
            if not normalized_room_url:
                raise KH3RunManagerError("Enter a valid HTTP or HTTPS room URL, or leave it blank.")
        run = self.get_run(run_id)
        if not run.managed or run.run_dir is None:
            raise KH3RunManagerError("Import the KH3 run before editing its connection profile.")
        previous_affinity = kh3_run_connection_affinity(run.server_address, run.effective_slot_name)
        next_affinity = kh3_run_connection_affinity(server_address, connection_slot_name)
        next_run_id = run.run_id
        if previous_affinity and previous_affinity != next_affinity:
            next_run_id = self.run_id_for_connection(
                run.seed_id,
                run.player,
                run.slot_name,
                server_address=server_address,
                connection_slot_name=connection_slot_name,
                platform=run.platform,
            )
        next_run_dir = self._assert_direct_child(
            self.run_root,
            self.run_root / next_run_id,
            label="connection profile update",
        )
        if next_run_dir != run.run_dir and next_run_dir.exists():
            raise KH3RunManagerError(
                "Another KH3 run already uses that server hostname and slot name. Edit or delete that entry instead."
            )
        manifest_path = self._manifest_path(run.run_dir)
        payload = load_optional_json_file(manifest_path)
        if not isinstance(payload, dict):
            raise KH3RunManagerError(f"Managed KH3 run metadata is missing: {manifest_path}")
        payload["run_id"] = next_run_id
        payload["connection_slot_name"] = connection_slot_name
        payload["server_address"] = server_address
        payload["room_url"] = normalized_room_url
        payload["bridge_dir_name"] = self.bridge_dir_name_for_run(
            run.seed_id,
            run.slot_name,
            next_run_id,
        )
        payload["updated_at_utc"] = self.now_utc()
        previous_active_run_id = self._read_active_run_id()
        moved = False
        try:
            if next_run_dir != run.run_dir:
                os.replace(run.run_dir, next_run_dir)
                moved = True
                manifest_path = self._manifest_path(next_run_dir)
            atomic_write_json(manifest_path, payload)
            if previous_active_run_id == run.run_id:
                self._write_active_run_id(next_run_id)
        except (OSError, KH3RunManagerError) as exc:
            if moved:
                try:
                    os.replace(next_run_dir, run.run_dir)
                except OSError:
                    pass
            raise KH3RunManagerError(f"Could not store the KH3 run's connection profile: {exc}") from exc
        self.invalidate_inventory_cache()
        return self.get_run(next_run_id)

    def install_managed_pak_when_none(
        self,
        run_id: str,
        *,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
    ) -> KH3RunInfo:
        """Install a generated pak through the same transactional save switch as a manual activation."""

        if self._installed_paks():
            raise KH3RunManagerError(
                "A KH3 AP seed pak appeared while the generated pak was being prepared; no pak was installed."
            )
        return self.activate(
            run_id,
            save_dir_override=save_dir_override,
            active_save_seed_id=active_save_seed_id,
            active_save_run_id=active_save_run_id,
        ).run

    def _snapshot_current_save(self, run: KH3RunInfo, save_dir: Path) -> tuple[int, tuple[str, ...]]:
        if run.run_dir is None:
            raise KH3RunManagerError(f"Cannot snapshot unmanaged KH3 run: {run.run_id}")
        run_dir = run.run_dir
        stage = run_dir / f".save-stage-{uuid.uuid4().hex}"
        snapshot = self._save_snapshot_dir(run_dir)
        old_snapshot = run_dir / f".save-old-{uuid.uuid4().hex}"
        copied = 0
        warnings: list[str] = []
        try:
            checked_path(self.run_root, run_dir)
            checked_root(snapshot)
            copied = self._copy_gameplay_save_files(save_dir, stage)
            if self.process_running():
                raise KH3RunManagerError(
                    "KINGDOM HEARTS III started while the current run was being saved; no game files changed."
                )
            if copied == 0 and self._has_gameplay_save_files(snapshot):
                warnings.append(
                    f"Active save folder was empty; retained the previous stored snapshot for {run.slot_name}."
                )
                return 0, tuple(warnings)
            if snapshot.exists():
                checked_path(run_dir, snapshot)
                checked_path(run_dir, old_snapshot)
                os.replace(snapshot, old_snapshot)
            try:
                checked_path(run_dir, stage)
                checked_path(run_dir, snapshot)
                os.replace(stage, snapshot)
            except OSError:
                if old_snapshot.exists() and not snapshot.exists():
                    checked_path(run_dir, old_snapshot)
                    checked_path(run_dir, snapshot)
                    os.replace(old_snapshot, snapshot)
                raise
            if old_snapshot.exists():
                try:
                    for path in inspect_save_tree(old_snapshot).files:
                        checked_save(old_snapshot, path).unlink()
                    remove_empty_save_directories(old_snapshot)
                    if any(old_snapshot.iterdir()):
                        warnings.append(f"Preserved unrecognized files from the older save snapshot at {old_snapshot}.")
                    else:
                        checked_root(old_snapshot).rmdir()
                except OSError as exc:
                    warnings.append(f"Could not remove older save snapshot {old_snapshot}: {exc}")
            payload = load_optional_json_file(self._manifest_path(run_dir)) or {}
            if isinstance(payload, dict):
                payload["updated_at_utc"] = self.now_utc()
                payload["save_snapshot_file_count"] = copied
                atomic_write_json(self._manifest_path(run_dir), payload)
            self.invalidate_inventory_cache()
            return copied, tuple(warnings)
        except OSError as exc:
            raise KH3RunManagerError(f"Could not snapshot KH3 saves for {run.slot_name}: {exc}") from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def _update_last_activated(self, run: KH3RunInfo) -> None:
        if run.run_dir is None:
            return
        manifest_path = self._manifest_path(run.run_dir)
        payload = load_optional_json_file(manifest_path)
        if not isinstance(payload, dict):
            return
        now = self.now_utc()
        payload["updated_at_utc"] = now
        payload["last_activated_at_utc"] = now
        atomic_write_json(manifest_path, payload)
        self.invalidate_inventory_cache()

    def _resolve_active_save_run(
        self,
        current_run: KH3RunInfo,
        active_save_files: list[Path],
        associated_seed_id: str | None,
        associated_run_id: str | None,
        *,
        preferred_run: KH3RunInfo | None = None,
    ) -> tuple[KH3RunInfo, str | None]:
        """Select the managed run that owns live saves when the installed pak is stale."""

        if not active_save_files:
            return current_run, None
        normalized_run_id = str(associated_run_id or "").strip()
        if normalized_run_id:
            try:
                associated_run = self.get_run(normalized_run_id)
            except KH3RunManagerError as exc:
                raise KH3RunManagerError(
                    f"The active saves are associated with managed run {normalized_run_id}, but that run is missing. "
                    "Restore or import the exact run before continuing."
                ) from exc
            if not associated_run.managed or not associated_run.pak_available:
                raise KH3RunManagerError(
                    f"The active saves are associated with {normalized_run_id}, but its managed seed pak is missing."
                )
            if not self.matches_platform(associated_run):
                raise KH3RunManagerError("The active save association belongs to another platform or an unassigned legacy run.")
            if associated_seed_id and associated_run.seed_id != associated_seed_id:
                raise KH3RunManagerError(
                    f"The active save association disagrees: run {normalized_run_id} uses seed "
                    f"{associated_run.seed_id}, not {associated_seed_id}."
                )
            if associated_run.run_id == current_run.run_id:
                return current_run, None
            return associated_run, (
                f"The installed pak profile was {current_run.run_id}, but the active saves belong to managed run "
                f"{associated_run.run_id}; stored the saves with their matching server profile."
            )
        if associated_seed_id is None or associated_seed_id == current_run.seed_id:
            return current_run, None
        if (
            preferred_run is not None
            and preferred_run.managed
            and preferred_run.pak_available
            and preferred_run.seed_id == associated_seed_id
        ):
            save_run = preferred_run
        else:
            matches = [
                run
                for run in self.list_runs()
                if run.managed and self.matches_platform(run) and run.pak_available and run.seed_id == associated_seed_id
            ]
            if len(matches) != 1:
                detail = "no matching managed run" if not matches else f"{len(matches)} matching managed runs"
                raise KH3RunManagerError(
                    f"The active saves are associated with seed {associated_seed_id}, but the installed pak is for "
                    f"{current_run.seed_id}, and Save Manager found {detail} for those saves. Import or activate the "
                    "exact matching run before continuing."
                )
            save_run = matches[0]
        return save_run, (
            f"The installed pak was for seed {current_run.seed_id}, but the active saves belong to managed run "
            f"{save_run.slot_name} (seed {save_run.seed_id}); stored the saves with their matching run."
        )

    def suspend_installed_run(
        self,
        *,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
    ) -> KH3RunSuspensionResult:
        """Preserve the current run and its saves, then remove only its live seed pak."""

        if self.process_running():
            raise KH3RunManagerError("Close KINGDOM HEARTS III before changing the active seed pak.")
        installed = self._installed_paks()
        if len(installed) != 1:
            raise KH3RunManagerError(
                f"Expected one installed KH3 AP seed pak to preserve, but found {len(installed)}."
            )
        current_identity = installed[0]
        save_dir = (
            self._save_root(Path(save_dir_override))
            if save_dir_override is not None
            else self.save_dir
        )
        active_save_files = self._iter_gameplay_save_files(save_dir)
        associated_seed_id = SeedGuardService.extract_seed_id(active_save_seed_id)
        current_run = self._active_managed_run_for_identity(current_identity)
        if current_run is None:
            current_run = self.import_pak(current_identity.path)
        save_run, ownership_warning = self._resolve_active_save_run(
            current_run,
            active_save_files,
            associated_seed_id,
            active_save_run_id,
        )
        saved_current_files, snapshot_warnings = self._snapshot_current_save(save_run, save_dir)
        warnings = tuple(([ownership_warning] if ownership_warning else []) + list(snapshot_warnings))
        if self.process_running():
            raise KH3RunManagerError(
                "KINGDOM HEARTS III started while the current run was being preserved; its pak was not removed."
            )
        refreshed = self._installed_paks()
        if len(refreshed) != 1 or refreshed[0].run_id != current_identity.run_id:
            raise KH3RunManagerError(
                "The installed KH3 seed pak changed while the current run was being preserved; no pak was removed."
            )
        try:
            current_identity.path.unlink()
        except OSError as exc:
            raise KH3RunManagerError(
                f"The current run was preserved, but its live seed pak could not be removed: {exc}"
            ) from exc
        try:
            self._write_active_run_id(save_run.run_id)
        except (OSError, KH3RunManagerError) as exc:
            raise KH3RunManagerError(
                f"The current run was preserved and its live pak removed, but save ownership could not be stored: {exc}"
            ) from exc
        self.invalidate_inventory_cache()
        refreshed_current_run = self.get_run(current_run.run_id)
        refreshed_save_run = (
            refreshed_current_run
            if save_run.run_id == current_run.run_id
            else self.get_run(save_run.run_id)
        )
        return KH3RunSuspensionResult(
            run=refreshed_current_run,
            saved_current_files=saved_current_files,
            save_dir=save_dir,
            warnings=warnings,
            save_run=refreshed_save_run,
        )

    def activate(
        self,
        run_id: str,
        *,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
    ) -> KH3RunActivationResult:
        if self.process_running():
            raise KH3RunManagerError("Close KINGDOM HEARTS III before switching managed runs.")
        target = self.get_run(run_id)
        if not self.matches_platform(target):
            raise KH3RunManagerError("Select this run's Steam or EGS installation before activating it.")
        if not target.managed or target.managed_pak_path is None or not target.managed_pak_path.is_file():
            raise KH3RunManagerError("Import this run's seed pak before activating it.")
        managed_identity = self.pak_identity(target.managed_pak_path)
        if self._pak_key_for_identity(managed_identity) != self._pak_key_for_run(target):
            raise KH3RunManagerError(
                f"Stored seed pak no longer matches managed run {target.run_id}: {target.managed_pak_path}"
            )

        installed = self._installed_paks()
        if len(installed) > 1:
            names = ", ".join(identity.pak_name for identity in installed)
            raise KH3RunManagerError(
                f"Multiple KH3 AP seed paks are installed: {names}. Remove the conflict before switching runs."
            )
        current_identity = installed[0] if installed else None
        save_dir = (
            self._save_root(Path(save_dir_override))
            if save_dir_override is not None
            else self.save_dir
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        active_save_files = self._iter_gameplay_save_files(save_dir)
        associated_seed_id = SeedGuardService.extract_seed_id(active_save_seed_id)
        associated_run_id = str(active_save_run_id or "").strip() or None
        current_run = (
            self._active_managed_run_for_identity(current_identity)
            if current_identity is not None
            else None
        )
        if current_identity is not None and current_run is None:
            current_run = self.import_pak(current_identity.path)
        if current_run is None and associated_run_id:
            try:
                candidate = self.get_run(associated_run_id)
            except KH3RunManagerError:
                candidate = None
            if candidate is not None and candidate.managed and candidate.pak_available:
                current_run = candidate
        if (
            current_identity is not None
            and current_run is not None
            and current_run.run_id == target.run_id
            and (
                (not active_save_files and not target.has_save)
                or (
                    active_save_files
                    and associated_run_id in {None, target.run_id}
                    and (associated_seed_id is None or associated_seed_id == target.seed_id)
                )
            )
        ):
            self._write_active_run_id(target.run_id)
            refreshed_target = self.get_run(target.run_id)
            return KH3RunActivationResult(refreshed_target, target.run_id, False, 0, 0, save_dir)
        if current_identity is None and active_save_files and current_run is None:
            raise KH3RunManagerError(
                "The active save folder contains gameplay saves but no managed server profile identifies them. "
                "Use Restore Vanilla KH3 before activating a managed AP run."
            )

        warnings: list[str] = []
        saved_current_files = 0
        if current_run is not None:
            save_run, ownership_warning = self._resolve_active_save_run(
                current_run,
                active_save_files,
                associated_seed_id,
                associated_run_id,
                preferred_run=target,
            )
            if ownership_warning:
                warnings.append(ownership_warning)
            saved_current_files, snapshot_warnings = self._snapshot_current_save(save_run, save_dir)
            warnings.extend(snapshot_warnings)

        mods_dir = self.mods_dir
        mods_dir.mkdir(parents=True, exist_ok=True)
        save_dir.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        target_save_stage = Path(tempfile.mkdtemp(
            prefix=f".{save_dir.name}-kh3-run-target-{token}-",
            dir=save_dir.parent,
        ))
        save_rollback = Path(tempfile.mkdtemp(
            prefix=f".{save_dir.name}-kh3-run-rollback-{token}-",
            dir=save_dir.parent,
        ))
        target_pak_stage = mods_dir / f".{target.pak_name}.{token}.stage"
        current_pak_rollback = mods_dir / f".{current_identity.pak_name}.{token}.rollback" if current_identity else None
        target_installed_path = mods_dir / target.pak_name
        restored_target_files = 0
        active_saves_archived = False
        current_pak_archived = False
        target_pak_installed = False
        previous_active_run_id = self._read_active_run_id()
        active_marker_changed = False

        try:
            if target.save_snapshot_dir is not None:
                self._copy_gameplay_save_files(target.save_snapshot_dir, target_save_stage)
            self._atomic_copy_file(target.managed_pak_path, target_pak_stage)
            if self.process_running():
                raise KH3RunManagerError(
                    "KINGDOM HEARTS III started while the run switch was being prepared; no files changed."
                )

            self._move_gameplay_save_files(save_dir, save_rollback)
            active_saves_archived = True
            if current_identity is not None and current_pak_rollback is not None:
                os.replace(current_identity.path, current_pak_rollback)
                current_pak_archived = True
            os.replace(target_pak_stage, target_installed_path)
            target_pak_installed = True
            restored_target_files = self._move_gameplay_save_files(target_save_stage, save_dir)
            self._write_active_run_id(target.run_id)
            active_marker_changed = True
        except Exception as exc:
            rollback_errors: list[str] = []
            if active_marker_changed:
                try:
                    self._restore_active_run_id(previous_active_run_id)
                    active_marker_changed = False
                except Exception as rollback_exc:
                    rollback_errors.append(f"restore active run identity: {rollback_exc}")
            if restored_target_files:
                try:
                    # The marker can fail after the target saves are live. Move
                    # them back out before restoring the old saves, including
                    # slots that exist only in the target run.
                    self._move_gameplay_save_files(save_dir, target_save_stage)
                except Exception as rollback_exc:
                    rollback_errors.append(f"remove restored target saves: {rollback_exc}")
            if target_pak_installed:
                try:
                    target_installed_path.unlink()
                except OSError as rollback_exc:
                    rollback_errors.append(f"remove target pak {target_installed_path}: {rollback_exc}")
            if current_pak_archived and current_pak_rollback is not None and current_identity is not None:
                try:
                    os.replace(current_pak_rollback, current_identity.path)
                    current_pak_archived = False
                except OSError as rollback_exc:
                    rollback_errors.append(f"restore current pak {current_identity.path}: {rollback_exc}")
            if active_saves_archived:
                try:
                    self._move_gameplay_save_files(save_rollback, save_dir)
                    active_saves_archived = False
                except Exception as rollback_exc:
                    rollback_errors.append(f"restore current saves: {rollback_exc}")
            if rollback_errors:
                raise KH3RunManagerError(
                    f"KH3 run switch failed ({exc}); rollback also needs attention: {'; '.join(rollback_errors)}"
                ) from exc
            if isinstance(exc, KH3RunManagerError):
                raise
            raise KH3RunManagerError(f"KH3 run switch failed and was rolled back: {exc}") from exc
        finally:
            if target_pak_stage.exists():
                try:
                    target_pak_stage.unlink()
                except OSError:
                    pass
            shutil.rmtree(target_save_stage, ignore_errors=True)
            if not active_saves_archived:
                try:
                    rollback_has_saves = self._has_gameplay_save_files(save_rollback)
                except KH3RunManagerError:
                    rollback_has_saves = True
                if not rollback_has_saves:
                    shutil.rmtree(save_rollback, ignore_errors=True)

        if current_pak_rollback is not None and current_pak_rollback.exists():
            try:
                current_pak_rollback.unlink()
            except OSError as exc:
                warnings.append(f"Could not remove temporary previous seed pak {current_pak_rollback}: {exc}")
        if save_rollback.exists():
            try:
                shutil.rmtree(save_rollback)
            except OSError as exc:
                warnings.append(f"Could not remove temporary previous save copy {save_rollback}: {exc}")
        try:
            self._update_last_activated(target)
        except OSError as exc:
            warnings.append(f"Could not update managed-run activation metadata: {exc}")
        try:
            activated = self.get_run(target.run_id)
        except Exception as exc:
            warnings.append(f"Could not refresh managed-run inventory after activation: {exc}")
            activated = replace(
                target,
                active=True,
                installed=True,
                installed_pak_path=target_installed_path,
            )
        return KH3RunActivationResult(
            run=activated,
            previous_run_id=current_run.run_id if current_run is not None else None,
            switched=True,
            saved_current_files=saved_current_files,
            restored_target_files=restored_target_files,
            save_dir=save_dir,
            warnings=tuple(warnings),
        )

    def delete_inactive_run(
        self,
        run_id: str,
        *,
        expected_active_run_id: str,
        active_bridge_dir: Path | str | None = None,
        current_platform_only: bool = True,
    ) -> KH3RunDeleteResult:
        """Delete one confirmed bulk target only while the protected run is still active."""
        runs = self.list_runs(force_refresh=True)
        active_ids = [run.run_id for run in runs if run.active]
        if not expected_active_run_id or active_ids != [expected_active_run_id]:
            raise KH3RunManagerError("The active KH3 run changed or is unavailable. Refresh Save Manager and retry.")
        if run_id == expected_active_run_id:
            raise KH3RunManagerError("Delete All Except Active cannot delete the active run.")
        if current_platform_only and not self.matches_platform(self.get_run(run_id)):
            raise KH3RunManagerError("Bulk cleanup cannot delete another platform's runs.")
        return self.delete_run(run_id, active_bridge_dir=active_bridge_dir)

    def delete_run(
        self,
        run_id: str,
        *,
        active_bridge_dir: Path | str | None = None,
        allow_active: bool = False,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
    ) -> KH3RunDeleteResult:
        run = self.get_run(run_id)
        if not run.managed and not run.installed:
            raise KH3RunManagerError("The unmanaged KH3 run is no longer installed.")
        if run.managed and run.run_dir is None:
            raise KH3RunManagerError("The managed KH3 run storage path is missing.")
        active_marker_id = self._read_active_run_id()
        marker_matches = active_marker_id == run_id
        # A remembered marker can outlive its installed pak. It identifies a
        # selected client profile only when the caller acknowledges that profile;
        # installed-run and live-bridge protection remain authoritative below.
        selected_profile = run.active or (marker_matches and allow_active)
        if selected_profile and not allow_active:
            raise KH3RunManagerError(
                "The active run can be deleted only while the Archipelago client is disconnected."
            )
        resolved_active_bridge = (
            Path(active_bridge_dir).expanduser().resolve()
            if active_bridge_dir is not None
            else None
        )
        other_managed_bridge_dirs = {
            path.expanduser().resolve()
            for candidate in self.list_runs()
            if candidate.managed and candidate.run_id != run.run_id
            for path in candidate.bridge_dirs
        }
        bridge_dirs = tuple(
            candidate
            for candidate in (
                self._assert_direct_child(self.bridge_root, path, label="bridge deletion")
                for path in run.bridge_dirs
                if path.exists()
            )
            if candidate not in other_managed_bridge_dirs
        )
        bridge_matches = bool(
            resolved_active_bridge is not None
            and resolved_active_bridge in bridge_dirs
        )
        if bridge_matches and not allow_active:
            raise KH3RunManagerError(
                "The client is still using this run's bridge. Switch runs or restart after Restore Vanilla KH3 first."
            )
        selected_profile = selected_profile or bridge_matches
        clear_active_marker = run.active or marker_matches
        requires_closed_game = selected_profile or run.installed
        if requires_closed_game and self.process_running():
            raise KH3RunManagerError("Close KINGDOM HEARTS III before deleting an installed or selected KH3 run.")

        run_dir = (
            self._assert_direct_child(self.run_root, run.run_dir, label="run deletion")
            if run.run_dir is not None
            else None
        )
        installed_pak: Path | None = None
        save_dir: Path | None = None
        active_save_files: list[Path] = []
        if run.installed:
            installed = self._installed_paks()
            matching_installed = [
                identity
                for identity in installed
                if self._pak_key_for_identity(identity) == self._pak_key_for_run(run)
            ]
            if len(matching_installed) != 1:
                label = "active-run" if run.active else "installed-run"
                raise KH3RunManagerError(
                    f"The installed KH3 seed pak changed before {label} deletion; no files were removed."
                )
            if run.active and len(installed) != 1:
                raise KH3RunManagerError(
                    "The installed KH3 seed pak changed before active-run deletion; no files were removed."
                )
            installed_pak = self._assert_direct_child(
                self.mods_dir,
                matching_installed[0].path,
                label="installed seed-pak deletion",
            )
        if run.active:
            save_dir = (
                self._save_root(Path(save_dir_override))
                if save_dir_override is not None
                else self.save_dir
            )
            active_save_files = self._iter_gameplay_save_files(save_dir)
            associated_seed_id = SeedGuardService.extract_seed_id(active_save_seed_id)
            associated_run_id = str(active_save_run_id or "").strip()
            if associated_run_id and associated_run_id != run.run_id:
                raise KH3RunManagerError(
                    f"The active saves belong to managed run {associated_run_id}, not {run.run_id}; no files "
                    "were removed."
                )
            if associated_seed_id and associated_seed_id != run.seed_id:
                raise KH3RunManagerError(
                    f"The active saves belong to seed {associated_seed_id}, not {run.seed_id}; no files were removed."
                )
        if requires_closed_game and self.process_running():
            raise KH3RunManagerError(
                "KINGDOM HEARTS III started while deletion was being prepared; no run files changed."
            )
        token = uuid.uuid4().hex
        moved_dirs: list[tuple[Path, Path, str]] = []
        installed_pak_quarantine: Path | None = None
        active_marker_quarantine: Path | None = None
        save_quarantine: Path | None = None
        staged_active_save_files = 0
        try:
            if active_save_files and save_dir is not None:
                save_dir.parent.mkdir(parents=True, exist_ok=True)
                save_quarantine = Path(tempfile.mkdtemp(
                    prefix=f".{save_dir.name}-kh3-delete-{token}-",
                    dir=save_dir.parent,
                ))
                staged_active_save_files = self._move_gameplay_save_files(save_dir, save_quarantine)
                if self.process_running():
                    raise KH3RunManagerError(
                        "KINGDOM HEARTS III started while deletion was being staged; the active saves will be "
                        "restored."
                    )
            if installed_pak is not None:
                installed_pak_quarantine = installed_pak.parent / f".{installed_pak.name}.kh3-delete-{token}"
                os.replace(installed_pak, installed_pak_quarantine)
            if clear_active_marker and self._active_run_path.is_file():
                active_marker_quarantine = (
                    self._active_run_path.parent / f".{self._active_run_path.name}.kh3-delete-{token}"
                )
                os.replace(self._active_run_path, active_marker_quarantine)
            deletion_sources = [(path, "bridge") for path in bridge_dirs]
            if run_dir is not None:
                deletion_sources.insert(0, (run_dir, "run"))
            for source, kind in deletion_sources:
                quarantine = source.parent / f".{source.name}.kh3-delete-{token}"
                os.replace(source, quarantine)
                moved_dirs.append((source, quarantine, kind))
        except Exception as exc:
            rollback_errors: list[str] = []
            for original, quarantine, _kind in reversed(moved_dirs):
                try:
                    os.replace(quarantine, original)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{quarantine}: {rollback_exc}")
            if active_marker_quarantine is not None and active_marker_quarantine.exists():
                try:
                    os.replace(active_marker_quarantine, self._active_run_path)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{active_marker_quarantine}: {rollback_exc}")
            if installed_pak_quarantine is not None and installed_pak_quarantine.exists() and installed_pak is not None:
                try:
                    os.replace(installed_pak_quarantine, installed_pak)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{installed_pak_quarantine}: {rollback_exc}")
            if save_quarantine is not None and save_dir is not None:
                try:
                    self._move_gameplay_save_files(save_quarantine, save_dir)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{save_quarantine}: {rollback_exc}")
                try:
                    shutil.rmtree(save_quarantine)
                except OSError:
                    pass
            details = f"; rollback errors: {', '.join(rollback_errors)}" if rollback_errors else ""
            self.invalidate_inventory_cache()
            raise KH3RunManagerError(f"Could not stage KH3 run deletion: {exc}{details}") from exc

        self.invalidate_inventory_cache()
        errors: list[str] = []
        removed_run = False
        removed_bridge_dirs = 0
        removed_installed_pak = False
        removed_active_save_files = 0
        for _original, quarantine, kind in moved_dirs:
            try:
                shutil.rmtree(quarantine)
                if kind == "run":
                    removed_run = True
                else:
                    removed_bridge_dirs += 1
            except OSError as exc:
                errors.append(f"{quarantine}: {exc}")
        if installed_pak_quarantine is not None:
            try:
                installed_pak_quarantine.unlink()
                removed_installed_pak = True
            except OSError as exc:
                errors.append(f"{installed_pak_quarantine}: {exc}")
        if active_marker_quarantine is not None:
            try:
                active_marker_quarantine.unlink()
            except OSError as exc:
                errors.append(f"{active_marker_quarantine}: {exc}")
        if save_quarantine is not None:
            try:
                shutil.rmtree(save_quarantine)
                removed_active_save_files = staged_active_save_files
            except OSError as exc:
                errors.append(f"{save_quarantine}: {exc}")
        return KH3RunDeleteResult(
            run_id=run_id,
            removed_run=removed_run,
            removed_bridge_dirs=removed_bridge_dirs,
            removed_installed_pak=removed_installed_pak,
            removed_active_save_files=removed_active_save_files,
            cleared_active_profile=selected_profile,
            errors=tuple(errors),
        )


__all__ = [
    "KH3_ACTIVE_RUN_FILE",
    "KH3_RUN_MANIFEST_FILE",
    "KH3_RUN_MANIFEST_FORMAT",
    "KH3_RUN_MANIFEST_VERSION",
    "KH3RunActivationResult",
    "KH3RunConnectionProfile",
    "KH3RunDeleteResult",
    "KH3RunEnrollmentResult",
    "KH3RunInfo",
    "KH3RunManagerError",
    "KH3RunManagerService",
    "KH3RunSuspensionResult",
    "format_kh3_run_server_address",
    "kh3_run_connection_affinity",
    "normalize_kh3_connection_slot_name",
    "normalize_kh3_run_server_host",
    "split_kh3_run_server_address",
]
