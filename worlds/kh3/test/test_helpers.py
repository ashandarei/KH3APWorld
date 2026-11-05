from __future__ import annotations

import json
import unittest
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from BaseClasses import CollectionState

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..Client import KH3Context


@contextmanager
def temporary_bridge_directory() -> Iterator[Path]:
    """Yield one isolated bridge directory and remove it after the test."""
    with TemporaryDirectory(prefix="kh3-bridge-test-") as temp_dir:
        yield Path(temp_dir)


@dataclass(frozen=True)
class DeterministicClock:
    """Provide explicit, immutable timestamps relative to one test instant."""

    start: datetime = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.start

    def at(self, **offset: float) -> datetime:
        return self.start + timedelta(**offset)

    def iso(self, **offset: float) -> str:
        return self.at(**offset).isoformat()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_location_metadata(
    *,
    bridge_key: str,
    ap_location_id: int,
    ap_location_name: str,
    source_table: str | None = None,
    source_id: str | None = None,
    world: str | None = None,
) -> dict[str, Any]:
    location = {
        "bridge_key": bridge_key,
        "ap_location_name": ap_location_name,
        "ap_location_id": ap_location_id,
    }
    if source_table is not None:
        location["source_table"] = source_table
    if source_id is not None:
        location["source_id"] = source_id
    if world is not None:
        location["world"] = world
    return location


def build_slot_data(
    locations: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "bridge": {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "locations": [dict(location) for location in locations],
        },
    }


def build_ap_item(
    *,
    index: int,
    item_id: int,
    item_name: str,
    location_id: int,
    location_name: str,
    from_player: int,
    from_player_name: str,
    flags: int = 0,
    bridge_key: str | None = None,
    kh3_runtime_token: str | None = None,
    kh3_grant_method: str | None = None,
) -> dict[str, Any]:
    item = {
        "index": index,
        "item_id": item_id,
        "item_name": item_name,
        "location_id": location_id,
        "location_name": location_name,
        "from_player": from_player,
        "from_player_name": from_player_name,
        "flags": flags,
    }
    if bridge_key is not None:
        item["bridge_key"] = bridge_key
    if kh3_runtime_token is not None:
        item["kh3_runtime_token"] = kh3_runtime_token
    if kh3_grant_method is not None:
        item["kh3_grant_method"] = kh3_grant_method
    return item


def build_ap_item_ledger(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "items": [dict(item) for item in items],
    }


def build_runtime_context(
    *,
    world: str,
    outer: str,
    opened_at: str,
    object_path: str | None = None,
    pawn_source: str | None = None,
    hook: str = "RuntimePlayablePawnContext",
    source: str | None = None,
) -> dict[str, Any]:
    context = {
        "hook": hook,
        "world": world,
        "outer": outer,
        "opened_at": opened_at,
    }
    if object_path is not None:
        context["object"] = object_path
    if pawn_source is not None:
        context["pawn_source"] = pawn_source
    if source is not None:
        context["source"] = source
    return context


def build_save_slot_event(
    *,
    source: str,
    save_slot: str | int,
    slot_file: str,
    observed_at_utc: str,
    save_slot_label: str | None = None,
    ap_index_at_save: int | None = None,
    last_applied_index_at_save: int | None = None,
    replay_cutoff_index: int | None = None,
    source_slot: str | None = None,
) -> dict[str, Any]:
    event = {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "event": "runtime_current_save_slot",
        "status": "known",
        "source": source,
        "save_slot": save_slot,
        "slot_file": slot_file,
        "observed_at_utc": observed_at_utc,
    }
    optional_fields = {
        "save_slot_label": save_slot_label,
        "ap_index_at_save": ap_index_at_save,
        "last_applied_index_at_save": last_applied_index_at_save,
        "replay_cutoff_index": replay_cutoff_index,
        "source_slot": source_slot,
    }
    event.update({key: value for key, value in optional_fields.items() if value is not None})
    return event


def build_runtime_grant_acknowledgement(
    *,
    request_id: str,
    applied_indices: Iterable[int],
    applied_at: str | None = None,
    applied_grants: Iterable[Mapping[str, Any]] = (),
    action_kind: str = "runtime_item_grant_batch",
    status: str = "applied",
) -> dict[str, Any]:
    acknowledgement = {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "request_id": request_id,
        "action_kind": action_kind,
        "status": status,
        "applied_indices": list(applied_indices),
    }
    grants = [dict(grant) for grant in applied_grants]
    if applied_at is not None:
        acknowledgement["applied_at"] = applied_at
    if grants:
        acknowledgement["applied_grants"] = grants
    return acknowledgement


