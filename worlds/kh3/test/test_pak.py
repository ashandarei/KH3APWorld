from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from .. import Pak
from ..client_support import seed_paks
from ..Pak import BLOCK_SIZE, FOOTER, PakError, PakReader, pack_directory


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "input with spaces"
    root.mkdir()
    for name, data in {"empty": b"", "nested/table.uasset": b"KH3" * BLOCK_SIZE,
                       "nested/table.uexp": bytes(range(256)) * 700,
                       "unicode/世界.txt": "Sora café".encode()}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


@pytest.fixture(scope="module")
def repak(tmp_path_factory):
    root = tmp_path_factory.mktemp("repak-reference")
    bundle = Path(__file__).parents[1] / "tools" / "repak" / "v0.2.3"
    if os.name == "nt":
        with zipfile.ZipFile(bundle / "repak_cli-x86_64-pc-windows-msvc.zip") as archive:
            # Extract only the known executable, not arbitrary archive paths.
            member = next(n for n in archive.namelist() if Path(n).name == "repak.exe")
            exe = root / "repak.exe"
            exe.write_bytes(archive.read(member))
    else:
        with tarfile.open(bundle / "repak_cli-x86_64-unknown-linux-gnu.tar.xz") as archive:
            member = next(m for m in archive.getmembers() if Path(m.name).name == "repak")
            exe = root / "repak"
            exe.write_bytes(archive.extractfile(member).read())
            exe.chmod(0o755)
    return exe


@pytest.mark.parametrize("compress", [True, False])
def test_python_roundtrip_and_deterministic_output(source, tmp_path, compress):
    first, second = tmp_path / "first.pak", tmp_path / "second.pak"
    pack_directory(source, first, compress=compress)
    pack_directory(source, second, compress=compress)
    assert first.read_bytes() == second.read_bytes()
    reader = PakReader(first)
    assert reader.mount_point == "../../../"
    reader.extract(tmp_path / "out")
    for path in source.rglob("*"):
        if path.is_file():
            name = path.relative_to(source).as_posix()
            assert reader.read(name) == path.read_bytes()
            assert (tmp_path / "out" / name).read_bytes() == path.read_bytes()


def test_bidirectional_native_repak_compatibility(source, tmp_path, repak):
    python_pak, native_pak = tmp_path / "python.pak", tmp_path / "native.pak"
    pack_directory(source, python_pak)
    subprocess.run([str(repak), "unpack", "-o", str(tmp_path / "native-out"), str(python_pak)],
                   check=True, capture_output=True)
    subprocess.run([str(repak), "pack", "--version", "V4", "--compression", "Zlib",
                    str(source), str(native_pak)], check=True, capture_output=True)
    reader = PakReader(native_pak)
    for path in source.rglob("*"):
        if path.is_file():
            name = path.relative_to(source).as_posix()
            assert (tmp_path / "native-out" / name).read_bytes() == path.read_bytes()
            assert reader.read(name) == path.read_bytes()


def test_filtered_extraction_and_overwrite_rules(source, tmp_path):
    pak = tmp_path / "test.pak"
    pack_directory(source, pak)
    reader = PakReader(pak)
    out = tmp_path / "out"
    reader.extract(out, includes=["nested/table.uasset"])
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["nested/table.uasset"]
    with pytest.raises(PakError, match="overwrite"):
        reader.extract(out)
    reader.extract(out, includes=["nested"], overwrite=True)
    assert (out / "nested/table.uexp").is_file()


def test_rejects_corrupt_index_data_and_limits(source, tmp_path):
    path = tmp_path / "test.pak"
    pack_directory(source, path)
    original = path.read_bytes()
    damaged = bytearray(original)
    damaged[-FOOTER.size - 1] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(PakError, match="index SHA-1"):
        PakReader(path)
    path.write_bytes(original)
    reader = PakReader(path)
    entry = reader.entries["nested/table.uasset"]
    damaged = bytearray(original)
    damaged[entry.offset + entry.header_size] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(PakError, match="data SHA-1"):
        reader.read("nested/table.uasset")
    with pytest.raises(PakError, match="size limit"):
        reader.read("nested/table.uasset", max_size=10)


def _replace_index(path, change):
    data = bytearray(path.read_bytes())
    encrypted, magic, version, offset, size, _ = FOOTER.unpack(data[-FOOTER.size:])
    index = change(bytes(data[offset:offset + size]))
    path.write_bytes(data[:offset] + index + FOOTER.pack(encrypted, magic, version, offset,
                                                      len(index), hashlib.sha1(index).digest()))


