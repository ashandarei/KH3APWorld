"""Read/write KH3's cooked UE4.17 tagged assets without native dependencies.

Untouched names, tags, imports, export metadata and opaque payloads are retained.
Only the package layout used by the bundled KH3 templates is accepted.
"""

from __future__ import annotations

import copy
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x9E2A83C1


class AssetError(ValueError):
    pass


class Reader:
    def __init__(self, data: bytes, asset=None):
        self.data, self.pos, self.asset = data, 0, asset

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise AssetError(f"Truncated asset at {self.pos:#x}, requesting {size} bytes")
        value = self.data[self.pos : self.pos + size]
        self.pos += size
        return value

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def string(self):
        size = self.unpack("i")
        if abs(size) > 16 * 1024 * 1024:
            raise AssetError("Invalid FString length")
        if not size:
            return ""
        raw = self.take(abs(size) * (2 if size < 0 else 1))
        value = raw.decode("utf-16-le" if size < 0 else "utf-8")
        if not value.endswith("\0"):
            raise AssetError("Unterminated FString")
        return value[:-1]

    def name(self):
        index, number = struct.unpack("<ii", self.take(8))
        if not 0 <= index < len(self.asset.names) or number < 0:
            raise AssetError(f"Invalid FName {index}:{number}")
        base = self.asset.names[index]
        value = f"{base}_{number - 1}" if number else base
        self.asset.name_indexes.setdefault(value, (index, number))
        return value


