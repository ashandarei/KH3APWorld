"""Archipelago network custody and readonly native DeathLink status."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from .store import BridgeStore
from .time import UtcClock, system_utc_now, utc_now

NETWORK_FILE = "deathlink_network_state.json"


class DeathLinkStatePayload(TypedDict, total=False):
    """Readonly DLL-owned runtime status."""
    ready: bool
    active: bool
    awaiting_apply: bool
    recovery_pending: bool
    request_id: str
    reason: str


class DeathLinkService:
    """Persist network intents until the DLL accepts them; never plan gameplay."""
    def __init__(self, store: BridgeStore, *, clock: UtcClock = system_utc_now):
        self.store = store
        self.clock = clock

    @staticmethod
    def request_id(data: Mapping[str, Any], clock: UtcClock = system_utc_now) -> str:
        try:
            milliseconds = int(float(data.get("time")) * 1000)
        except (TypeError, ValueError, OverflowError):
            milliseconds = int(utc_now(clock).timestamp() * 1000)
        return f"deathlink-gameover-{milliseconds}"

    def identity(self):
        metadata = self.store.read_optional_json_view("slot_data.json") or {}
        identity = metadata.get("kh3_seed") or {}
        if not identity.get("seed_name") or type(identity.get("slot")) is not int or identity["slot"] <= 0:
            raise ValueError("Missing DeathLink network seed identity")
        return {"seed_name": identity["seed_name"], "slot": identity["slot"]}

    def network(self):
        identity = self.identity()
        state = self.store.read_optional_json(NETWORK_FILE)
        if state is None:
            return {"schema": "kh3ap.deathlink_network.v1", **identity, "incoming": [], "sent_ids": []}
        if (not isinstance(state, dict) or state.get("schema") != "kh3ap.deathlink_network.v1"
                or any(state.get(k) != v for k, v in identity.items())
                or not isinstance(state.get("incoming"), list) or len(state["incoming"]) > 256
                or not isinstance(state.get("sent_ids"), list) or len(state["sent_ids"]) > 4096
                or any(not isinstance(i, str) or not i or len(i) > 160 for i in state["sent_ids"])
                or len(set(state["sent_ids"])) != len(state["sent_ids"])
                or any(not isinstance(i, dict) or not isinstance(i.get("request_id"), str)
                       or not i["request_id"] or len(i["request_id"]) > 160
                       or not isinstance(i.get("source"), str) or not isinstance(i.get("cause"), str)
                       or ("manual" in i and type(i["manual"]) is not bool)
                       for i in state["incoming"])
                or len({i["request_id"] for i in state["incoming"]}) != len(state["incoming"])):
            raise ValueError("Invalid or mismatched DeathLink network checkpoint")
        return state

    def queue(self, data: Mapping[str, Any], *, manual: bool = False):
        state = self.network()
        request_id = self.request_id(data, self.clock)
        if manual:
            request_id = request_id.replace("deathlink-gameover-", "manual-gameover-", 1)
        if not any(i["request_id"] == request_id for i in state["incoming"]):
            if len(state["incoming"]) >= 256:
                raise ValueError("DeathLink network inbox is full")
            state["incoming"].append({"request_id": request_id, "source": str(data.get("source") or "")[:1024],
                                      "cause": str(data.get("cause") or "")[:4096]})
            if manual:
                state["incoming"][-1]["manual"] = True
            self.store.write_json(NETWORK_FILE, state)
        return {"status": "submitted", "request_id": request_id}

    def accepted(self, ids):
        state = self.network()
        remaining = [i for i in state["incoming"] if i["request_id"] not in ids]
        if remaining != state["incoming"]:
            state["incoming"] = remaining
            self.store.write_json(NETWORK_FILE, state)
        return state

    def sent(self, identity):
        state = self.network()
        if identity not in state["sent_ids"]:
            state["sent_ids"] = [*state["sent_ids"], identity][-4096:]
            self.store.write_json(NETWORK_FILE, state)
        return state

    def native_state(self):
        try:
            from ..transport.pipe_transport import transport_for
        except ImportError:
            return None
        transport = transport_for(self.store.root / "runtime_deathlink_state.json")
        payload = transport.read_view("runtime_deathlink_state.json") if transport else None
        if not isinstance(payload, dict):
            return None
        identity = self.identity()
        runtime = self.store.read_optional_json("bridge_runtime_status.json") or {}
        if (payload.get("schema") != "kh3ap.deathlink_state.v1" or payload.get("owner") != "dll"
                or type(payload.get("process_id")) is not int or payload["process_id"] <= 0
                or payload["process_id"] != runtime.get("kh3_native_process_pid", runtime.get("kh3_process_pid"))
                or any(payload.get(k) != v for k, v in identity.items())):
            return None
        return payload