def test_rejects_path_traversal_even_with_valid_hash(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe").write_bytes(b"content")
    path = tmp_path / "test.pak"
    pack_directory(source, path)
    _replace_index(path, lambda index: index.replace(b"safe\0", b"../x\0"))
    with pytest.raises(PakError, match="Unsafe pak path"):
        PakReader(path)


def test_rejects_unsupported_version_and_encryption(source, tmp_path):
    path = tmp_path / "test.pak"
    pack_directory(source, path)
    original = path.read_bytes()
    for position, value, message in [(-FOOTER.size, 1, "Encrypted"),
                                      (-FOOTER.size + 5, 5, "Unsupported")]:
        data = bytearray(original)
        data[position] = value
        path.write_bytes(data)
        with pytest.raises(PakError, match=message):
            PakReader(path)


def test_failed_pack_preserves_existing_output(source, tmp_path):
    output = source / "bad.pak"
    output.write_bytes(b"keep")
    with pytest.raises(PakError, match="outside"):
        pack_directory(source, output)
    assert output.read_bytes() == b"keep"


def test_interrupted_pack_preserves_destination_and_removes_temporary_file(source, tmp_path, monkeypatch):
    output = tmp_path / "existing.pak"
    output.write_bytes(b"previous pak")
    def fail(*args):
        raise OSError("simulated compression failure")
    monkeypatch.setattr(Pak.zlib, "compress", fail)
    with pytest.raises(OSError, match="simulated"):
        pack_directory(source, output)
    assert output.read_bytes() == b"previous pak"
    assert not list(tmp_path.glob(".existing.pak.*"))


def test_modules_run_from_zip_without_a_python_subprocess(source, tmp_path, monkeypatch):
    # Exercise zipimport as used by APWorlds without building a release artifact.
    package_name = "kh3_pak_zip_test"
    archive_path = tmp_path / "modules.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(f"{package_name}/__init__.py", "")
        for name in ("Pak.py",):
            archive.write(Path(Pak.__file__).with_name(name), f"{package_name}/{name}")
    monkeypatch.syspath_prepend(str(archive_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        module = importlib.import_module(package_name + ".Pak")
        output = tmp_path / "zip.pak"
        module.pack_directory(source, output)
        assert module.PakReader(output).read("nested/table.uasset") == (source / "nested/table.uasset").read_bytes()
    finally:
        for name in list(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]


@pytest.mark.parametrize("padding_size", [0, 5 * 1024 * 1024])
def test_metadata_read_uses_python_without_subprocess(tmp_path, monkeypatch, padding_size):
    monkeypatch.setenv("KH3_REPAK_PATH", "obsolete-repak")
    monkeypatch.setenv("KH3_UNREALPAK_PATH", "obsolete-UnrealPak")
    monkeypatch.setenv("KH3_USE_WINE_UNREALPAK", "1")
    source = tmp_path / "source"
    metadata_path = source / seed_paks.KH3_AP_SEED_METADATA_PAK_PATH
    metadata_path.parent.mkdir(parents=True)
    # Use the actual metadata contract, also exercised by the client suite.
    metadata = {"format": "kh3_ap_seed_metadata", "seed": "AP_12345-1-Tester",
                "slot": 1, "slot_name": "Tester"}
    if padding_size:
        metadata["padding"] = "x" * padding_size
    metadata_path.write_text(json.dumps(metadata))
    output = tmp_path / "pakchunk9-Tester-randomizer-AP_12345-1_99_P.pak"
    pack_directory(source, output)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("native tool invoked"))
    assert seed_paks.extract_kh3_ap_seed_metadata_from_pak(output) == metadata


@pytest.mark.parametrize("payload", [
    b"not-json", b"{}", b"[]", b"\xff",
    json.dumps({"format": "kh3_ap_seed_metadata", "seed": "AP_99999", "slot": 1, "slot_name": "Tester"}).encode(),
])
def test_python_metadata_reader_rejects_invalid_or_mismatched_identity(tmp_path, payload):
    source = tmp_path / "source"
    metadata_path = source / seed_paks.KH3_AP_SEED_METADATA_PAK_PATH
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_bytes(payload)
    output = tmp_path / "pakchunk9-Tester-randomizer-AP_12345-1_99_P.pak"
    pack_directory(source, output)
    assert seed_paks.extract_kh3_ap_seed_metadata_from_pak(output) is None