def fstring(value: str, wide=False):
    if not value:
        return struct.pack("<i", 0)
    wide = wide or not value.isascii()
    encoded = (value + "\0").encode("utf-16-le" if wide else "utf-8")
    return struct.pack("<i", -len(encoded) // 2 if wide else len(encoded)) + encoded


def name_hash(value: str):
    # UE4's deprecated case-insensitive CRC plus its case-preserving UTF-32 CRC.
    insensitive = 0
    for byte in "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in value).encode("utf-8"):
        entry = (insensitive ^ byte) & 255
        table = entry << 24
        for _ in range(8):
            table = ((table << 1) ^ (0x04C11DB7 if table & 0x80000000 else 0)) & 0xFFFFFFFF
        insensitive = (insensitive >> 8) ^ table
    units = struct.unpack("<" + "H" * (len(value.encode("utf-16-le")) // 2), value.encode("utf-16-le"))
    sensitive = zlib.crc32(b"".join(struct.pack("<I", unit) for unit in units))
    return (insensitive & 0xFFFF) | ((sensitive & 0xFFFF) << 16)


@dataclass
class Property:
    name: str
    kind: str
    payload: bytes
    extra: bytes = b""
    index: int = 0
    value: object = None
    inner: str | None = None
    dirty: bool = False
    array_header: bytes = b""

    def set(self, value):
        self.value, self.dirty = value, True

    def encode(self, asset):
        extra, payload = self.extra, self.payload
        if self.kind == "StructProperty" and isinstance(self.value, list):
            payload = asset.encode_properties(self.value)
        elif self.kind == "ArrayProperty" and self.inner == "StructProperty" and isinstance(self.value, list):
            data = b"".join(asset.encode_properties(props) for props in self.value)
            header = bytearray(self.array_header)
            struct.pack_into("<q", header, 16, len(data))
            payload = struct.pack("<i", len(self.value)) + header + data
        elif self.dirty:
            if self.kind in ("NameProperty", "EnumProperty"):
                payload = asset.name(str(self.value).rstrip("\0"))
            elif self.kind in ("StrProperty", "AssetObjectProperty", "SoftObjectProperty"):
                if self.kind == "SoftObjectProperty":
                    raise AssetError("SoftObjectProperty editing is unsupported")
                payload = fstring(str(self.value).rstrip("\0"), struct.unpack("<i", self.payload[:4])[0] < 0)
            elif self.kind == "BoolProperty":
                extra = bytes([bool(self.value)]) + extra[1:]
                payload = b""
            elif self.kind in ("IntProperty", "UInt32Property", "FloatProperty", "Int64Property", "DoubleProperty"):
                fmt = {
                    "IntProperty": "i",
                    "UInt32Property": "I",
                    "FloatProperty": "f",
                    "Int64Property": "q",
                    "DoubleProperty": "d",
                }[self.kind]
                payload = struct.pack("<" + fmt, self.value)
            elif self.kind == "ArrayProperty" and self.inner in ("NameProperty", "EnumProperty"):
                payload = struct.pack("<i", len(self.value)) + b"".join(
                    asset.name(str(v).rstrip("\0")) for v in self.value
                )
            else:
                raise AssetError(f"Cannot edit {self.kind} {self.name}")
        return (
            asset.name(self.name)
            + asset.name(self.kind)
            + struct.pack("<ii", len(payload), self.index)
            + extra
            + payload
        )


def get(properties, name):
    matches = [p for p in properties if p.name == name]
    if len(matches) != 1:
        raise AssetError(f"Expected one {name} property; found {len(matches)}")
    return matches[0]


@dataclass
class Export:
    map_offset: int
    serial_offset: int
    serial_size: int
    properties: list[Property]
    object_guid: bytes
    rows: dict[str, list[Property]] | None
    tail: bytes


class Asset:
    def __init__(self, primary: bytes, sidecar: bytes):
        self.primary, self.sidecar = primary, sidecar
        r = Reader(primary)
        if r.unpack("I") != MAGIC or r.unpack("i") != -7:
            raise AssetError("Expected a cooked UE4 KH3 package")
        r.take(12)
        custom_count = r.unpack("i")
        if not 0 <= custom_count <= 128:
            raise AssetError("Invalid custom version count")
        r.take(custom_count * 20)
        self.header_size_pos = r.pos
        self.header_size = r.unpack("i")
        if self.header_size != len(primary):
            raise AssetError("Expected a split cooked package")
        r.string()
        self.flags = r.unpack("I")
        self.name_count_pos = r.pos
        name_count = r.unpack("i")
        self.name_offset = r.unpack("i")
        self.offset_fields = []

        def offset():
            self.offset_fields.append(r.pos)
            return r.unpack("i")

        r.unpack("i")
        offset()  # gatherable text
        export_count = r.unpack("i")
        export_offset = offset()
        self.import_count = r.unpack("i")
        self.import_offset = offset()
        offset()
        r.unpack("i")
        offset()
        offset()
        offset()
        r.take(16)
        generation_count = r.unpack("i")
        self.generation_name_positions = []
        for _ in range(generation_count):
            r.unpack("i")
            self.generation_name_positions.append(r.pos)
            r.unpack("i")
        for _ in range(2):
            r.take(10)
            r.string()
        r.unpack("I")
        if r.unpack("i") != 0:
            raise AssetError("Package-level compression is unsupported")
        r.unpack("I")
        for _ in range(r.unpack("i")):
            r.string()
        offset()
        self.bulk_offset_pos = r.pos
        self.bulk_offset = r.unpack("q")
        offset()
        r.take(4 * r.unpack("i"))
        r.unpack("i")
        offset()
        if r.pos != self.name_offset:
            raise AssetError(f"Unsupported package summary: {r.pos} != {self.name_offset}")
        self.names, self.name_bytes, self.name_indexes = [], [], {}
        for index in range(name_count):
            start = r.pos
            value = r.string()
            r.take(4)
            self.names.append(value)
            self.name_indexes.setdefault(value, (index, 0))
            self.name_bytes.append(primary[start : r.pos])
        self.original_name_count = name_count
        self.name_end = r.pos
        self.import_classes = []
        for index in range(self.import_count):
            ir = Reader(primary, self)
            ir.pos = self.import_offset + index * 28
            ir.name()
            ir.name()
            ir.take(4)
            self.import_classes.append(ir.name())
        self.exports = []
        for index in range(export_count):
            entry = export_offset + index * 104
            class_index = struct.unpack_from("<i", primary, entry)[0]
            serial_size, serial_offset = struct.unpack_from("<qq", primary, entry + 28)
            er = Reader(sidecar[serial_offset - len(primary) : serial_offset - len(primary) + serial_size], self)
            props = self.read_properties(er)
            guid_flag = er.take(4)
            guid = guid_flag + (er.take(16) if struct.unpack("<i", guid_flag)[0] else b"")
            rows = None
            class_name = self.import_classes[-class_index - 1] if class_index < 0 else ""
            if "DataTable" in class_name:
                rows = {}
                count = er.unpack("i")
                if not 0 <= count <= 100000:
                    raise AssetError("Invalid DataTable row count")
                for _ in range(count):
                    row = er.name()
                    if row in rows:
                        raise AssetError(f"Duplicate table row {row}")
                    rows[row] = self.read_properties(er)
            self.exports.append(
                Export(entry, serial_offset, serial_size, props, guid, rows, er.take(len(er.data) - er.pos))
            )

    @classmethod
    def load(cls, path: Path):
        return cls(path.read_bytes(), path.with_suffix(".uexp").read_bytes())

    def name(self, value):
        value = value.rstrip("\0")
        if value not in self.name_indexes:
            self.name_indexes[value] = (len(self.names), 0)
            self.names.append(value)
            self.name_bytes.append(fstring(value) + struct.pack("<I", name_hash(value)))
        return struct.pack("<ii", *self.name_indexes[value])

    def read_properties(self, r):
        props = []
        while True:
            name = r.name()
            if name == "None":
                return props
            kind = r.name()
            size = r.unpack("i")
            index = r.unpack("i")
            start = r.pos
            inner = None
            if kind == "StructProperty":
                inner = r.name()
                r.take(16)
            elif kind in ("EnumProperty", "ByteProperty", "ArrayProperty", "SetProperty"):
                inner = r.name()
            elif kind == "MapProperty":
                r.name()
                r.name()
            elif kind == "BoolProperty":
                r.take(1)
            guid_flag = r.unpack("B")
            if guid_flag not in (0, 1):
                raise AssetError("Invalid property GUID flag")
            if guid_flag:
                r.take(16)
            extra = r.data[start : r.pos]
            payload = r.take(size)
            prop = Property(name, kind, payload, extra, index, inner=inner)
            pr = Reader(payload, self)
            if kind in ("NameProperty", "EnumProperty"):
                prop.value = pr.name()
            elif kind in ("StrProperty", "AssetObjectProperty"):
                prop.value = pr.string()
            elif kind == "BoolProperty":
                prop.value = bool(extra[0])
            elif kind in ("IntProperty", "FloatProperty", "Int64Property", "UInt32Property", "DoubleProperty"):
                prop.value = pr.unpack(
                    {
                        "IntProperty": "i",
                        "FloatProperty": "f",
                        "Int64Property": "q",
                        "UInt32Property": "I",
                        "DoubleProperty": "d",
                    }[kind]
                )
            elif kind == "StructProperty" and inner not in (
                "Vector",
                "Vector2D",
                "Rotator",
                "LinearColor",
                "Color",
                "Quat",
                "Guid",
            ):
                prop.value = self.read_properties(pr)
                if pr.pos != len(payload):
                    raise AssetError(f"Unparsed struct bytes in {name}")
            elif kind == "ArrayProperty" and inner in ("NameProperty", "EnumProperty"):
                count = pr.unpack("i")
                if count < 0 or count > len(payload) // 8:
                    raise AssetError("Invalid name array count")
                prop.value = [pr.name() for _ in range(count)]
            elif kind == "ArrayProperty" and inner == "StructProperty":
                count = pr.unpack("i")
                tag_start = pr.pos
                pr.name()
                pr.name()
                pr.take(8)
                struct_type = pr.name()
                pr.take(16)
                if pr.unpack("B"):
                    pr.take(16)
                prop.array_header = payload[tag_start : pr.pos]
                if struct_type not in (
                    "Vector",
                    "Vector2D",
                    "Rotator",
                    "LinearColor",
                    "Color",
                    "Quat",
                    "Guid",
                    "MaterialParameterInfo",
                ):
                    prop.value = [self.read_properties(pr) for _ in range(count)]
                    if pr.pos != len(payload):
                        raise AssetError(f"Unparsed array struct bytes in {name}")
            props.append(prop)

    def encode_properties(self, properties):
        return b"".join(prop.encode(self) for prop in properties) + self.name("None")

    @property
    def rows(self):
        tables = [export.rows for export in self.exports if export.rows is not None]
        if len(tables) != 1:
            raise AssetError("Expected one DataTable export")
        return tables[0]

    def clone_row(self, row):
        return copy.deepcopy(self.rows[row])

    def encode(self):
        contents = []
        for export in self.exports:
            data = self.encode_properties(export.properties) + export.object_guid
            if export.rows is not None:
                data += struct.pack("<i", len(export.rows))
                data += b"".join(self.name(row) + self.encode_properties(props) for row, props in export.rows.items())
            contents.append(data + export.tail)
        names = b"".join(self.name_bytes)
        delta = len(names) - (self.name_end - self.name_offset)
        primary = bytearray(self.primary[: self.name_offset] + names + self.primary[self.name_end :])
        struct.pack_into("<i", primary, self.header_size_pos, len(primary))
        struct.pack_into("<i", primary, self.name_count_pos, len(self.names))
        if len(self.names) != self.original_name_count:
            for pos in self.generation_name_positions:
                struct.pack_into("<i", primary, pos, len(self.names))
        for pos in self.offset_fields:
            old = struct.unpack_from("<i", primary, pos)[0]
            if old >= self.name_end:
                struct.pack_into("<i", primary, pos, old + delta)
        sidecar = bytearray()
        old_end = 0
        for export, content in zip(self.exports, contents):
            relative = export.serial_offset - len(self.primary)
            sidecar.extend(self.sidecar[old_end:relative])
            struct.pack_into("<qq", primary, export.map_offset + delta + 28, len(content), len(primary) + len(sidecar))
            sidecar.extend(content)
            old_end = relative + export.serial_size
        sidecar.extend(self.sidecar[old_end:])
        if self.bulk_offset:
            struct.pack_into(
                "<q",
                primary,
                self.bulk_offset_pos,
                self.bulk_offset + len(primary) - len(self.primary) + len(sidecar) - len(self.sidecar),
            )
        return bytes(primary), bytes(sidecar)
