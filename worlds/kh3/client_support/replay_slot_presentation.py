"""Save-slot status and AP replay-index presentation for the KH3 client."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

Payload = Mapping[str, Any]
RECENT_MANUAL_REPLAY_SLOT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class ReplaySlotSelectionInput:
    """All sidecar state used to choose and describe relevant replay slots."""

    native_save_receipts: Payload | None
    runtime_current_save_slot: Payload | None = None
    runtime_display_save_slot: Payload | None = None
    last_runtime_save_load_event: Payload | None = None


@dataclass(frozen=True, slots=True)
class ReplaySlotSummary:
    label: str
    replay_index: int | None
    pending_items: tuple[tuple[int, str], ...] = ()
    saved_at_utc: str | None = None
    is_last_loaded: bool = False
    is_current: bool = False
    preview_ready: bool = False

    @property
    def text(self) -> str:
        index_text = str(self.replay_index) if self.replay_index is not None else "-"
        label = "AUTO" if self.label.casefold() == "auto" else self.label
        return f"{label}: AP{index_text}"

    @property
    def tooltip(self) -> str:
        lines: list[str]
        if self.replay_index is None:
            lines = [f"{self.label} has no saved AP replay index yet."]
        else:
            lines = [f"{self.label} snapshot index: {self.replay_index}"]
        if self.saved_at_utc:
            lines.append(f"Last saved: {self.saved_at_utc}")
        if self.replay_index is None:
            return "\n".join(lines)
        if not self.preview_ready:
            lines.append("Native replay preview unavailable.")
            return "\n".join(lines)
        if not self.pending_items:
            lines.append("No unapplied AP items for this snapshot.")
            return "\n".join(lines)

        limit = 30
        lines.append("Unapplied AP items:")
        lines.extend(f"#{item_index} {item_name}" for item_index, item_name in self.pending_items[:limit])
        remaining = len(self.pending_items) - limit
        if remaining > 0:
            lines.append(f"... {remaining} more")
        return "\n".join(lines)

    def as_legacy_dict(self) -> dict[str, str]:
        return {"label": self.label, "text": self.text, "tooltip": self.tooltip}


def _slot_number(payload: Payload) -> int:
    save_slot = payload.get("save_slot")
    if isinstance(save_slot, str) and save_slot.upper() == "AUTO":
        return 0
    try:
        return int(save_slot)
    except (TypeError, ValueError):
        match = re.search(r"KHIII_slot(\d+)\.bin$", str(payload.get("slot_file") or ""))
        return int(match.group(1)) if match else -1


def describe_save_slot(
    ledger: Any,
    runtime_display: Any = None,
    runtime_current: Any = None,
) -> str:
    using_runtime = False
    using_runtime_current = False
    if isinstance(runtime_current, dict) and runtime_current.get("status") == "known":
        resolution = runtime_current
        using_runtime = True
        using_runtime_current = True
    elif isinstance(runtime_display, dict) and runtime_display.get("status") == "known":
        resolution = runtime_display
        using_runtime = True
    elif isinstance(ledger, dict):
        resolution = ledger.get("last_resolution")
    else:
        return "-"
    if not isinstance(resolution, dict) and isinstance(ledger, dict):
        resolution = ledger.get("last_matched_resolution")
    if not isinstance(resolution, dict):
        return "-"
    if not using_runtime and resolution.get("status") != "matched":
        return f"Unknown ({resolution.get('reason') or 'unknown'})"
    number = _slot_number(resolution)
    if number == 0:
        description = "Auto (slot 0)"
        if using_runtime_current and isinstance(runtime_display, dict) and runtime_display.get("status") == "known":
            display_number = _slot_number(runtime_display)
            if display_number > 0:
                description += f" | Last manual: Slot {display_number}"
        return description
    if number > 0:
        return f"Slot {number}"
    return str(resolution.get("slot_file") or "Unknown")


def describe_last_save_loaded(payload: Any) -> str:
    if not isinstance(payload, dict) or str(payload.get("access_kind") or "") != "load":
        return "-"
    number = _slot_number(payload)
    if number == 0:
        return "Auto (slot 0)"
    if number > 0:
        return f"Slot {number}"
    return str(payload.get("slot_file") or "-")


def slot_replay_index(slots: Payload, slot_number: int) -> int | None:
    entry = slots.get(f"kh3sv2/data/KHIII_slot{slot_number}.bin")
    if not isinstance(entry, dict):
        return None
    value = entry.get("last_applied_index_at_save")
    return value if type(value) is int else None


def _slot_saved_at_utc(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for field in ("stamped_at_utc", "observed_at_utc"):
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    for field in ("manual_save_intent_event", "save_file_open_event"):
        nested = payload.get(field)
        if isinstance(nested, Mapping):
            value = str(nested.get("observed_at_utc") or "").strip()
            if value:
                return value
    return None


def _slot_recency(payload: Any) -> float:
    value = _slot_saved_at_utc(payload)
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except (OverflowError, ValueError):
        return float("-inf")


def _slot_number_from_entry(slot_file: str, payload: Any) -> int:
    if isinstance(payload, Mapping):
        number = _slot_number(payload)
        if number >= 0:
            return number
    match = re.search(r"KHIII_slot(\d+)\.bin$", slot_file)
    return int(match.group(1)) if match else -1


def build_replay_slot_summaries(source: ReplaySlotSelectionInput) -> tuple[ReplaySlotSummary, ...]:
    """Show AUTO plus the five most relevant distinct manual save slots."""

    ledger = source.native_save_receipts
    ledger_slots = ledger.get("slots") if isinstance(ledger, Mapping) else None
    slots = dict(ledger_slots) if isinstance(ledger_slots, Mapping) else {}

    def overlay_runtime_slot(payload: Payload | None, *, include_preview: bool) -> int:
        if not isinstance(payload, Mapping) or payload.get("status") != "known":
            return -1
        slot_number = _slot_number(payload)
        if slot_number < 0:
            return -1
        slot_file = f"kh3sv2/data/KHIII_slot{slot_number}.bin"
        existing = slots.get(slot_file)
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        # Keep membership, index and preview from the same ledger checkpoint;
        # a loaded/display-slot observation may have been published earlier.
        checkpoint_fields = {"saved_receipts", "last_applied_index_at_save", "ap_index_at_save",
                             "replay_cutoff_index", "pending_replay_items", "replay_preview_ready"}
        merged.update({key: value for key, value in payload.items()
                       if key not in checkpoint_fields or not isinstance(existing, Mapping)})
        if not include_preview and not isinstance(existing, Mapping):
            merged.pop("pending_replay_items", None)
            merged.pop("replay_preview_ready", None)
        merged["slot_file"] = slot_file
        slots[slot_file] = merged
        return slot_number

    current_slot_number = overlay_runtime_slot(source.runtime_current_save_slot, include_preview=True)
    display_slot_number = overlay_runtime_slot(source.runtime_display_save_slot, include_preview=False)
    if current_slot_number < 0:
        current_slot_number = display_slot_number
    if current_slot_number < 0 and isinstance(ledger, Mapping):
        resolution = ledger.get("last_resolution")
        if not isinstance(resolution, Mapping):
            resolution = ledger.get("last_matched_resolution")
        if isinstance(resolution, Mapping) and resolution.get("status") == "matched":
            current_slot_number = _slot_number(resolution)
            if current_slot_number > 0:
                slot_file = f"kh3sv2/data/KHIII_slot{current_slot_number}.bin"
                slots.setdefault(slot_file, {"save_slot": current_slot_number})

    last_loaded_slot_number = -1
    last_event = source.last_runtime_save_load_event
    if isinstance(last_event, Mapping) and str(last_event.get("access_kind") or "") == "load":
        loaded_slot_number = _slot_number(last_event)
        if loaded_slot_number >= 0:
            last_loaded_slot_number = loaded_slot_number
            slot_file = f"kh3sv2/data/KHIII_slot{loaded_slot_number}.bin"
            existing = slots.get(slot_file)
            merged = dict(existing) if isinstance(existing, Mapping) else {}
            merged.setdefault("slot_file", slot_file)
            merged.setdefault("save_slot", loaded_slot_number)
            merged.setdefault("observed_at_utc", last_event.get("observed_at_utc"))
            slots[slot_file] = merged

    manual_slots: dict[int, Payload] = {}
    for slot_file, payload in slots.items():
        if not isinstance(payload, Mapping):
            continue
        slot_number = _slot_number_from_entry(str(slot_file), payload)
        if slot_number <= 0:
            continue
        existing = manual_slots.get(slot_number)
        if existing is None or _slot_recency(payload) >= _slot_recency(existing):
            manual_slots[slot_number] = payload

    ordered_manual_slots = sorted(
        manual_slots,
        key=lambda slot_number: (_slot_recency(manual_slots[slot_number]), slot_number),
        reverse=True,
    )
    pinned_slots = list(dict.fromkeys(
        number for number in (current_slot_number, last_loaded_slot_number, display_slot_number)
        if number > 0 and number in manual_slots
    ))
    selected_manual_slots = (
        pinned_slots + [number for number in ordered_manual_slots if number not in pinned_slots]
    )[:RECENT_MANUAL_REPLAY_SLOT_LIMIT]

    summaries: list[ReplaySlotSummary] = []
    displayed_slots = [(0, "Auto"), *((number, f"S{number}") for number in selected_manual_slots)]
    for slot_number, label in displayed_slots:
        replay_index = slot_replay_index(slots, slot_number)
        slot_payload = slots.get(f"kh3sv2/data/KHIII_slot{slot_number}.bin")
        native_items = slot_payload.get("pending_replay_items") if isinstance(slot_payload, Mapping) else None
        preview_ready = (isinstance(slot_payload, Mapping) and slot_payload.get("replay_preview_ready") is True
                         and isinstance(native_items, list)
                         and all(isinstance(item, Mapping) and type(item.get("index")) is int
                                 and isinstance(item.get("item_name"), str) for item in native_items))
        pending = tuple((item["index"], item["item_name"]) for item in native_items) if preview_ready else ()
        summaries.append(
            ReplaySlotSummary(
                label,
                replay_index,
                pending,
                _slot_saved_at_utc(slot_payload),
                is_last_loaded=slot_number == last_loaded_slot_number,
                is_current=slot_number == current_slot_number,
                preview_ready=preview_ready,
            )
        )
    return tuple(summaries)
