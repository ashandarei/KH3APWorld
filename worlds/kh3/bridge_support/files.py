from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from ..client_support.json_snapshot import freeze_json
from ..RuntimeSeed import SEED_FILES, encode_document, expand_document, metadata_payload, slot_payload, storage_payload
from ..transport.pipe_transport import PERSISTENT, state_for, transport_for
from .checkpoint import INCREMENTAL, read_bytes, snapshot_bytes
from .diagnostic_history import HISTORIES, history_for


def bridge_exists(path: Path) -> bool:
    transport = transport_for(path)
    state = transport or state_for(path)
    return state.signature(path.name)[0] is not None if state else path.exists()


def acknowledge_pipe_event(path: Path, expected=None) -> None:
    transport = transport_for(path)
    if transport:
        transport.acknowledge(path.name, expected)




def _read_text_with_retries(
    path: Path,
    *,
    attempts: int = 5,
    delay_seconds: float = 0.05,
) -> str:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            return read_bytes(path).decode("utf-8-sig") if path.name in INCREMENTAL else path.read_text(encoding="utf-8-sig")
        except (FileNotFoundError, PermissionError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def read_text_tail_lines(
    path: Path,
    *,
    max_lines: int,
    chunk_size: int = 64 * 1024,
) -> tuple[str, ...]:
    """Read the final text lines without loading the complete file."""

    if max_lines <= 0:
        return ()
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    if path.name in HISTORIES:
        return history_for(path.parent).lines(path.name, max_lines)

    transport = transport_for(path)
    if transport:
        return transport.lines(path.name, max_lines)

    chunks: list[bytes] = []
    newline_count = 0
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        position = source.tell()
        while position > 0 and newline_count <= max_lines:
            read_size = min(chunk_size, position)
            position -= read_size
            source.seek(position)
            chunk = source.read(read_size)
            if not chunk:
                break
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return tuple(text.splitlines()[-max_lines:])


def atomic_write_json(
    path: Path,
    payload: dict[str, Any] | list[Any],
    attempts: int = 10,
    delay_seconds: float = 0.05,
) -> None:
    if path.name == "slot_data.json":
        payload = slot_payload(payload)
    elif path.name == "kh3_ap_seed_metadata.json":
        payload = metadata_payload(payload)
    if path.name in SEED_FILES:
        payload = storage_payload(path.name, payload)
    seed_bytes = encode_document(path.name, payload) if path.name in SEED_FILES else None
    transport = transport_for(path)
    if transport and path.name not in PERSISTENT:
        transport.write(path.name, payload)
        return
    state = state_for(path)
    if state:
        value = state.write(path.name, payload)
        if transport:
            transport.write_encoded(path.name, value, payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    # Machine-owned seed metadata and checkpoints can grow to several MiB.
    # Compact encoding uses the C encoder instead of the Python pretty printer;
    # the atomic replacement and persist-before-pipe-publication order stay the same.
    compact = path.name == "ap_items.json"
    serialized = seed_bytes.decode("utf-8") if seed_bytes is not None else json.dumps(payload, sort_keys=True, **(
        {"separators": (",", ":")} if compact else {"indent": 2}
    ))
    if path.name in INCREMENTAL and path.with_suffix(path.suffix + ".wal").exists():
        temp_path.write_bytes(snapshot_bytes(path, serialized.encode("utf-8")))
    else:
        temp_path.write_text(serialized, encoding="utf-8")
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            os.replace(temp_path, path)
            if path.name in INCREMENTAL:
                path.with_suffix(path.suffix + ".wal").unlink(missing_ok=True)
            if transport:
                transport.write(path.name, payload)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(min(0.5, delay_seconds * (attempt + 1)))
    if temp_path.exists():
        temp_path.unlink(missing_ok=True)
    assert last_error is not None
    raise PermissionError(f"Could not replace locked bridge JSON file: {path}") from last_error


def load_json_file(path: Path) -> dict[str, Any]:
    transport = transport_for(path) or state_for(path)
    if transport:
        value = transport.read(path.name)
        if value is None:
            raise FileNotFoundError(f"KH3 pipe channel is not available: {path.name}")
        return value
    # Be tolerant of BOM-prefixed JSON written by some Windows tooling.
    last_decode_error: json.JSONDecodeError | None = None
    for attempt in range(5):
        text = _read_text_with_retries(path)
        try:
            return expand_document(path.name, json.loads(text))
        except json.JSONDecodeError as exc:
            last_decode_error = exc
            if attempt == 4:
                break
            time.sleep(0.05)
    assert last_decode_error is not None
    raise last_decode_error


def load_optional_json_file(path: Path) -> dict[str, Any] | None:
    transport = transport_for(path) or state_for(path)
    if transport:
        try:
            return transport.read(path.name)
        except (FileNotFoundError, PermissionError, UnicodeError, json.JSONDecodeError):
            return None
    if not path.exists():
        return None
    try:
        return load_json_file(path)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None


def load_optional_json_file_once(path: Path) -> dict[str, Any] | None:
    """Read on the client event loop; transient failures retry on its next poll."""
    transport = transport_for(path) or state_for(path)
    if transport:
        try:
            return transport.read(path.name)
        except (FileNotFoundError, PermissionError, UnicodeError, json.JSONDecodeError):
            return None
    try:
        return expand_document(path.name, json.loads(
            read_bytes(path).decode("utf-8-sig") if path.name in INCREMENTAL else path.read_text(encoding="utf-8-sig")))
    except (FileNotFoundError, PermissionError, UnicodeError, json.JSONDecodeError):
        return None


def load_optional_json_view(path: Path) -> dict[str, Any] | None:
    """Read-only snapshot shared by pipe/state consumers; offline reads retry later."""
    transport = transport_for(path) or state_for(path)
    if transport:
        try:
            return transport.read_view(path.name)
        except (FileNotFoundError, PermissionError, UnicodeError, json.JSONDecodeError):
            return None
    return freeze_json(load_optional_json_file_once(path))


def append_json_line(path: Path, payload: dict[str, Any]) -> None:
    """Append one compact JSON object followed by a newline."""
    if path.name in HISTORIES:
        history_for(path.parent).append(path.name, payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as jsonl_file:
        jsonl_file.write(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")))
        jsonl_file.write("\n")


def load_json_lines(
    path: Path,
    *,
    optional: bool = True,
    ignore_invalid: bool = True,
) -> list[dict[str, Any]]:
    """Read JSON objects from a JSONL file using bridge retry behavior."""
    if path.name in HISTORIES:
        return [json.loads(line) for line in history_for(path.parent).lines(path.name)]
    transport = transport_for(path)
    if transport:
        return [json.loads(line) for line in transport.lines(path.name)]
    try:
        lines = _read_text_with_retries(path).splitlines()
    except FileNotFoundError:
        if optional:
            return []
        raise

    payloads: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if ignore_invalid:
                continue
            raise
        if not isinstance(payload, dict):
            if ignore_invalid:
                continue
            raise ValueError(f"JSONL entry in {path} is not a JSON object.")
        payloads.append(payload)
    return payloads


def file_signature(path: Path) -> tuple[int, int] | tuple[None, None]:
    """Return the bridge polling signature used for one optional file."""
    transport = transport_for(path) or state_for(path)
    if transport:
        return transport.signature(path.name)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (None, None)
    return (stat.st_mtime_ns, stat.st_size)


def safe_unlink(path: Path, *, missing_ok: bool = True) -> bool:
    """Delete an optional bridge file without surfacing races or lock errors."""
    transport = transport_for(path)
    if transport:
        deleted = transport.delete(path.name)
        if path.name not in PERSISTENT:
            return deleted
        # A durable client checkpoint must not reappear on the next startup.
    state = state_for(path)
    if state:
        return state.delete(path.name)
    try:
        path.unlink()
        if path.name in INCREMENTAL:
            path.with_suffix(path.suffix + ".wal").unlink(missing_ok=True)
    except (FileNotFoundError, OSError):
        return False
    return True
