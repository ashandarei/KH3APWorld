"""KH3 seed-pak naming and metadata extraction using the Python pak reader."""

from __future__ import annotations

import importlib.resources
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import Utils

from ..Bridge import BridgeProtocolError, KH3_WORLD_VERSION
from ..bridge_support.files import atomic_write_json, load_optional_json_file

logger = logging.getLogger("Client")


def _installed_kh3_apworld_version() -> str:
    try:
        package_name = __package__.rsplit(".", 1)[0]
        manifest = importlib.resources.files(package_name).joinpath("archipelago.json")
        with manifest.open("r", encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)
        world_version = payload.get("world_version")
        if isinstance(world_version, str) and world_version.strip():
            return world_version.strip()
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return KH3_WORLD_VERSION
    return KH3_WORLD_VERSION


KH3_AP_SEED_PAK_PATTERN = re.compile(
    r"^pakchunk9-(?P<slot>.+?)-randomizer-AP_(?P<seed>\d+)-(?P<player>\d+)_99_P\.pak$",
    re.IGNORECASE,
)
KH3_AP_SEED_PAK_SLOT_PREFIX = "pakchunk9-"
KH3_AP_SEED_PAK_SLOT_SUFFIX = "-randomizer-"
KH3_AP_SEED_METADATA_PAK_PATH = Path("KINGDOM HEARTS III/Content/Archipelago/kh3_ap_seed_metadata.json")
KH3_SEED_PAK_METADATA_CACHE_FORMAT = "kh3_seed_pak_metadata_cache"
KH3_SEED_PAK_METADATA_CACHE_VERSION = 1


def deactivate_incompatible_seed_paks(mods_dir: Path) -> bool:
    """Check actual format/catalog compatibility, independent of release labels.

    Preserve unusable paks as disabled files; leave bridge state and saves alone.
    A failed read or rename blocks offline initialization without closing the UI.
    """
    from ..Pak import PakReader
    from ..SlotData import decode_slot_data

    safe = True
    for pak_path in sorted(mods_dir.glob("*.pak")):
        if KH3_AP_SEED_PAK_PATTERN.fullmatch(pak_path.name) is None:
            continue
        try:
            raw = PakReader(pak_path).read(KH3_AP_SEED_METADATA_PAK_PATH.as_posix())
            metadata = json.loads(raw.decode("utf-8-sig"))
            if not kh3_ap_seed_metadata_matches_pak(metadata, pak_path):
                raise BridgeProtocolError("Seed metadata format or seed/player identity is invalid.")
            if metadata.get("format_version") != 1:
                raise BridgeProtocolError("Unsupported KH3 seed metadata format version.")
            decode_slot_data(metadata.get("slot_data"))
        except OSError as exc:
            safe = False
            logger.warning("Could not read KH3 seed %s: %s. Offline startup is blocked.", pak_path, exc)
            continue
        except (BridgeProtocolError, ValueError, KeyError, TypeError) as exc:
            reason = str(exc)
        else:
            continue
        try:
            disabled_path = pak_path.with_name(pak_path.name + ".disabled")
            if disabled_path.exists():
                disabled_path = pak_path.with_name(pak_path.name + f".{uuid.uuid4().hex}.disabled")
            pak_path.rename(disabled_path)
            logger.warning(
                "Deactivated incompatible KH3 seed %s: %s. "
                "Use the matching client/world or regenerate the seed with the current world. "
                "The pak is preserved at %s; saves and stored runs were left in place.",
                pak_path.name, reason, disabled_path,
            )
        except (OSError, ValueError, KeyError) as exc:
            safe = False
            logger.warning("Could not deactivate KH3 seed %s: %s. Offline startup is blocked.", pak_path, exc)
    return safe


def seed_pak_metadata_cache_path() -> Path:
    """Return the user-scoped installed seed-pak metadata cache path."""

    return Path(Utils.user_path("kh3", "cache", "seed-pak-metadata.json")).resolve()


def _seed_pak_cache_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    return os.path.normcase(str(resolved))


