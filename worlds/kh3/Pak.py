"""KH3's unencrypted Unreal pak V4 subset, using only the Python standard library.

Writes Zlib blocks and reads stored/Zlib files. This is deliberately not a general
Unreal archive tool: unsupported versions, encryption and codecs fail explicitly.
The wire layout is documented by https://github.com/trumank/repak (v0.2.3).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Sequence

MAGIC = 0x5A6F12E1
VERSION = 4
BLOCK_SIZE = 126976
FOOTER = struct.Struct("<BIIQQ20s")
ENTRY = struct.Struct("<QQQI20s")
MAX_INDEX_SIZE = 64 * 1024 * 1024
MAX_FILE_SIZE = 512 * 1024 * 1024


class PakError(ValueError):
    pass


def _read(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise PakError("Truncated pak")
    return data


def _string(stream: BinaryIO) -> str:
    length, = struct.unpack("<i", _read(stream, 4))
    if not length or abs(length) > 32768:
        raise PakError("Invalid pak string length")
    data = _read(stream, abs(length) * (2 if length < 0 else 1))
    terminator = b"\0\0" if length < 0 else b"\0"
    if not data.endswith(terminator):
        raise PakError("Unterminated pak string")
    try:
        return data[:-len(terminator)].decode("utf-16-le" if length < 0 else "utf-8")
    except UnicodeError as exc:
        raise PakError("Invalid pak string encoding") from exc


def _encode_string(value: str) -> bytes:
    if value.isascii():
        data = value.encode("ascii") + b"\0"
        return struct.pack("<i", len(data)) + data
    data = value.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(data) // 2)) + data


def _safe_name(name: str) -> str:
    # Reject Windows drive names/ADS and separators even when running on Linux.
    if (not name or "\\" in name or ":" in name or "\0" in name
            or name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/"))
            or any(p.endswith((".", " ")) for p in name.split("/"))):
        raise PakError(f"Unsafe pak path: {name!r}")
    for part in name.split("/"):
        stem = part.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                    *[f"LPT{i}" for i in range(1, 10)]}:
            raise PakError(f"Reserved pak path: {name!r}")
    return name


class _Entry:
    def __init__(self, stream: BinaryIO):
        self.offset, self.size, self.raw_size, self.codec, self.digest = ENTRY.unpack(_read(stream, ENTRY.size))
        if self.codec not in (0, 1):
            raise PakError(f"Unsupported pak compression method {self.codec}; expected stored or Zlib")
        self.blocks = []
        if self.codec:
            count, = struct.unpack("<I", _read(stream, 4))
            if not 0 < count <= MAX_FILE_SIZE // 1024:
                raise PakError("Invalid pak compression block count")
            self.blocks = [struct.unpack("<QQ", _read(stream, 16)) for _ in range(count)]
        flags, self.block_size = struct.unpack("<BI", _read(stream, 5))
        if flags:
            raise PakError("Encrypted or deleted pak entries are unsupported")
        self.header_size = ENTRY.size + 5 + (4 + 16 * len(self.blocks) if self.codec else 0)
        # repak 0.2.3 writes an extra zero block-count for empty compressed
        # files, although their codec is stored. Accept that legacy encoding.
        if not self.codec and not self.size and not self.raw_size and not self.block_size:
            position = stream.tell()
            if stream.read(4) == struct.pack("<I", BLOCK_SIZE):
                self.header_size += 4
                self.block_size = BLOCK_SIZE
            else:
                stream.seek(position)
        if self.size > MAX_FILE_SIZE or self.raw_size > MAX_FILE_SIZE:
            raise PakError("Pak entry exceeds the KH3 file size limit")
        if self.codec:
            if not 0 < self.block_size <= MAX_FILE_SIZE:
                raise PakError("Invalid pak compression block size")
            if len(self.blocks) != (self.raw_size + self.block_size - 1) // self.block_size:
                raise PakError("Pak block count does not match uncompressed size")
            position = self.offset + self.header_size
            for start, end in self.blocks:
                if start != position or end <= start:
                    raise PakError("Invalid pak compression block offsets")
                position = end
            if position != self.offset + self.header_size + self.size:
                raise PakError("Pak block sizes do not match entry size")
        elif self.size != self.raw_size:
            raise PakError("Stored pak entry has unequal sizes")


class PakReader:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: dict[str, _Entry] = {}
        with self.path.open("rb") as stream:
            size = stream.seek(0, 2)
            if size < FOOTER.size:
                raise PakError("Pak is too short")
            stream.seek(-FOOTER.size, 2)
            encrypted, magic, version, offset, index_size, digest = FOOTER.unpack(_read(stream, FOOTER.size))
            if magic != MAGIC or version != VERSION:
                raise PakError("Unsupported pak format; KH3 Python pak requires V4")
            if encrypted:
                raise PakError("Encrypted pak indexes are unsupported")
            if index_size > MAX_INDEX_SIZE or offset + index_size != size - FOOTER.size:
                raise PakError("Invalid pak index bounds")
            self.index_offset = offset
            stream.seek(offset)
            index_data = _read(stream, index_size)
        if hashlib.sha1(index_data).digest() != digest:
            raise PakError("Pak index SHA-1 mismatch")
        index = io.BytesIO(index_data)
        self.mount_point = _string(index)
        count, = struct.unpack("<I", _read(index, 4))
        if count > index_size // (ENTRY.size + 10):
            raise PakError("Invalid pak entry count")
        names = set()
        for _ in range(count):
            name = _safe_name(_string(index))
            if name.casefold() in names:
                raise PakError(f"Duplicate pak path: {name}")
            names.add(name.casefold())
            entry = _Entry(index)
            if entry.offset + entry.header_size + entry.size > offset:
                raise PakError(f"Pak entry overlaps index: {name}")
            self.entries[name] = entry
        if index.read(1):
            raise PakError("Unexpected trailing pak index data")

    def read(self, name: str, *, max_size: int = MAX_FILE_SIZE) -> bytes:
        entry = self.entries[name]
        if entry.raw_size > max_size:
            raise PakError(f"Pak entry exceeds requested size limit: {name}")
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            # The data header duplicates the index entry but stores offset zero.
            header = bytearray(_read(stream, entry.header_size))
            struct.pack_into("<Q", header, 0, entry.offset)
            local = _Entry(io.BytesIO(header))
            if vars(local) != vars(entry):
                raise PakError(f"Pak data header disagrees with index: {name}")
            data = _read(stream, entry.size)
        if hashlib.sha1(data).digest() != entry.digest:
            raise PakError(f"Pak data SHA-1 mismatch: {name}")
        if not entry.codec:
            return data
        output = bytearray()
        base = entry.offset + entry.header_size
        for start, end in entry.blocks:
            expected = min(entry.block_size, entry.raw_size - len(output))
            decoder = zlib.decompressobj()
            try:
                block = decoder.decompress(data[start - base:end - base], expected + 1)
            except zlib.error as exc:
                raise PakError(f"Invalid Zlib block: {name}") from exc
            if len(block) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise PakError(f"Pak decompression size mismatch: {name}")
            output.extend(block)
        return bytes(output)

    def extract(self, destination: Path | str, *, includes: Sequence[str] = (),
                strip_prefix: str = "../../../", overwrite: bool = False) -> None:
        root = Path(destination).resolve()
        selected = []
        for name in self.entries:
            mounted = self.mount_point + name
            if not mounted.startswith(strip_prefix):
                raise PakError(f"Pak mount does not start with {strip_prefix!r}")
            relative = _safe_name(mounted[len(strip_prefix):])
            if includes and not any(relative == p.rstrip("/") or relative.startswith(p.rstrip("/") + "/")
                                    for p in includes):
                continue
            target = root.joinpath(*PurePosixPath(relative).parts)
            if not target.resolve().is_relative_to(root):
                raise PakError(f"Pak extraction escapes destination: {relative}")
            if target.exists() and not overwrite:
                raise PakError(f"Extraction would overwrite {target}")
            selected.append((name, target))
        for name, target in selected:
            data = self.read(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, lambda stream, data=data: stream.write(data))


def _atomic_write(destination: Path, write) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            write(stream)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def pack_directory(source: Path | str, destination: Path | str, *,
                   mount_point: str = "../../../", compress: bool = True) -> None:
    root, target = Path(source).resolve(), Path(destination).resolve()
    if not root.is_dir():
        raise PakError(f"Pak input directory does not exist: {root}")
    if target.is_relative_to(root):
        raise PakError("Pak output must be outside the input directory")
    files = sorted((path for path in root.rglob("*") if path.is_file()),
                   key=lambda path: path.relative_to(root).as_posix())
    names = [_safe_name(path.relative_to(root).as_posix()) for path in files]
    if len({name.casefold() for name in names}) != len(names):
        raise PakError("Input contains duplicate case-insensitive paths")
    if any(not path.resolve().is_relative_to(root) for path in files):
        raise PakError("Pak input contains a link outside the input directory")

    def write(stream: BinaryIO):
        stored_records = {"KINGDOM HEARTS III/Content/Archipelago/" + filename for filename in
                          ("runtime_seed_settings.json", "slot_data.json", "runtime_seed_hints.json")}
        records = []
        for path, name in zip(files, names):
            if path.stat().st_size > MAX_FILE_SIZE:
                raise PakError(f"Pak input exceeds size limit: {path}")
            raw = path.read_bytes()
            # The DLL reads these bounded records once per process. Stored data
            # needs neither extraction nor a codec during startup.
            codec = int(compress and bool(raw) and name not in stored_records)
            blocks = [zlib.compress(raw[i:i + BLOCK_SIZE], 6) for i in range(0, len(raw), BLOCK_SIZE)] if codec else []
            payload = b"".join(blocks) if codec else raw
            offset = stream.tell()
            header = ENTRY.pack(0, len(payload), len(raw), codec, hashlib.sha1(payload).digest())
            if codec:
                cursor = offset + ENTRY.size + 5 + 4 + 16 * len(blocks)
                header += struct.pack("<I", len(blocks))
                for block in blocks:
                    header += struct.pack("<QQ", cursor, cursor + len(block))
                    cursor += len(block)
            header += struct.pack("<BI", 0, BLOCK_SIZE if codec else 0)
            stream.write(header)
            stream.write(payload)
            records.append(_encode_string(name) + struct.pack("<Q", offset) + header[8:])
        index = _encode_string(mount_point) + struct.pack("<I", len(records)) + b"".join(records)
        if len(index) > MAX_INDEX_SIZE:
            raise PakError("Pak index exceeds size limit")
        offset = stream.tell()
        stream.write(index)
        stream.write(FOOTER.pack(0, MAGIC, VERSION, offset, len(index), hashlib.sha1(index).digest()))

    _atomic_write(target, write)


def run_command(arguments: Sequence[str]) -> str:
    """The small repak-compatible command subset used by the C# randomizer."""
    parser = argparse.ArgumentParser(prog="KH3 Python pak", exit_on_error=False)
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack")
    pack.add_argument("--version", choices=["V4"], default="V4")
    pack.add_argument("--compression", choices=["Zlib"], default="Zlib")
    pack.add_argument("--mount-point", default="../../../")
    pack.add_argument("-q", action="store_true")
    pack.add_argument("source")
    pack.add_argument("destination")
    listing = sub.add_parser("list")
    listing.add_argument("pak")
    unpack = sub.add_parser("unpack")
    unpack.add_argument("-o", required=True)
    unpack.add_argument("-s", default="../../../")
    unpack.add_argument("-q", action="store_true")
    unpack.add_argument("-f", action="store_true")
    unpack.add_argument("-i", action="append", default=[])
    unpack.add_argument("pak")
    args = parser.parse_args(list(arguments))
    if args.command == "pack":
        pack_directory(args.source, args.destination, mount_point=args.mount_point)
    elif args.command == "list":
        return "\n".join(sorted(PakReader(args.pak).entries)) + "\n"
    else:
        PakReader(args.pak).extract(args.o, includes=args.i, strip_prefix=args.s, overwrite=args.f)
    return ""


if __name__ == "__main__":
    import sys
    try:
        print(run_command(sys.argv[1:]), end="")
    except (PakError, OSError) as exc:
        sys.exit(str(exc))
