from __future__ import annotations

import argparse
import ctypes
import json
import re
import struct
import sys
from collections import Counter
from ctypes import wintypes

import pymem

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PROTECT = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
WRITABLE_PROTECT = {0x04, 0x08, 0x40, 0x80}

KH3_ABILITY_TABLE_ENTRY_COUNT = 512
KH3_ABILITY_TABLE_BYTE_SIZE = KH3_ABILITY_TABLE_ENTRY_COUNT * 4
KH3_ABILITY_TABLE_ANCHOR_INDICES = (0x01, 0x08, 0x14, 0x1A, 0x1F, 0x29, 0x2D, 0x33)
KH3_UNLOCKED_ABILITY_DATA_BYTES = bytes((1, 3, 5, 7, 9, 11, 13, 15))
KH3_CHARACTER_RECORD_SIZE = 0x9C0
KH3_CHARACTER_ABILITY_OFFSET = 0x160
KH3_CHARACTER_POST_ABILITY_PADDING = 0x20
KH3_ABILITY_TEMPLATE_ANCHORS = {
    0: 0x00000440,
    1: 0x00001440,
    19: 0x00002330,
    28: 0x00010430,
    31: 0x00048440,
    33: 0x00008440,
    41: 0x00009430,
    46: 0x00003330,
    47: 0x0000A110,
    51: 0x00008430,
    54: 0x00040440,
    62: 0x00009410,
    71: 0x00040440,
    72: 0x0000A110,
}


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


if sys.platform == "win32":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    VirtualQueryEx = kernel32.VirtualQueryEx
    VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    VirtualQueryEx.restype = ctypes.c_size_t
else:
    VirtualQueryEx = None


def _require_windows_memory_api() -> None:
    if VirtualQueryEx is None:
        raise RuntimeError("KH3 memory probing is only supported on Windows.")


def _open_process(process_name: str | None = None, pid: int | None = None) -> pymem.Pymem:
    if pid is not None:
        return pymem.Pymem()
    if not process_name:
        raise ValueError("Either process_name or pid is required")
    return pymem.Pymem(process_name)


def _attach_process(process_name: str | None = None, pid: int | None = None) -> pymem.Pymem:
    if pid is not None:
        pm = pymem.Pymem()
        pm.open_process_from_id(pid)
        return pm
    if not process_name:
        raise ValueError("Either process_name or pid is required")
    return pymem.Pymem(process_name)