def _seed_pak_signature(path: Path) -> tuple[int, int] | None:
    """Return a copy-stable pak signature supported on Windows and Linux.

    ``st_ctime`` is creation time on Windows and metadata-change time on Linux,
    so replacing an installed pak with an identical managed copy changes it on
    both platforms. ``copy2`` preserves modification time and size, which makes
    those fields suitable for recognizing the same immutable generated pak
    after Save Manager reinstalls it.
    """

    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _load_seed_pak_metadata_cache(cache_path: Path | None) -> dict[str, Any]:
    empty = {
        "format": KH3_SEED_PAK_METADATA_CACHE_FORMAT,
        "version": KH3_SEED_PAK_METADATA_CACHE_VERSION,
        "entries": {},
    }
    if cache_path is None:
        return empty
    payload = load_optional_json_file(cache_path)
    if (
        not isinstance(payload, dict)
        or payload.get("format") != KH3_SEED_PAK_METADATA_CACHE_FORMAT
        or payload.get("version") != KH3_SEED_PAK_METADATA_CACHE_VERSION
        or not isinstance(payload.get("entries"), dict)
    ):
        return empty
    return payload


def _cached_seed_pak_metadata(
    cache: dict[str, Any],
    pak_path: Path,
) -> dict[str, Any] | None:
    signature = _seed_pak_signature(pak_path)
    if signature is None:
        return None
    entry = cache.get("entries", {}).get(_seed_pak_cache_key(pak_path))
    if not isinstance(entry, dict):
        return None
    metadata = entry.get("metadata")
    if (
        entry.get("mtime_ns") != signature[0]
        or entry.get("size") != signature[1]
        or not isinstance(metadata, dict)
        or metadata.get("format") != "kh3_ap_seed_metadata"
        or not kh3_ap_seed_metadata_matches_pak(metadata, pak_path)
    ):
        return None
    return metadata


def _store_seed_pak_metadata(
    cache_path: Path | None,
    cache: dict[str, Any],
    pak_path: Path,
    metadata: dict[str, Any],
) -> None:
    if cache_path is None:
        return
    signature = _seed_pak_signature(pak_path)
    if signature is None:
        return
    entries = cache.setdefault("entries", {})
    entries[_seed_pak_cache_key(pak_path)] = {
        "path": str(pak_path),
        "mtime_ns": signature[0],
        "size": signature[1],
        "metadata": metadata,
    }
    if len(entries) > 8:
        ordered = sorted(
            entries.items(),
            key=lambda entry: int(entry[1].get("mtime_ns", 0)) if isinstance(entry[1], dict) else 0,
            reverse=True,
        )
        cache["entries"] = dict(ordered[:8])
    try:
        atomic_write_json(cache_path, cache)
    except OSError as exc:
        logger.debug("Could not update KH3 seed-pak metadata cache %s: %s", cache_path, exc)


def infer_kh3_ap_slot_name_from_pak_name(pak_path: Path | str) -> str | None:
    filename = Path(pak_path).name
    if KH3_AP_SEED_PAK_PATTERN.fullmatch(filename) is None:
        return None
    folded_filename = filename.casefold()
    slot_start = len(KH3_AP_SEED_PAK_SLOT_PREFIX)
    if not folded_filename.startswith(KH3_AP_SEED_PAK_SLOT_PREFIX):
        return None
    slot_end = folded_filename.find(KH3_AP_SEED_PAK_SLOT_SUFFIX, slot_start)
    if slot_end < 0:
        return None
    slot_name = filename[slot_start:slot_end].strip()
    return slot_name or None


