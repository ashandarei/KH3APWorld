"""Immutable JSON snapshots shared by readers and the publication worker."""

from __future__ import annotations

import json
from typing import Any


def _immutable(*_args: Any, **_kwargs: Any) -> None:
    raise TypeError("JSON snapshot is read-only; copy it before editing")


class JsonObject(dict):
    """Retain dict/JSON compatibility without exposing ordinary mutations."""

    __setitem__ = __delitem__ = __ior__ = _immutable
    clear = pop = popitem = setdefault = update = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, _memo):
        return self


class JsonArray(list):
    """Retain list/JSON compatibility for existing payload consumers."""

    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable
    append = clear = extend = insert = pop = remove = reverse = sort = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, _memo):
        return self


def freeze_json(value: Any) -> Any:
    if isinstance(value, (JsonObject, JsonArray)):
        return value
    if isinstance(value, dict):
        return JsonObject((key, freeze_json(child)) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return JsonArray(freeze_json(child) for child in value)
    return value


def copy_json(value: Any) -> Any:
    """Return a mutable copy only at boundaries that actually need to edit."""
    if isinstance(value, dict):
        return {key: copy_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [copy_json(child) for child in value]
    return value


def freeze_slot_location_catalog(slot_data: dict) -> dict:
    """Detach incoming seed locations so catalog readers can cache by identity.

    The surrounding slot data remains editable. Replacing the catalog creates
    a new revision; mutable catalogs supplied by other callers remain supported.
    """
    bridge = slot_data.get("bridge")
    if not isinstance(bridge, dict) or not isinstance(bridge.get("locations"), (list, tuple)):
        return slot_data
    return {**slot_data, "bridge": {**bridge, "locations": freeze_json(bridge["locations"])}}


def chunked_json_array(chunks: tuple[JsonArray, ...]) -> JsonArray:
    """Share immutable receipt batches, including their lazy JSON encodings.

    The list remains compatible with existing consumers. Chunks contain values,
    not predecessor snapshots, so retaining a history does not retain every old
    full list or recursively encode a long chain of single-item appends.
    """
    result = JsonArray(item for chunk in chunks for item in chunk)
    result._json_chunks = chunks
    return result


def encode_json(value: Any) -> bytes:
    """Canonical UTF-8, memoized only for immutable snapshots.

    Receipt histories reuse encoded batches; only new receipts need JSON
    conversion. Joining the full wire snapshot still copies its bytes once.
    """
    immutable = isinstance(value, (JsonObject, JsonArray))
    cached = getattr(value, "_json_bytes", None) if immutable else None
    if cached is not None:
        return cached
    chunks = getattr(value, "_json_chunks", None) if isinstance(value, JsonArray) else None
    if chunks is not None:
        encoded = b"[" + b",".join(encode_json(chunk)[1:-1] for chunk in chunks if chunk) + b"]"
    elif isinstance(value, JsonObject) and any(
        isinstance(child, JsonArray) and hasattr(child, "_json_chunks") for child in value.values()
    ):
        encoded = b"{" + b",".join(
            encode_json(key) + b":" + encode_json(value[key]) for key in sorted(value)
        ) + b"}"
    else:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if immutable:
        value._json_bytes = encoded
    return encoded
