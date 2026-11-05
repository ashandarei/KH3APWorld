"""Structured observability records shared by KH3 bridge services."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .paths import RUNTIME_ITEM_LATENCY_TRACE_FILE
from .diagnostic_history import history_for

RUNTIME_ITEM_LATENCY_TRACE_SCHEMA = "kh3ap.runtime_item_latency_trace.v1"
RUNTIME_ITEM_LATENCY_TRACE_ENABLED = os.environ.get(
    "KH3_RUNTIME_ITEM_LATENCY_TRACE_ENABLED", "1"
).strip().lower() not in {"0", "false", "no", "off"}


def append_runtime_item_latency_trace_event(
    bridge_dir: Path | str | None,
    event: str,
    *,
    recorded_at_utc: str | None = None,
    **fields: Any,
) -> bool:
    """Retain one item-delivery boundary event for on-demand diagnostics."""

    if not RUNTIME_ITEM_LATENCY_TRACE_ENABLED or bridge_dir is None:
        return False
    bridge_path = Path(bridge_dir)
    payload = {
        "schema": RUNTIME_ITEM_LATENCY_TRACE_SCHEMA,
        "event": event,
        **fields,
        "recorded_at_utc": recorded_at_utc or datetime.now(timezone.utc).isoformat(),
    }
    history_for(bridge_path).append(RUNTIME_ITEM_LATENCY_TRACE_FILE, payload)
    return True


@dataclass(frozen=True)
class RuntimeGrantProfileRecord:
    """Immutable input for one runtime-grant profile JSONL record."""

    protocol_name: str
    protocol_version: int
    event: str
    bridge_dir: str
    fields: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    def signature_payload(self) -> dict[str, Any]:
        """Build the exact pre-sequence payload used for deduplication."""
        return {
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
            "event": self.event,
            "bridge_dir": self.bridge_dir,
            **self.fields,
        }

    def signature(self) -> str:
        """Return the stable compatibility signature for this event."""
        return json.dumps(self.signature_payload(), sort_keys=True, default=str, separators=(",", ":"))

    def to_payload(self, *, profile_seq: int, recorded_at_utc: str) -> dict[str, Any]:
        """Add the established sequence and timestamp fields for persistence."""
        payload = self.signature_payload()
        payload["profile_seq"] = profile_seq
        payload["recorded_at_utc"] = recorded_at_utc
        return payload