def kh3_pak_player_name(slot_name: str) -> str:
    """Match the Python generator's filename sanitization without changing AP names."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", slot_name).strip("._") or "Player"


def kh3_ap_seed_metadata_matches_pak(metadata: dict[str, Any], pak_path: Path | str) -> bool:
    """Validate the embedded metadata identity against its seed pak filename."""

    if not isinstance(metadata, dict) or metadata.get("format") != "kh3_ap_seed_metadata":
        return False
    match = KH3_AP_SEED_PAK_PATTERN.fullmatch(Path(pak_path).name)
    if match is None:
        return False

    seed_id = match.group("seed")
    if re.search(rf"(?<!\d){re.escape(seed_id)}(?!\d)", str(metadata.get("seed") or "")) is None:
        return False
    slot_name = str(metadata.get("slot_name") or metadata.get("kh3_player_name") or "").strip()
    if not slot_name or kh3_pak_player_name(slot_name).casefold() != match.group("slot").casefold():
        return False
    slot = metadata.get("slot")
    if isinstance(slot, bool):
        return False
    try:
        return int(slot) == int(match.group("player"))
    except (TypeError, ValueError):
        return False


def _decode_kh3_ap_seed_metadata(payload: bytes | str, *, source: str) -> dict[str, Any] | None:
    from ..RuntimeSeed import encode_document, expand_document, metadata_payload, runtime_documents
    try:
        text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
        metadata = expand_document("kh3_ap_seed_metadata.json", json.loads(text))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.info("Could not parse KH3 AP metadata from %s: %s", source, exc)
        return None
    if not isinstance(metadata, dict) or metadata.get("format") != "kh3_ap_seed_metadata":
        logger.info("KH3 AP metadata from %s has an unsupported format.", source)
        return None
    metadata = metadata_payload(metadata)
    runtime_documents(metadata)
    encode_document("kh3_ap_seed_metadata.json", metadata)
    return metadata


def extract_kh3_ap_seed_metadata_from_pak(pak_path: Path) -> dict[str, Any] | None:
    from ..Pak import PakError, PakReader

    pak_path = pak_path.expanduser().resolve()
    if not pak_path.is_file():
        return None
    try:
        # Large seeds can exceed 4 MiB; use the pak reader's validated limit.
        data = PakReader(pak_path).read(KH3_AP_SEED_METADATA_PAK_PATH.as_posix())
        metadata = _decode_kh3_ap_seed_metadata(data, source=f"{pak_path}:python")
    except (PakError, OSError, KeyError) as exc:
        logger.info("Could not read KH3 AP metadata from %s: %s", pak_path, exc)
        return None
    if metadata is None or not kh3_ap_seed_metadata_matches_pak(metadata, pak_path):
        return None
    return metadata


def load_installed_kh3_ap_seed_metadata(
    kh3_mods_dir: Path,
    *,
    expected_seed: str | None = None,
    metadata_cache_path: Path | None = None,
) -> tuple[dict[str, Any], Path] | None:
    kh3_mods_dir = kh3_mods_dir.expanduser().resolve()
    if not kh3_mods_dir.is_dir():
        return None

    candidates: list[Path] = []
    for pak_path in sorted(kh3_mods_dir.glob("*.pak"), key=lambda path: path.stat().st_mtime, reverse=True):
        match = KH3_AP_SEED_PAK_PATTERN.search(pak_path.name)
        if match is None or (expected_seed and match.group("seed") != expected_seed):
            continue
        candidates.append(pak_path)

    metadata_cache = _load_seed_pak_metadata_cache(metadata_cache_path)
    for pak_path in candidates:
        cached_metadata = _cached_seed_pak_metadata(metadata_cache, pak_path)
        if cached_metadata is not None:
            return cached_metadata, pak_path
        metadata = extract_kh3_ap_seed_metadata_from_pak(pak_path)
        if metadata is not None and kh3_ap_seed_metadata_matches_pak(metadata, pak_path):
            _store_seed_pak_metadata(metadata_cache_path, metadata_cache, pak_path, metadata)
            return metadata, pak_path
    return None


__all__ = [
    "KH3_AP_SEED_METADATA_PAK_PATH",
    "KH3_AP_SEED_PAK_PATTERN",
    "KH3_SEED_PAK_METADATA_CACHE_FORMAT",
    "KH3_SEED_PAK_METADATA_CACHE_VERSION",
    "extract_kh3_ap_seed_metadata_from_pak",
    "infer_kh3_ap_slot_name_from_pak_name",
    "kh3_ap_seed_metadata_matches_pak",
    "load_installed_kh3_ap_seed_metadata",
    "seed_pak_metadata_cache_path",
]
