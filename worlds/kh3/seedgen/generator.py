# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""In-process Python KH3 seed generation; no SDK, CLI process or tool downloads."""

from __future__ import annotations

import copy
import hashlib
import importlib.resources
import json
import re
import shutil
import tempfile
from pathlib import Path

from ..Pak import PakReader, pack_directory
from ..RuntimeSeed import encode_document, metadata_payload, runtime_documents
from .localization import make_hints
from .presentation import build_presentation
from .remaps import add_runtime_settings
from .tables import Tables, randomize_options

METADATA_PATH = "KINGDOM HEARTS III/Content/Archipelago/kh3_ap_seed_metadata.json"
FINGERPRINT_PATH = "KINGDOM HEARTS III/Content/Archipelago/kh3_randomizer_backend.json"


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def runtime_reward_bundles(items: dict, defaults: dict, slot_data: dict) -> list[dict]:
    """Exact table rewards for native repair and VBonus presentation."""
    def reward_tokens(fields):
        tokens = [token.rstrip("\0") for token in fields.values()]
        # AP notification markers represent a check, never a second native grant.
        return [token for token in tokens if token not in {"AP_ITEM_000", "ARCHIPELAGO_ITEM"}
                and not token.startswith("ITEM_AP_I")]
    bundles = []
    for row in slot_data.get("bridge", {}).get("locations", []):
        table, source = row.get("source_table"), row.get("source_id")
        if table not in {"VBonus", "Event"} or source not in items.get(table, {}):
            continue
        bundles.append(dict(
            kind="vbonus" if table == "VBonus" else "event", source_id=source,
            bridge_key=row["bridge_key"], location_name=row.get("ap_location_name", ""),
            world=row.get("world", ""), grant_context="fight_vbonus" if table == "VBonus" else "scripted_event",
            randomized_tokens=reward_tokens(items[table][source]),
            vanilla_tokens=reward_tokens(defaults.get(table, {}).get(source, {})),
        ))
    return bundles


def backend_fingerprint(root):
    source_hash = hashlib.sha256()
    resources = importlib.resources.files(__package__)
    for name in (
        "__init__.py",
        "__main__.py",
        "assets.py",
        "rng.py",
        "tables.py",
        "remaps.py",
        "localization.py",
        "presentation.py",
        "generator.py",
        "coliseum.py",
    ):
        source_hash.update(name.encode("ascii") + b"\0" + resources.joinpath(name).read_bytes() + b"\0")
    world_resources = importlib.resources.files(__package__.rsplit(".", 1)[0])
    for name in ("RuntimeSeed.py", "RaceMode.py", "Pak.py", "data/runtime_json_limits.json"):
        source_hash.update(name.encode("ascii") + b"\0" + world_resources.joinpath(name).read_bytes() + b"\0")
    return dict(
        format="kh3_randomizer_backend",
        format_version=2,
        implementation="python",
        source_sha256=source_hash.hexdigest(),
        enemy_compatibility_sha256=hashlib.sha256(
            (root / "wwwroot/DefaultEnemyCompatibility.json").read_bytes()
        ).hexdigest(),
    )


def pak_name(seed):
    match = re.fullmatch(r"(AP_\d+)-(\d+)-(.+)", seed) or re.fullmatch(r"(AP_\d+)_P(\d+)_(.+)", seed)

    def clean(value):
        return re.sub("[^A-Za-z0-9._-]+", "_", value).strip("._") or "Player"

    if match:
        return f"pakchunk9-{clean(match[3])}-randomizer-{match[1]}-{match[2]}_99_P.pak"
    return f"pakchunk99-randomizer-{clean(seed)}_99_P.pak"