def _hex_dump(data: bytes, base_address: int) -> str:
    lines: list[str] = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = ascii_view(chunk)
        lines.append(f"{base_address + offset:016X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def iter_readable_regions(process_handle):
    _require_windows_memory_api()
    address = 0
    max_address = 0x7FFFFFFFFFFF
    while address < max_address:
        mbi = MEMORY_BASIC_INFORMATION()
        result = VirtualQueryEx(
            process_handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not result:
            break

        base = mbi.BaseAddress
        size = int(mbi.RegionSize)
        if base is None or size <= 0:
            address += 0x1000
            continue

        base_address = int(base)
        protect = mbi.Protect & 0xFF
        if (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and protect in READABLE_PROTECT
            and protect != PAGE_NOACCESS
        ):
            yield base_address, size

        address = base_address + size


def iter_writable_private_regions(process_handle):
    _require_windows_memory_api()
    address = 0
    max_address = 0x7FFFFFFFFFFF
    while address < max_address:
        mbi = MEMORY_BASIC_INFORMATION()
        result = VirtualQueryEx(
            process_handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not result:
            break

        base = mbi.BaseAddress
        size = int(mbi.RegionSize)
        if base is None or size <= 0:
            address += 0x1000
            continue

        base_address = int(base)
        protect = mbi.Protect & 0xFF
        if (
            mbi.State == MEM_COMMIT
            and mbi.Type == MEM_PRIVATE
            and not (mbi.Protect & PAGE_GUARD)
            and protect in WRITABLE_PROTECT
        ):
            yield base_address, size

        address = base_address + size


def ascii_view(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)


def list_processes() -> list[dict[str, object]]:
    processes: list[dict[str, object]] = []
    for process in pymem.process.list_processes():
        image_name = process.szExeFile.decode("utf-8", "ignore").rstrip("\x00")
        processes.append(
            {
                "pid": int(process.th32ProcessID),
                "name": image_name,
            }
        )
    return processes


def list_modules(process_name: str | None = None, pid: int | None = None) -> list[dict[str, object]]:
    pm = _attach_process(process_name, pid)
    try:
        return [
            {
                "name": module.name,
                "filename": module.filename,
                "base": int(module.lpBaseOfDll),
                "size": int(module.SizeOfImage),
            }
            for module in pymem.process.enum_process_module(pm.process_handle)
        ]
    finally:
        pm.close_process()


def summarize_regions(process_name: str | None = None, pid: int | None = None) -> dict[str, object]:
    pm = _attach_process(process_name, pid)
    try:
        regions = list(iter_readable_regions(pm.process_handle))
        size_counter = Counter()
        total_bytes = 0
        for _, size in regions:
            total_bytes += size
            if size < 0x1000:
                size_counter["lt_4k"] += 1
            elif size < 0x10000:
                size_counter["lt_64k"] += 1
            elif size < 0x100000:
                size_counter["lt_1m"] += 1
            else:
                size_counter["ge_1m"] += 1
        return {
            "region_count": len(regions),
            "total_readable_bytes": total_bytes,
            "size_buckets": dict(size_counter),
            "largest_regions": [
                {"base": base, "size": size}
                for base, size in sorted(regions, key=lambda item: item[1], reverse=True)[:20]
            ],
        }
    finally:
        pm.close_process()


def scan_for_needles(
    process_name: str | None,
    needles: list[str],
    max_region_size: int,
    pid: int | None = None,
) -> dict[str, list[tuple[int, int, int]]]:
    pm = _attach_process(process_name, pid)
    try:
        hits: dict[str, list[tuple[int, int, int]]] = {needle: [] for needle in needles}
        encoded_needles = {needle: needle.encode("ascii") for needle in needles}
        for base_address, size in iter_readable_regions(pm.process_handle):
            if size > max_region_size:
                continue
            try:
                data = pm.read_bytes(base_address, size)
            except Exception:
                continue

            for needle, encoded in encoded_needles.items():
                start = 0
                while True:
                    index = data.find(encoded, start)
                    if index == -1:
                        break
                    hits[needle].append((base_address + index, base_address, size))
                    start = index + 1
        return hits
    finally:
        pm.close_process()


def scan_for_bytes(
    process_name: str | None,
    needle_hex: str,
    max_region_size: int,
    pid: int | None = None,
) -> list[tuple[int, int, int]]:
    cleaned = needle_hex.replace(" ", "")
    encoded = bytes.fromhex(cleaned)
    pm = _attach_process(process_name, pid)
    try:
        hits: list[tuple[int, int, int]] = []
        for base_address, size in iter_readable_regions(pm.process_handle):
            if size > max_region_size:
                continue
            try:
                data = pm.read_bytes(base_address, size)
            except Exception:
                continue
            start = 0
            while True:
                index = data.find(encoded, start)
                if index == -1:
                    break
                hits.append((base_address + index, base_address, size))
                start = index + 1
        return hits
    finally:
        pm.close_process()


def _build_ability_table_anchor_pattern() -> re.Pattern[bytes]:
    unlocked = b"[" + bytes(KH3_UNLOCKED_ABILITY_DATA_BYTES) + b"]\\x00\\x00\\x00"
    pieces = [unlocked]
    for previous, current in zip(
        KH3_ABILITY_TABLE_ANCHOR_INDICES,
        KH3_ABILITY_TABLE_ANCHOR_INDICES[1:],
    ):
        gap = (current - previous - 1) * 4
        pieces.append(b".{" + str(gap).encode("ascii") + b"}")
        pieces.append(unlocked)
    return re.compile(b"(?=(" + b"".join(pieces) + b"))", re.DOTALL)


def _summarize_ability_table_candidate(address: int, table_bytes: bytes) -> dict[str, object] | None:
    if len(table_bytes) != KH3_ABILITY_TABLE_BYTE_SIZE or address % 4 != 0:
        return None
    values = struct.unpack("<512I", table_bytes)
    if any((values[index] & 1) == 0 or values[index] > 15 for index in KH3_ABILITY_TABLE_ANCHOR_INDICES):
        return None

    small_value_count = sum(value <= 15 for value in values)
    nonzero_indices = [index for index, value in enumerate(values) if value]
    high_tail_nonzero = sum(value != 0 for value in values[0x100:])
    if small_value_count != KH3_ABILITY_TABLE_ENTRY_COUNT or not 8 <= len(nonzero_indices) <= 256 or high_tail_nonzero != 0:
        return None

    return {
        "address": address,
        "nonzero_count": len(nonzero_indices),
        "small_value_count": small_value_count,
        "high_tail_nonzero": high_tail_nonzero,
        "anchor_values": {
            f"0x{index:03X}": values[index]
            for index in KH3_ABILITY_TABLE_ANCHOR_INDICES
        },
        "nonzero_entries": [
            {"index": index, "data": values[index]}
            for index in nonzero_indices[:256]
        ],
    }


def scan_for_kh3_ability_tables(
    process_name: str | None,
    pid: int | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    """Find raw KH3 512-entry ability bitfield tables without reading save files."""
    if chunk_size < KH3_ABILITY_TABLE_BYTE_SIZE * 2:
        raise ValueError("chunk_size must be at least twice the KH3 ability table size")

    pm = _attach_process(process_name, pid)
    anchor_pattern = _build_ability_table_anchor_pattern()
    anchor_offset = KH3_ABILITY_TABLE_ANCHOR_INDICES[0] * 4
    overlap = KH3_ABILITY_TABLE_BYTE_SIZE + anchor_offset
    candidates_by_address: dict[int, dict[str, object]] = {}
    scanned_region_count = 0
    scanned_bytes = 0
    try:
        for region_base, region_size in iter_writable_private_regions(pm.process_handle):
            scanned_region_count += 1
            offset = 0
            while offset < region_size:
                read_start = max(0, offset - overlap)
                read_size = min(chunk_size + overlap, region_size - read_start)
                try:
                    data = pm.read_bytes(region_base + read_start, read_size)
                except Exception:
                    offset += chunk_size
                    continue
                scanned_bytes += len(data)

                for match in anchor_pattern.finditer(data):
                    table_start_in_chunk = match.start(1) - anchor_offset
                    if table_start_in_chunk < 0:
                        continue
                    table_end_in_chunk = table_start_in_chunk + KH3_ABILITY_TABLE_BYTE_SIZE
                    if table_end_in_chunk > len(data):
                        continue
                    address = region_base + read_start + table_start_in_chunk
                    if address in candidates_by_address:
                        continue
                    summary = _summarize_ability_table_candidate(
                        address,
                        data[table_start_in_chunk:table_end_in_chunk],
                    )
                    if summary is None:
                        continue
                    summary["region_base"] = region_base
                    summary["region_size"] = region_size
                    candidates_by_address[address] = summary

                offset += chunk_size
    finally:
        pm.close_process()

    return {
        "scanned_region_count": scanned_region_count,
        "scanned_bytes": scanned_bytes,
        "candidate_count": len(candidates_by_address),
        "candidates": list(candidates_by_address.values()),
    }


def _masked_ability_entry_pattern(template_high_bits: int) -> bytes:
    raw = struct.pack("<I", template_high_bits)
    low_byte_min = raw[0]
    low_byte_max = low_byte_min | 0x0F
    return (
        b"[\\x"
        + f"{low_byte_min:02x}".encode("ascii")
        + b"-\\x"
        + f"{low_byte_max:02x}".encode("ascii")
        + b"]"
        + re.escape(raw[1:])
    )


def _build_ability_template_pattern() -> re.Pattern[bytes]:
    pieces: list[bytes] = []
    previous_index = -1
    for index, template_high_bits in KH3_ABILITY_TEMPLATE_ANCHORS.items():
        gap = (index - previous_index - 1) * 4
        if gap:
            pieces.append(b".{" + str(gap).encode("ascii") + b"}")
        pieces.append(_masked_ability_entry_pattern(template_high_bits))
        previous_index = index
    return re.compile(b"(?=(" + b"".join(pieces) + b"))", re.DOTALL)


def _summarize_template_ability_table(address: int, table_bytes: bytes) -> dict[str, object] | None:
    if len(table_bytes) != KH3_ABILITY_TABLE_BYTE_SIZE or address % 4 != 0:
        return None
    values = struct.unpack("<512I", table_bytes)
    if any((values[index] & ~0x0F) != expected for index, expected in KH3_ABILITY_TEMPLATE_ANCHORS.items()):
        return None

    unlocked = [index for index, value in enumerate(values) if value & 0x01]
    enabled = [index for index, value in enumerate(values) if value & 0x02]
    unseen = [index for index, value in enumerate(values) if value & 0x04]
    return {
        "address": address,
        "unlocked_count": len(unlocked),
        "enabled_count": len(enabled),
        "unseen_count": len(unseen),
        "unlocked_indices": unlocked,
        "enabled_indices": enabled,
        "test_index_entries": {
            f"0x{index:03X}": {
                "data": values[index],
                "flags": values[index] & 0x0F,
            }
            for index in (1, 8, 0x14, 0x1A, 0x1F, 0x23, 0x29, 0x2D, 0x33)
        },
    }


def scan_for_kh3_template_ability_tables(
    process_name: str | None,
    pid: int | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    """Locate native KH3 ability tables using stable per-entry template bits."""
    if chunk_size < KH3_ABILITY_TABLE_BYTE_SIZE * 2:
        raise ValueError("chunk_size must be at least twice the KH3 ability table size")

    pm = _attach_process(process_name, pid)
    pattern = _build_ability_template_pattern()
    overlap = KH3_ABILITY_TABLE_BYTE_SIZE
    candidates_by_address: dict[int, dict[str, object]] = {}
    scanned_region_count = 0
    scanned_bytes = 0
    try:
        for region_base, region_size in iter_writable_private_regions(pm.process_handle):
            scanned_region_count += 1
            offset = 0
            while offset < region_size:
                read_start = max(0, offset - overlap)
                read_size = min(chunk_size + overlap, region_size - read_start)
                try:
                    data = pm.read_bytes(region_base + read_start, read_size)
                except Exception:
                    offset += chunk_size
                    continue
                scanned_bytes += len(data)

                for match in pattern.finditer(data):
                    table_start = match.start(1)
                    table_end = table_start + KH3_ABILITY_TABLE_BYTE_SIZE
                    if table_end > len(data):
                        continue
                    address = region_base + read_start + table_start
                    if address in candidates_by_address:
                        continue
                    summary = _summarize_template_ability_table(
                        address,
                        data[table_start:table_end],
                    )
                    if summary is None:
                        continue
                    summary["region_base"] = region_base
                    summary["region_size"] = region_size
                    candidates_by_address[address] = summary

                offset += chunk_size
    finally:
        pm.close_process()

    return {
        "scanned_region_count": scanned_region_count,
        "scanned_bytes": scanned_bytes,
        "candidate_count": len(candidates_by_address),
        "candidates": list(candidates_by_address.values()),
    }


def _summarize_character_record_candidate(
    character_address: int,
    context_bytes: bytes,
    character_offset: int,
) -> dict[str, object] | None:
    ability_offset = character_offset + KH3_CHARACTER_ABILITY_OFFSET
    ability_end = ability_offset + KH3_ABILITY_TABLE_BYTE_SIZE
    stats_offset = ability_end + KH3_CHARACTER_POST_ABILITY_PADDING
    required_size = stats_offset + 16
    if character_offset < 0 or required_size > len(context_bytes):
        return None

    current_weapon_index = context_bytes[character_offset + 6]
    if current_weapon_index > 2:
        return None

    table_bytes = context_bytes[ability_offset:ability_end]
    values = struct.unpack("<512I", table_bytes)
    if any(value > 15 for value in values):
        return None
    nonzero_indices = [index for index, value in enumerate(values) if value]
    if not 1 <= len(nonzero_indices) <= 256:
        return None
    if any(values[index] != 0 for index in range(0x100, KH3_ABILITY_TABLE_ENTRY_COUNT)):
        return None
    if any(context_bytes[ability_end:stats_offset]):
        return None

    attack_boost, magic_boost, defense_boost, ap_boost = context_bytes[stats_offset:stats_offset + 4]
    hp, mp, focus = struct.unpack_from("<iii", context_bytes, stats_offset + 4)
    if not (1 <= hp <= 10000 and 0 <= mp <= 10000 and 0 <= focus <= 10000):
        return None

    return {
        "character_address": character_address,
        "ability_table_address": character_address + KH3_CHARACTER_ABILITY_OFFSET,
        "current_weapon_index": current_weapon_index,
        "boosts": {
            "attack": attack_boost,
            "magic": magic_boost,
            "defense": defense_boost,
            "ap": ap_boost,
        },
        "stats": {"hp": hp, "mp": mp, "focus": focus},
        "nonzero_count": len(nonzero_indices),
        "nonzero_entries": [
            {"index": index, "data": values[index]}
            for index in nonzero_indices
        ],
    }


def scan_for_kh3_character_records(
    process_name: str | None,
    pid: int | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    """Locate native KH3 character records by their ability/stat layout."""
    if chunk_size < KH3_CHARACTER_RECORD_SIZE * 2:
        raise ValueError("chunk_size must be at least twice the KH3 character record size")

    pm = _attach_process(process_name, pid)
    zero_tail_pattern = re.compile(b"\\x00{1024,}")
    overlap = KH3_CHARACTER_RECORD_SIZE * 2
    candidates_by_address: dict[int, dict[str, object]] = {}
    scanned_region_count = 0
    scanned_bytes = 0
    try:
        for region_base, region_size in iter_writable_private_regions(pm.process_handle):
            scanned_region_count += 1
            offset = 0
            while offset < region_size:
                read_start = max(0, offset - overlap)
                read_size = min(chunk_size + overlap, region_size - read_start)
                try:
                    data = pm.read_bytes(region_base + read_start, read_size)
                except Exception:
                    offset += chunk_size
                    continue
                scanned_bytes += len(data)

                for match in zero_tail_pattern.finditer(data):
                    # The zero run normally ends in the 0x20-byte gap after the
                    # ability table. Zero boost bytes can extend it by up to four
                    # bytes before the first non-zero HP byte.
                    for trailing_zero_count in range(0x20, 0x31):
                        table_end = match.end() - trailing_zero_count
                        table_start = table_end - KH3_ABILITY_TABLE_BYTE_SIZE
                        character_offset = table_start - KH3_CHARACTER_ABILITY_OFFSET
                        if character_offset < 0 or character_offset % 4 != 0:
                            continue
                        character_address = region_base + read_start + character_offset
                        if character_address in candidates_by_address:
                            continue
                        summary = _summarize_character_record_candidate(
                            character_address,
                            data,
                            character_offset,
                        )
                        if summary is None:
                            continue
                        summary["region_base"] = region_base
                        summary["region_size"] = region_size
                        candidates_by_address[character_address] = summary

                offset += chunk_size
    finally:
        pm.close_process()

    return {
        "scanned_region_count": scanned_region_count,
        "scanned_bytes": scanned_bytes,
        "candidate_count": len(candidates_by_address),
        "candidates": list(candidates_by_address.values()),
    }


def dump_context(process_name: str | None, address: int, radius: int, pid: int | None = None) -> dict[str, str]:
    pm = _attach_process(process_name, pid)
    try:
        start = max(0, address - radius)
        data = pm.read_bytes(start, radius * 2)
        return {
            "ascii": ascii_view(data),
            "hex_dump": _hex_dump(data, start),
        }
    finally:
        pm.close_process()


def dump_region(
    process_name: str | None,
    address: int,
    length: int,
    pid: int | None = None,
) -> bytes:
    pm = _attach_process(process_name, pid)
    try:
        return pm.read_bytes(address, length)
    finally:
        pm.close_process()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KH3 memory reconnaissance helper.")
    parser.add_argument(
        "--process-name",
        default="KINGDOM HEARTS III.exe",
        help="Target process name.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Target process id. Overrides --process-name when provided.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("processes", help="List running processes.")
    subparsers.add_parser("modules", help="List loaded modules for the target process.")
    subparsers.add_parser("regions", help="Summarize readable memory regions for the target process.")

    find_parser = subparsers.add_parser("find", help="Scan readable memory for ASCII needles.")
    find_parser.add_argument("needles", nargs="+", help="ASCII strings to search for.")
    find_parser.add_argument(
        "--max-region-size",
        type=int,
        default=2_000_000,
        help="Skip very large memory regions while scanning.",
    )

    find_bytes_parser = subparsers.add_parser("find-bytes", help="Scan readable memory for a hex byte pattern.")
    find_bytes_parser.add_argument("needle_hex", help="Hex byte sequence, for example '53 40 76 45'.")
    find_bytes_parser.add_argument(
        "--max-region-size",
        type=int,
        default=2_000_000,
        help="Skip very large memory regions while scanning.",
    )

    ability_parser = subparsers.add_parser(
        "find-ability-tables",
        help="Scan writable private memory for KH3's native 512-entry ability tables.",
    )
    ability_parser.add_argument(
        "--chunk-size",
        type=int,
        default=8 * 1024 * 1024,
        help="Bytes read per memory chunk.",
    )

    character_parser = subparsers.add_parser(
        "find-character-records",
        help="Scan writable private memory for KH3's native character/ability records.",
    )
    character_parser.add_argument(
        "--chunk-size",
        type=int,
        default=8 * 1024 * 1024,
        help="Bytes read per memory chunk.",
    )

    template_parser = subparsers.add_parser(
        "find-template-ability-tables",
        help="Locate KH3 ability tables by their stable native template bits.",
    )
    template_parser.add_argument(
        "--chunk-size",
        type=int,
        default=8 * 1024 * 1024,
        help="Bytes read per memory chunk.",
    )

    context_parser = subparsers.add_parser("context", help="Dump ASCII context around an address.")
    context_parser.add_argument("address", help="Hex or decimal address.")
    context_parser.add_argument(
        "--radius",
        type=int,
        default=512,
        help="Bytes to show before and after the address.",
    )

    dump_region_parser = subparsers.add_parser("dump-region", help="Dump raw bytes for a memory region.")
    dump_region_parser.add_argument("address", help="Hex or decimal base address.")
    dump_region_parser.add_argument("length", type=int, help="Number of bytes to read.")
    dump_region_parser.add_argument("--output", type=str, help="Optional file path for raw bytes.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "processes":
        print(json.dumps(list_processes(), indent=2))
        return 0

    if args.command == "modules":
        print(json.dumps(list_modules(args.process_name, args.pid), indent=2))
        return 0

    if args.command == "regions":
        print(json.dumps(summarize_regions(args.process_name, args.pid), indent=2))
        return 0

    if args.command == "find":
        hits = scan_for_needles(args.process_name, args.needles, args.max_region_size, args.pid)
        for needle, values in hits.items():
            print(f"{needle}: {len(values)} hits")
            for address, region_base, region_size in values[:20]:
                print(f"  {hex(address)} region={hex(region_base)} size={region_size}")
        return 0

    if args.command == "find-bytes":
        hits = scan_for_bytes(args.process_name, args.needle_hex, args.max_region_size, args.pid)
        print(f"{args.needle_hex}: {len(hits)} hits")
        for address, region_base, region_size in hits[:20]:
            print(f"  {hex(address)} region={hex(region_base)} size={region_size}")
        return 0

    if args.command == "find-ability-tables":
        payload = scan_for_kh3_ability_tables(
            args.process_name,
            args.pid,
            args.chunk_size,
        )
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "find-character-records":
        payload = scan_for_kh3_character_records(
            args.process_name,
            args.pid,
            args.chunk_size,
        )
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "find-template-ability-tables":
        payload = scan_for_kh3_template_ability_tables(
            args.process_name,
            args.pid,
            args.chunk_size,
        )
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "context":
        address = int(args.address, 0)
        payload = dump_context(args.process_name, address, args.radius, args.pid)
        print(payload["hex_dump"])
        print()
        print(payload["ascii"])
        return 0

    if args.command == "dump-region":
        address = int(args.address, 0)
        data = dump_region(args.process_name, address, args.length, args.pid)
        if args.output:
            with open(args.output, "wb") as file:
                file.write(data)
            print(f"Wrote {len(data)} bytes to {args.output}")
        else:
            print(_hex_dump(data, address))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