class KH3WorldIntegrityMixin(object):
    def _state_without_precollected_items(self) -> CollectionState:
        state = CollectionState(self.multiworld)
        for item in self.multiworld.precollected_items[self.player]:
            if state.prog_items[self.player].get(item.name, 0) > 0:
                state.remove_item(item.name, self.player)
        state.stale[self.player] = True
        return state


class KH3BridgeTestCase(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        write_json(path, payload)

    def _write_ready_runtime_context(self, bridge_dir: Path, opened_at: str | None = None) -> None:
        opened_at = opened_at or datetime.now(timezone.utc).isoformat()
        self._write_json(
            bridge_dir / "runtime_context.json",
            build_runtime_context(
                world="World /Game/Maps/GardenOfAssemblage/umap/GoA_01.GoA_01",
                outer="Package /Game/Maps/GardenOfAssemblage/umap/GoA_01",
                object_path=(
                    "p_ex001_Pawn_C /Game/Maps/GardenOfAssemblage/umap/GoA_01.GoA_01:"
                    "PersistentLevel.p_ex001_Pawn_C_0 @111 real=true"
                ),
                pawn_source="ZoneTiming:TresShowMapName:frame:GetPlayerController:Pawn",
                opened_at=opened_at,
            ),
        )
        self._write_runtime_sora_run_state(bridge_dir)

    def _write_goa_mapname_runtime_context(self, bridge_dir: Path, opened_at: str = "2026-05-05T12:03:00+00:00") -> dict:
        runtime_context = build_runtime_context(
            world="World /Game/Maps/GardenOfAssemblage/umap/GoA_01.GoA_01",
            outer="Package /Game/Maps/GardenOfAssemblage/umap/GoA_01",
            object_path=(
                "p_ex001_Pawn_C /Game/Maps/GardenOfAssemblage/umap/GoA_01.GoA_01:"
                "PersistentLevel.p_ex001_Pawn_C_0 @111 real=true"
            ),
            pawn_source="GoAMapName:UIPMapNameCallback:cached_pawn",
            opened_at=opened_at,
        )
        self._write_json(bridge_dir / "runtime_context.json", runtime_context)
        return runtime_context

    def _write_new_game_current_slot(self, bridge_dir: Path) -> None:
        self._write_json(
            bridge_dir / "runtime_current_save_slot.json",
            build_save_slot_event(
                source="new_game_staging",
                save_slot="AUTO",
                save_slot_label="AUTO",
                slot_file="kh3sv2/data/KHIII_slot0.bin",
                observed_at_utc="2026-05-05T12:02:00+00:00",
                ap_index_at_save=0,
                last_applied_index_at_save=0,
                replay_cutoff_index=-1,
                source_slot="NEW_GAME",
            ),
        )

    def _write_runtime_sora_run_state(self, bridge_dir: Path) -> None:
        self._write_json(bridge_dir / "runtime_sora_state.json", {
            "schema": "kh3ap.runtime_sora_state.v1",
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "event": "runtime_sora_state",
            "status": "known",
            "source": "current_state_write_polyhook",
            "state_class": "TresPlayerState_Run",
            "current_state": "TresPlayerState_Run",
            "safety": "ok",
            "observed_at_utc": "2026-05-05T12:03:02+00:00",
        })

    def _write_runtime_sora_game_over_state(self, bridge_dir: Path, *, event_index: int = 776) -> None:
        self._write_json(bridge_dir / "runtime_sora_state.json", {
            "schema": "kh3ap.runtime_sora_state.v1",
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "event": "runtime_sora_state",
            "status": "known",
            "source": "current_state_write_polyhook",
            "polyhook_event_index": event_index,
            "state_key": f"known|TresPlayerState_GameOver|118|event={event_index}",
            "state_class": "TresPlayerState_GameOver",
            "current_state": "TresPlayerState_GameOver",
            "current_state_id": 118,
            "safety": "not_ok",
            "observed_at_utc": f"2026-05-05T12:04:{event_index % 60:02d}+00:00",
        })

    def _make_deathlink_client_context(
        self,
        bridge_dir: Path,
        *,
        deathlink_enabled: bool = True,
    ) -> tuple[KH3Context, list[str]]:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = bridge_dir
        ctx.slot_data = {"death_link": int(deathlink_enabled)}
        ctx.tags = {"DeathLink"} if deathlink_enabled else set()
        ctx.slot = 1
        ctx.player_names = {1: "KH3"}
        ctx.server = type("Server", (), {"socket": type("Socket", (), {"closed": False})()})()
        ctx.last_death_link = 0
        ctx.last_deathlink_game_over_key = ""
        ctx.received_deathlink_game_over_request_id = None
        sent_causes: list[str] = []

        async def fake_send_death(cause: str = "") -> None:
            sent_causes.append(cause)

        ctx.send_death = fake_send_death
        return ctx, sent_causes

    def _runtime_grant_payload_grants(self, bridge_dir: Path) -> list[dict[str, Any]]:
        payload = json.loads((bridge_dir / "runtime_item_grant.json").read_text(encoding="utf-8"))
        if isinstance(payload.get("grants"), list):
            return list(payload["grants"])
        return [payload]

    def _write_minimal_slot_data(self, bridge_dir: Path) -> None:
        self._write_json(bridge_dir / "slot_data.json", build_slot_data())

    def _write_game_over_state_event(
        self,
        bridge_dir: Path,
        *,
        recorded_at_utc: str,
        event_index: int = 776,
    ) -> None:
        (bridge_dir / "current_state_write_polyhook_events.jsonl").write_text(
            json.dumps({
                "schema": "kh3ap.current_state_write_polyhook_event.v1",
                "recorded_at_utc": recorded_at_utc,
                "event": "state_write",
                "event_index": event_index,
                "state_comp": "0x1bf338f1640",
                "new_state": "0x1beef80aba0",
                "effective_state": "0x1beef80aba0",
                "state_comp_real": True,
                "current_state_real": True,
                "state_comp_matches_cached_pawn": True,
                "new_state_class": "TresPlayerState_GameOver",
                "state_class": "TresPlayerState_GameOver",
                "catalog_match": True,
                "current_state_id": 118,
            }),
            encoding="utf-8",
        )

    def _fstring(self, value: str) -> bytes:
        encoded = value.encode("ascii") + b"\x00"
        return len(encoded).to_bytes(4, "little", signed=True) + encoded
    def _randomizer_vbonus_save(self, source_ids: list[str]) -> bytes:
        entries = b"".join(self._fstring(source_id) for source_id in source_ids)
        inner = (
            self._fstring("NameProperty")
            + b"\x00"
            + len(source_ids).to_bytes(4, "little", signed=True)
            + entries
        )
        return (
            self._fstring("notObtainedVBonusData")
            + self._fstring("ArrayProperty")
            + len(inner).to_bytes(8, "little", signed=False)
            + inner
            + self._fstring("None")
        )


class KH3RuntimeGrantTestCase(KH3BridgeTestCase):
    def _write_ready_runtime_context(self, bridge_dir: Path, opened_at: str | None = None) -> None:
        opened_at = opened_at or datetime.now(timezone.utc).isoformat()
        self._write_json(
            bridge_dir / "runtime_context.json",
            build_runtime_context(
                world="World /Game/Levels/tt/tt_30/tt_30.tt_30",
                outer="Package /Game/Levels/tt/tt_30",
                object_path=(
                    "p_ex001_Pawn_C /Game/Levels/tt/tt_30/tt_30.tt_30:"
                    "PersistentLevel.p_ex001_Pawn_C_0 @111 real=true"
                ),
                pawn_source="ZoneTiming:TresShowMapName:cached_pawn",
                opened_at=opened_at,
            ),
        )

    def _write_runtime_sora_idle_state(self, bridge_dir: Path) -> None:
        self._write_json(bridge_dir / "runtime_sora_state.json", {
            "schema": "kh3ap.runtime_sora_state.v1",
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "event": "runtime_sora_state",
            "status": "known",
            "source": "current_state_write_polyhook",
            "state_class": "TresPlayerState_Idle",
            "current_state": "TresPlayerState_Idle",
            "safety": "ok",
            "observed_at_utc": "2026-05-05T12:03:05+00:00",
        })


def mock_native_description(item):
    """Explicit native envelope fixture; this does not decode item names or IDs."""
    method = item.get("kh3_grant_method", "bp_tres_get_item")
    kind = {"controller_unlock": "grant_controller_unlock", "direct_player_bad_status": "trigger_trap", "king_mickey_trap": "trigger_trap"}.get(method, "add_inventory_item")
    action = {"kind": kind, "item_name": item.get("item_name")}
    if kind == "grant_controller_unlock":
        action["unlock_display_name"] = item["item_name"].removeprefix("Unlock: ")
    return {**item, "description_owner": "dll", "native_supported": True,
            "kh3_grant_method": method, "action": action, "kh3_secondary_rewards": item.get("kh3_secondary_rewards", [])}


def publish_native_descriptions(session, items=None):
    """Mock the DLL snapshot for bookkeeping tests using explicit grant facts.

    Native catalog tests exercise the real ID decoder and ordinal expansion.
    This fixture only supplies the bound snapshot consumed by Python.
    """
    from ..bridge_support.files import atomic_write_json
    identity = session.slot_data.setdefault("kh3_seed", {"seed_name": "unit-test", "slot": 1})
    identity.setdefault("seed_name", "unit-test")
    identity.setdefault("slot", 1)
    atomic_write_json(session.bridge_dir / "bridge_runtime_status.json", {"kh3_process_pid": 777, "kh3_process_running": True})
    native = session.native_reconciliation_state() or dict(
        schema="kh3ap.native_reconciliation_state.v1", owner="dll", process_id=777,
        **identity, delivery_epoch=4, initialized=True, ready=True,
        active_receipts=dict(version=1, through=-1, extra=[]),
        pending_delivery_indices=[], pending_replay_indices=[])
    native = dict(native, description_revision=native.get("description_revision", 0) + 1)
    items = list(items if items is not None else session._load_ap_items_view().get("items", []))
    rows = {str(item["index"]): {**item, **mock_native_description(item), "native_supported": True}
            for item in items}
    atomic_write_json(session.bridge_dir / "runtime_item_descriptions.json", dict(
        schema="kh3ap.item_descriptions.v1", owner="dll", process_id=777,
        **identity, revision=native["description_revision"], items=rows))
    atomic_write_json(session.bridge_dir / "runtime_reconciliation_state.json", native)
    return native


def apply_native_item_event(session, payload):
    from ..bridge_support.files import atomic_write_json
    from ..bridge_support.saved_receipts import SavedReceipts
    identity = session.slot_data.setdefault("kh3_seed", {"seed_name": "unit-test", "slot": 1})
    identity.setdefault("seed_name", "unit-test")
    identity.setdefault("slot", 1)
    atomic_write_json(session.bridge_dir / "bridge_runtime_status.json", {"kh3_process_pid": 777, "kh3_process_running": True})
    previous = publish_native_descriptions(session)
    active = SavedReceipts.read({"saved_receipts": previous["active_receipts"]}) if previous else SavedReceipts()
    if payload.get("status") in {"applied", "partial"}:
        active = active.add(payload.get("applied_indices", []))
    items = session._load_ap_items_view().get("items", [])
    pending = [int(item["index"]) for item in items if not active.contains(int(item["index"]))]
    applied = sorted(int(item["index"]) for item in items if active.contains(int(item["index"])))
    last = -1
    while last + 1 in applied:
        last += 1
    atomic_write_json(session.bridge_dir / "runtime_reconciliation_state.json", dict(
        schema="kh3ap.native_reconciliation_state.v1", owner="dll", process_id=777,
        **identity, delivery_epoch=4, initialized=True, ready=True, active_receipts=active.payload(),
        description_revision=previous["description_revision"],
        pending_delivery_indices=pending, pending_replay_indices=pending,
        delivery_summary=dict(received_count=len(items), applied_count=len(applied), pending_count=len(pending),
            applied_item_indices=applied, last_applied_index=last, highest_received_index=max((item["index"] for item in items), default=-1))))
    indices = payload.get("applied_indices", [])
    actions = [mock_native_description(item) for item in items if item["index"] in indices]
    bound = dict(payload, owner="dll", process_id=777, **identity, delivery_epoch=4,
        schema="kh3ap.receipt_ack.v1", receipt_bookkeeping_owner="dll",
        event_key=str(payload.get("request_id")) + "|" + str(payload.get("applied_at")) + "|" + str(indices),
        indices=indices, newly_applied_indices=indices, applied_actions=actions,
        client_notifications=[], pending_item_count=len(pending))
    atomic_write_json(session.bridge_dir / "runtime_item_apply_state.json", dict(
        protocol_name="kh3_ap_file_bridge", protocol_version=1, schema="kh3ap.receipt_apply_state.v1",
        owner="dll", process_id=777, **identity, delivery_epoch=4,
        last_processed_event_key=bound["event_key"], last_processed_event=bound))
    return session.apply_runtime_item_apply_event(bound)