def generate_seed(root: Path, output: Path, seed: str, config: dict, items: dict, metadata: dict):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not seed or not isinstance(items, dict) or not isinstance(metadata, dict):
        raise ValueError("KH3 generation requires a seed, exact rewards and AP metadata")
    metadata = copy.deepcopy(metadata)
    items = copy.deepcopy(items)
    race_mode = bool(metadata.get("slot_data", {}).get("race_mode"))
    if race_mode:
        from ..RaceMode import race_slot_data, validate_race_export
        validate_race_export(metadata)
        metadata["slot_data"] = race_slot_data(metadata["slot_data"])
    if metadata.get("seed") != seed or int(metadata["slot"]) <= 0 or not isinstance(metadata.get("slot_data"), dict):
        raise ValueError("AP metadata seed/player does not match the requested generation")
    # The export may contain the public generic placeholder; the native bridge uses this token.
    for rows in items.values():
        for fields in rows.values():
            for key, token in fields.items():
                if isinstance(token, str) and token.rstrip("\0") == "ARCHIPELAGO_ITEM":
                    fields[key] = "AP_ITEM_000"
    defaults = json.loads((root / "wwwroot/DefaultKH3.json").read_text(encoding="utf-8-sig"))
    metadata["slot_data"]["runtime_reward_bundles"] = runtime_reward_bundles(items, defaults, metadata["slot_data"])
    randomize_options(seed, items, config, defaults)
    add_runtime_settings(root, seed, config, metadata)
    hints = make_hints(seed, config, metadata)
    tables = Tables(root)
    qol = tables.settings(config, seed, hints)
    tables.rewards(items, metadata["slot_data"], qol.get("ITEM_004", False))
    build_presentation(tables, items, metadata, hints)
    fingerprint = backend_fingerprint(root)
    metadata["randomizer_backend"] = fingerprint
    full_metadata = json_bytes(metadata)
    metadata = metadata_payload(metadata)
    documents = runtime_documents(metadata)
    if len(encode_document("runtime_seed_settings.json", documents["runtime_seed_settings.json"])) > 1024 * 1024:
        raise ValueError("Native KH3 seed settings exceed the 1 MiB pak record limit")
    files = tables.encode()
    files[METADATA_PATH] = encode_document("kh3_ap_seed_metadata.json", metadata)
    for filename, document in documents.items():
        files["KINGDOM HEARTS III/Content/Archipelago/" + filename] = encode_document(filename, document)
    files[FINGERPRINT_PATH] = json_bytes(fingerprint)
    spoiler = dict(
        SeedName=seed,
        AvailablePools=config.get("pools", {}),
        Exceptions=config.get("exceptions", {}),
        Limiters=config.get("limiters", {}),
        Modifications=[],
        RandomizerBackend=fingerprint,
    )
    if not race_mode:
        files["SpoilerLog.json"] = json_bytes(spoiler)
    output.mkdir(parents=True, exist_ok=True)
    staging_root = root / "wwwroot/pak/Seeds"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    pak_path = output / pak_name(seed)
    # Build from an isolated directory and atomically replace the pak only after all outputs validate.
    with tempfile.TemporaryDirectory(prefix="kh3-python-", dir=staging_root) as temporary:
        work = Path(temporary)
        payload = work / "payload"
        payload.mkdir()
        for name, data in files.items():
            destination = payload / name
            if not destination.resolve().is_relative_to(payload.resolve()):
                raise ValueError(f"Seed payload path escapes staging: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        packed = work / pak_path.name
        pack_directory(payload, packed)
        if packed.stat().st_size < 5 * 1024:
            raise ValueError("Generated KH3 pak is incomplete")
        archive = PakReader(packed)
        if set(archive.entries) != set(files):
            raise ValueError("Generated KH3 pak inventory differs from staged payload")
        for name, expected in files.items():
            if archive.read(name) != expected:
                raise ValueError(f"Generated KH3 pak readback failed: {name}")
        if staging.exists():
            if staging.resolve().parent != staging_root.resolve() or staging.is_symlink():
                raise ValueError("Unsafe KH3 staging directory")
            shutil.rmtree(staging)
        payload.rename(staging)
        # Pak.pack_directory writes atomically beside the destination, including across drives.
        pack_directory(staging, pak_path)
    spoiler_path = None
    if race_mode:
        from ..RaceMode import remove_race_spoilers
        remove_race_spoilers(output, seed)
    else:
        spoiler_path = output / f"SpoilerLog-{seed}.json"
        spoiler_path.write_bytes(files["SpoilerLog.json"])
        (output / f"{seed}.generated-metadata.json").write_bytes(full_metadata)
    return pak_path, spoiler_path
