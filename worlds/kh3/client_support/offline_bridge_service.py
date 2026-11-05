"""Prepare the KH3 filesystem bridge before an AP server connection."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from re import Pattern
from typing import Any

from ..RuntimeSeed import encode_document, metadata_payload, slot_payload
from .seed_paks import kh3_pak_player_name

OFFLINE_BRIDGE_PUBLICATION_MANIFEST_FILE = "offline_bridge_publication.json"
OFFLINE_BRIDGE_PUBLICATION_MANIFEST_FORMAT = "kh3_offline_bridge_publication"
OFFLINE_BRIDGE_PUBLICATION_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class InstalledPakDiscoveryInput:
    """Paths and seed identity used to discover an installed seed pak."""

    mods_dir: Path
    expected_seed: str | None = None
    bridge_dir: Path | None = None


@dataclass(frozen=True)
class OfflineBridgeInitializationInput:
    """Context state required to initialize a disconnected bridge."""

    current_bridge_dir: Path | None
    bridge_root: Path
    mods_dir: Path
    expected_seed: str | None
    auth: str | None
    server_address: str | None
    team: int | None
    bridge_dir_name: str = ""


@dataclass(frozen=True)
class OfflineBridgeInitializationResult:
    """Validated offline state produced for the facade handoff."""

    bridge_dir: Path
    offline_seed_name: str
    auth: str
    slot_data: dict[str, Any]
    location_key_to_id: dict[str, int]
    location_id_to_bridge_key: dict[int, str]
    metadata: dict[str, Any]
    pak_path: Path


class OfflineBridgeService:
    """Own installed-pak discovery and disconnected bridge publication."""

    def __init__(
        self,
        *,
        protocol_name: str,
        protocol_version: int,
        world_version: str,
        seed_pak_pattern: Pattern[str],
        list_pak_paths: Callable[[Path], list[Path]],
        modified_time: Callable[[Path], float],
        load_installed_metadata: Callable[[Path, str | None], tuple[dict[str, Any], Path] | None],
        infer_slot_name_from_pak: Callable[[Path], str | None],
        validate_slot_data: Callable[[dict[str, Any], str], dict[str, Any]],
        validate_protocol: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
        load_optional_json: Callable[[Path], dict[str, Any] | None],
        load_cached_items: Callable[[Path | None], dict[str, Any]],
        atomic_write_json: Callable[[Path, dict[str, Any]], None],
        create_directory: Callable[[Path], None],
        delete_file: Callable[[Path], None],
        safe_name: Callable[[str], str],
        extract_seed_id: Callable[[Any], str | None],
        log_info: Callable[..., None],
    ) -> None:
        self.protocol_name = protocol_name
        self.protocol_version = protocol_version
        self.world_version = world_version
        self.seed_pak_pattern = seed_pak_pattern
        self.list_pak_paths = list_pak_paths
        self.modified_time = modified_time
        self.load_installed_metadata = load_installed_metadata
        self.infer_slot_name_from_pak = infer_slot_name_from_pak
        self.validate_slot_data = validate_slot_data
        self.validate_protocol = validate_protocol
        self.protocol_error = protocol_error
        self.load_optional_json = load_optional_json
        self.load_cached_items = load_cached_items
        self.atomic_write_json = atomic_write_json
        self.create_directory = create_directory
        self.delete_file = delete_file
        self.safe_name = safe_name
        self.extract_seed_id = extract_seed_id
        self.log_info = log_info

    def _load_seed_metadata(self, path: Path) -> dict[str, Any] | None:
        try:
            return self.load_optional_json(path)
        except (self.protocol_error, ValueError, OSError) as exc:
            self.log_info("Ignoring invalid cached KH3 seed metadata %s: %s. "
                          "Will rebuild from the matching installed pak if compatible.", path, exc)
            return None

    @staticmethod
    def normalize_archive_seed_name(value: str | None) -> str:
        """Normalize the archive seed component used in bridge directory names."""
        text = str(value or "").strip()
        if text.startswith("AP_"):
            text = text[3:]
        return text or "kh3"

    def discover_installed_paks(self, source: InstalledPakDiscoveryInput) -> list[dict[str, Any]]:
        """Return installed seed paks in the established path ordering."""
        paks: list[dict[str, Any]] = []
        for pak_path in sorted(self.list_pak_paths(source.mods_dir)):
            match = self.seed_pak_pattern.search(pak_path.name)
            if match is not None:
                pak = {
                    "seed": match.group("seed"),
                    "name": pak_path.name,
                    "path": str(pak_path),
                }
                try:
                    pak["size"] = pak_path.stat().st_size
                except OSError:
                    # The policy service can retry the stat from the recorded path.
                    pass
                paks.append(pak)
        return paks

    def infer_slot_name(self, source: InstalledPakDiscoveryInput) -> str | None:
        """Infer the player name using filename, metadata, then bridge precedence."""
        candidates = sorted(
            self.list_pak_paths(source.mods_dir),
            key=self.modified_time,
            reverse=True,
        )
        for pak_path in candidates:
            if self.seed_pak_pattern.search(pak_path.name) is None:
                continue
            if slot_name := self.infer_slot_name_from_pak(pak_path):
                return slot_name

        slot_name = self._infer_slot_name_from_metadata(source)
        if slot_name:
            return slot_name
        if source.bridge_dir is None:
            return None
        metadata = self._load_seed_metadata(source.bridge_dir / "kh3_ap_seed_metadata.json")
        return self._metadata_slot_name(metadata)

    def _infer_slot_name_from_metadata(self, source: InstalledPakDiscoveryInput) -> str | None:
        try:
            result = self.load_installed_metadata(source.mods_dir, source.expected_seed)
        except Exception as exc:
            self.log_info("Could not infer KH3 slot name from installed pak: %s", exc)
            return None
        if result is None:
            return None
        metadata, pak_path = result
        return self._metadata_slot_name(metadata) or self.infer_slot_name_from_pak(pak_path)

    @staticmethod
    def _metadata_slot_name(metadata: dict[str, Any] | None) -> str | None:
        if not isinstance(metadata, dict):
            return None
        slot_name = metadata.get("slot_name") or metadata.get("kh3_player_name")
        if isinstance(slot_name, str) and slot_name.strip():
            return slot_name.strip()
        return None

    def initialize(
        self,
        source: OfflineBridgeInitializationInput,
    ) -> OfflineBridgeInitializationResult | None:
        """Validate installed metadata and prepare a compatible offline bridge."""
        if source.current_bridge_dir is not None:
            return None
        loaded = self._load_initialization_metadata(source)
        if loaded is None:
            return None
        metadata, pak_path, slot_data = loaded
        seed_name, slot_name = self._offline_names(metadata)
        requested_bridge_name = str(source.bridge_dir_name or "").strip()
        if requested_bridge_name and Path(requested_bridge_name).name != requested_bridge_name:
            self.log_info("Managed KH3 bridge directory name is invalid: %s", requested_bridge_name)
            return None
        bridge_dir = source.bridge_root / (
            requested_bridge_name
            or self.safe_name(f"{seed_name}_{slot_name}")
        )
        self.create_directory(bridge_dir)
        bridge_data = slot_data.get("bridge", {})
        key_to_id = {entry["bridge_key"]: entry["ap_location_id"] for entry in bridge_data["locations"]}
        id_to_key = {entry["ap_location_id"]: entry["bridge_key"] for entry in bridge_data["locations"]}
        return OfflineBridgeInitializationResult(
            bridge_dir=bridge_dir,
            offline_seed_name=seed_name,
            auth=source.auth or slot_name,
            slot_data=slot_data,
            location_key_to_id=key_to_id,
            location_id_to_bridge_key=id_to_key,
            metadata=metadata,
            pak_path=pak_path,
        )

    def _load_initialization_metadata(
        self,
        source: OfflineBridgeInitializationInput,
    ) -> tuple[dict[str, Any], Path, dict[str, Any]] | None:
        bridge_cached = self._load_initialization_metadata_from_bridge(source)
        if bridge_cached is not None:
            return bridge_cached
        try:
            result = self.load_installed_metadata(source.mods_dir, source.expected_seed)
        except Exception as exc:
            self.log_info("Could not load KH3 AP metadata from installed pak: %s", exc)
            return None
        if result is None:
            return None
        metadata, pak_path = result
        slot_data = metadata.get("slot_data")
        if not isinstance(slot_data, dict):
            self.log_info("Installed KH3 AP pak metadata is missing slot_data: %s", pak_path)
            return None
        try:
            self.validate_slot_data(slot_data, f"{pak_path}:kh3_ap_seed_metadata.json")
        except self.protocol_error as exc:
            self.log_info("Installed KH3 AP pak metadata has invalid slot_data: %s", exc)
            return None
        return metadata, pak_path, slot_data

    def _load_initialization_metadata_from_bridge(
        self,
        source: OfflineBridgeInitializationInput,
    ) -> tuple[dict[str, Any], Path, dict[str, Any]] | None:
        """Reuse validated per-run bridge metadata before opening the seed pak.

        Save Manager keeps a bridge directory for every imported run. Its
        metadata was originally extracted from that run's pak, so matching the
        installed pak's filename identity lets repeated switches avoid an
        pak metadata read. The pak loader remains the fallback for new runs,
        missing bridge files, or any identity/validation mismatch.
        """

        expected_seed = self.extract_seed_id(source.expected_seed)
        try:
            candidates = sorted(
                self.list_pak_paths(source.mods_dir),
                key=self.modified_time,
                reverse=True,
            )
        except OSError:
            return None

        for pak_path in candidates:
            match = self.seed_pak_pattern.fullmatch(pak_path.name)
            if match is None:
                continue
            seed_id = match.group("seed")
            if expected_seed is not None and seed_id != expected_seed:
                continue
            slot_name = self.infer_slot_name_from_pak(pak_path)
            if not slot_name:
                continue
            requested_bridge_name = str(source.bridge_dir_name or "").strip()
            if requested_bridge_name and Path(requested_bridge_name).name != requested_bridge_name:
                return None
            bridge_dir = source.bridge_root / (requested_bridge_name or self.safe_name(f"{seed_id}_{slot_name}"))
            metadata_path = bridge_dir / "kh3_ap_seed_metadata.json"
            metadata = self._load_seed_metadata(metadata_path)
            if not isinstance(metadata, dict) or metadata.get("format") != "kh3_ap_seed_metadata":
                continue
            metadata_seed, metadata_slot_name, metadata_slot = self.metadata_identity(metadata)
            if (
                metadata_seed != seed_id
                or kh3_pak_player_name(metadata_slot_name).casefold() != slot_name.casefold()
                or metadata_slot != int(match.group("player"))
            ):
                continue
            slot_data = metadata.get("slot_data")
            if not isinstance(slot_data, dict):
                continue
            try:
                self.validate_slot_data(slot_data, str(metadata_path))
            except self.protocol_error as exc:
                self.log_info("Cached KH3 bridge metadata is invalid: %s", exc)
                continue
            self.log_info("Reused KH3 seed metadata from managed bridge: %s", metadata_path)
            return metadata, pak_path, slot_data
        return None

    def _offline_names(self, metadata: dict[str, Any]) -> tuple[str, str]:
        foreign_manifest = metadata.get("foreign_item_manifest")
        archive_name = foreign_manifest.get("archive_name") if isinstance(foreign_manifest, dict) else None
        seed_name = self.normalize_archive_seed_name(
            Path(str(archive_name)).stem if archive_name else str(metadata.get("seed") or "kh3")
        )
        slot_name = str(metadata.get("slot_name") or metadata.get("kh3_player_name") or "kh3")
        return seed_name, slot_name

    def offline_bridge_matches(self, metadata: dict[str, Any], bridge_dir: Path) -> bool:
        """Decide whether AP-owned observations belong to the installed seed."""
        current_seed, current_slot_name, current_slot = self.metadata_identity(metadata)
        previous_status = self._load_previous_status(bridge_dir)
        previous_status_identity = self._status_identity(previous_status)
        if previous_status_identity[0] and previous_status_identity[1]:
            return self._identities_match(
                previous_status_identity,
                (current_seed, current_slot_name, current_slot),
            )

        previous_metadata = self._load_seed_metadata(bridge_dir / "kh3_ap_seed_metadata.json")
        if isinstance(previous_metadata, dict):
            previous_identity = self.metadata_identity(previous_metadata)
            return self._identities_match(previous_identity, (current_seed, current_slot_name, current_slot))

        return False

    def _status_identity(self, status: dict[str, Any]) -> tuple[str | None, str, int | None]:
        return (
            self.extract_seed_id(status.get("seed_name")),
            str(status.get("player_name") or "").strip(),
            status.get("slot") if isinstance(status.get("slot"), int) else None,
        )

    def metadata_identity(self, metadata: dict[str, Any]) -> tuple[str | None, str, int | None]:
        """Return the stable seed, player, and slot identity from pak metadata."""
        foreign_manifest = metadata.get("foreign_item_manifest")
        archive_name = foreign_manifest.get("archive_name") if isinstance(foreign_manifest, dict) else None
        seed_source = Path(str(archive_name)).stem if archive_name else metadata.get("seed")
        slot_value = metadata.get("slot")
        return (
            self.extract_seed_id(seed_source),
            str(metadata.get("slot_name") or metadata.get("kh3_player_name") or "").strip(),
            slot_value if isinstance(slot_value, int) else None,
        )

    @staticmethod
    def _identities_match(
        previous: tuple[str | None, str, int | None],
        current: tuple[str | None, str, int | None],
    ) -> bool:
        previous_seed, previous_name, previous_slot = previous
        current_seed, current_name, current_slot = current
        if not previous_seed or previous_seed != current_seed:
            return False
        if previous_name and current_name and previous_name != current_name:
            return False
        return not (previous_slot is not None and current_slot is not None and previous_slot != current_slot)

    def write_offline_files(
        self,
        *,
        source: OfflineBridgeInitializationInput,
        metadata: dict[str, Any],
        pak_path: Path,
        bridge_dir: Path,
        seed_name: str | None,
        slot_data: dict[str, Any],
        location_count: int,
        preserve_previous_status: bool,
    ) -> None:
        metadata = metadata_payload(metadata)
        slot_data = slot_payload(slot_data)
        documents = {"slot_data.json": slot_data}
        encode_document("kh3_ap_seed_metadata.json", metadata)
        previous_status = self._load_previous_status(bridge_dir)
        # A mismatched/unknown owner invalidates receipts as well as checks.
        # Retaining them here would relabel the last seed's items on startup.
        previous_items = self.load_cached_items(bridge_dir) if preserve_previous_status else {"items": []}
        checked_locations = previous_status.get("checked_locations", []) if preserve_previous_status else []
        if not isinstance(checked_locations, list):
            checked_locations = []
        goal_complete = bool(previous_status.get("goal_complete", False)) if preserve_previous_status else False
        player_name = (
            metadata.get("slot_name")
            or metadata.get("kh3_player_name")
            or previous_status.get("player_name")
            or source.auth
        )
        publication_source = self._publication_source(metadata, pak_path, slot_data)
        publication_manifest_path = bridge_dir / OFFLINE_BRIDGE_PUBLICATION_MANIFEST_FILE
        publication_manifest = self.load_optional_json(publication_manifest_path)
        reusable_manifest = (
            publication_manifest
            if isinstance(publication_manifest, dict)
            and publication_manifest.get("format") == OFFLINE_BRIDGE_PUBLICATION_MANIFEST_FORMAT
            and publication_manifest.get("version") == OFFLINE_BRIDGE_PUBLICATION_MANIFEST_VERSION
            and publication_manifest.get("source") == publication_source
            else None
        )
        publication_files = {
            **documents,
            "kh3_ap_seed_metadata.json": metadata,
        }
        slot_data_path = bridge_dir / "slot_data.json"
        if not self._manifest_file_is_current(reusable_manifest, "slot_data.json", slot_data_path):
            self._publish_json_if_changed(slot_data_path, slot_data)

        self._publish_json_if_changed(
            bridge_dir / "ap_status.json",
            {
                "protocol_name": self.protocol_name,
                "protocol_version": self.protocol_version,
                "world_version": self.world_version,
                "connected": False,
                "server_address": source.server_address,
                "slot": metadata.get("slot") if metadata.get("slot") is not None else previous_status.get("slot"),
                "team": source.team,
                "player_name": player_name,
                "seed_name": seed_name,
                "checked_locations": checked_locations,
                "checked_location_count": len(checked_locations),
                "total_location_count": location_count,
                "received_item_count": len(previous_items.get("items", [])),
                "goal_complete": goal_complete,
                "installed_pak": f"metadata {pak_path.name}",
            },
        )
        if not previous_items.get("items"):
            self._publish_json_if_changed(
                bridge_dir / "ap_items.json",
                {
                    "protocol_name": self.protocol_name,
                    "protocol_version": self.protocol_version,
                    "items": [],
                },
            )
        metadata_path = bridge_dir / "kh3_ap_seed_metadata.json"
        if not self._manifest_file_is_current(
            reusable_manifest,
            "kh3_ap_seed_metadata.json",
            metadata_path,
        ):
            self._publish_json_if_changed(metadata_path, metadata)
        next_manifest = {
            "format": OFFLINE_BRIDGE_PUBLICATION_MANIFEST_FORMAT,
            "version": OFFLINE_BRIDGE_PUBLICATION_MANIFEST_VERSION,
            "source": publication_source,
            "files": {
                file_name: self._file_signature(bridge_dir / file_name)
                for file_name in publication_files
            },
        }
        self._publish_json_if_changed(publication_manifest_path, next_manifest)

    @staticmethod
    def _file_signature(path: Path) -> dict[str, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }

    @staticmethod
    def _payload_digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _publication_source(
        self,
        metadata: dict[str, Any],
        pak_path: Path,
        slot_data: dict[str, Any],
    ) -> dict[str, Any]:
        pak_signature = self._file_signature(pak_path)
        seed, player_name, slot = self.metadata_identity(metadata)
        source = {
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
            "world_version": self.world_version,
            "pak_path": str(pak_path.expanduser().resolve()),
            "pak_signature": pak_signature,
            "seed": seed,
            "player_name": player_name,
            "slot": slot,
        }
        if pak_signature is None:
            source["metadata_sha256"] = self._payload_digest(metadata)
            source["slot_data_sha256"] = self._payload_digest(slot_data)
        return source

    def _manifest_file_is_current(
        self,
        manifest: dict[str, Any] | None,
        file_name: str,
        path: Path,
    ) -> bool:
        if not isinstance(manifest, dict):
            return False
        files = manifest.get("files")
        if not isinstance(files, dict):
            return False
        current_signature = self._file_signature(path)
        return current_signature is not None and files.get(file_name) == current_signature

    def _publish_json_if_changed(self, path: Path, payload: dict[str, Any]) -> bool:
        seed_metadata = path.name in {"slot_data.json", "kh3_ap_seed_metadata.json"}
        previous = self._load_seed_metadata(path) if seed_metadata else self.load_optional_json(path)
        if previous == payload:
            return False
        if seed_metadata and previous is None and path.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = path.with_name(path.name + f".{stamp}.bak")
            shutil.copy2(path, backup)
            self.log_info("Rebuilding KH3 seed cache %s; preserved the invalid file at %s.", path, backup)
        self.atomic_write_json(path, payload)
        return True

    def _load_previous_status(self, bridge_dir: Path) -> dict[str, Any]:
        status_path = bridge_dir / "ap_status.json"
        previous_status = self.load_optional_json(status_path) or {}
        if previous_status:
            try:
                return self.validate_protocol(previous_status, str(status_path))
            except self.protocol_error:
                return {}
        return previous_status

    def delete_bridge_file(self, bridge_dir: Path | None, file_name: str) -> None:
        """Best-effort deletion for a consumed AP-owned bridge payload."""
        if bridge_dir is None:
            return
        try:
            self.delete_file(bridge_dir / file_name)
        except OSError:
            return
