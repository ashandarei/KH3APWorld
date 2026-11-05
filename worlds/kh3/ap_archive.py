from __future__ import annotations

import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArchiveValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlotSummary:
    slot: int
    name: str
    game: str


def _require_mapping(multidata: dict[str, Any], key: str) -> dict[Any, Any]:
    value = multidata.get(key)
    if not isinstance(value, dict):
        raise ArchiveValidationError(f"Archive multidata is missing required mapping '{key}'.")
    return value


def find_archipelago_entry_name(entry_names: list[str]) -> str:
    matches = [name for name in entry_names if name.endswith(".archipelago")]
    if not matches:
        raise ArchiveValidationError("Archive does not contain a .archipelago payload.")
    if len(matches) > 1:
        joined = ", ".join(sorted(matches))
        raise ArchiveValidationError(f"Archive contains multiple .archipelago payloads: {joined}")
    return matches[0]


def load_multidata(ap_zip: Path, runtime_root: Path) -> dict[str, Any]:
    if not ap_zip.is_file():
        raise ArchiveValidationError(f"AP archive not found: {ap_zip}")
    if not runtime_root.is_dir():
        raise ArchiveValidationError(f"Archipelago runtime root not found: {runtime_root}")

    sys.path.insert(0, str(runtime_root))
    from Utils import restricted_loads  # type: ignore

    try:
        with zipfile.ZipFile(ap_zip) as archive:
            entry_name = find_archipelago_entry_name(archive.namelist())
            payload = archive.read(entry_name)
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError(f"Malformed AP archive zip: {ap_zip}") from exc
    except KeyError as exc:
        raise ArchiveValidationError(f"AP archive entry could not be read from {ap_zip}") from exc

    if len(payload) < 2:
        raise ArchiveValidationError(f"AP archive payload is empty or truncated: {entry_name}")

    try:
        multidata = restricted_loads(zlib.decompress(payload[1:]))
    except zlib.error as exc:
        raise ArchiveValidationError(f"AP archive payload could not be decompressed: {entry_name}") from exc
    except Exception as exc:
        raise ArchiveValidationError(f"AP archive payload could not be decoded: {entry_name}") from exc

    if not isinstance(multidata, dict):
        raise ArchiveValidationError("Decoded AP archive payload is not a mapping.")

    _require_mapping(multidata, "slot_info")
    _require_mapping(multidata, "slot_data")
    _require_mapping(multidata, "locations")
    _require_mapping(multidata, "datapackage")
    return multidata


def list_slots(multidata: dict[str, Any]) -> list[SlotSummary]:
    slots = _require_mapping(multidata, "slot_info")
    summaries: list[SlotSummary] = []
    for slot, slot_info in slots.items():
        summaries.append(
            SlotSummary(
                slot=int(slot),
                name=getattr(slot_info, "name", str(slot)),
                game=getattr(slot_info, "game", "Unknown"),
            )
        )
    return summaries


def list_kh3_slots(multidata: dict[str, Any]) -> list[SlotSummary]:
    return [summary for summary in list_slots(multidata) if summary.game == "Kingdom Hearts III"]


def format_slot_summaries(summaries: list[SlotSummary]) -> str:
    return ", ".join(f"{summary.slot}:{summary.name} [{summary.game}]" for summary in summaries)


def resolve_kh3_slot(multidata: dict[str, Any], requested_slot: int | None) -> int:
    kh3_slots = list_kh3_slots(multidata)
    kh3_slot_ids = [summary.slot for summary in kh3_slots]
    if requested_slot is not None:
        if requested_slot not in kh3_slot_ids:
            available = format_slot_summaries(kh3_slots) or "<none>"
            raise ArchiveValidationError(
                f"Requested slot {requested_slot} is not a KH3 slot in the archive. Available KH3 slots: {available}"
            )
        return requested_slot
    if not kh3_slots:
        raise ArchiveValidationError("Archive does not contain a Kingdom Hearts III slot.")
    if len(kh3_slots) != 1:
        available = format_slot_summaries(kh3_slots)
        raise ArchiveValidationError(
            f"Archive contains multiple KH3 slots. Pass --slot explicitly. Available KH3 slots: {available}"
        )
    return kh3_slots[0].slot


def get_slot_game(multidata: dict[str, Any], slot: int) -> str | None:
    slot_info = _require_mapping(multidata, "slot_info").get(slot)
    return getattr(slot_info, "game", None)


def get_slot_name(multidata: dict[str, Any], slot: int) -> str:
    slot_info = _require_mapping(multidata, "slot_info").get(slot)
    return getattr(slot_info, "name", str(slot))


def require_bridge_locations(multidata: dict[str, Any], slot: int) -> list[dict[str, Any]]:
    from worlds.kh3.Bridge import BridgeProtocolError
    from worlds.kh3.SlotData import decode_slot_data
    slot_data = _require_mapping(multidata, "slot_data")
    slot_entry = slot_data.get(slot)
    if not isinstance(slot_entry, dict):
        raise ArchiveValidationError(f"Archive slot_data is missing KH3 slot {slot}.")

    try:
        bridge = decode_slot_data(slot_entry).get("bridge")
    except BridgeProtocolError as exc:
        raise ArchiveValidationError(str(exc)) from exc
    if not isinstance(bridge, dict):
        raise ArchiveValidationError(f"Archive slot_data for KH3 slot {slot} is missing bridge metadata.")

    locations = bridge.get("locations")
    if not isinstance(locations, (list, tuple)):
        raise ArchiveValidationError(f"Archive bridge metadata for KH3 slot {slot} is missing bridge locations.")

    return list(locations)
