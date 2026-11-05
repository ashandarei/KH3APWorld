from __future__ import annotations

import hashlib
import json
import re
import shutil
import ssl
import subprocess
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import Utils

from .platform_support.download_tls import create_download_ssl_context

ROOT = Path(Utils.local_path()).resolve()
KH3_LOOSE_WORLD_ROOT = ROOT / "worlds" / "kh3"
KH3_RANDOMIZER_ROOT = KH3_LOOSE_WORLD_ROOT / "randomizer"
KH3_EXTRACTED_DATA = KH3_LOOSE_WORLD_ROOT / "data" / "extracted_locations.json"
KH3_CONTAINER_PAYLOAD = "kh3_seed.json"
KH3_TOOL_DOWNLOAD_USER_AGENT = "Archipelago-KH3-Client/tool-bootstrap"
KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES = 5 * 1024
KH3_PAK_STAGING_HASH_LENGTH = 12
KH3_RANDOMIZER_BACKEND_FINGERPRINT_RELATIVE_PATH = (
    "KINGDOM HEARTS III/Content/Archipelago/kh3_randomizer_backend.json"
)
KH3_AP_SEED_METADATA_RELATIVE_PATH = "KINGDOM HEARTS III/Content/Archipelago/kh3_ap_seed_metadata.json"
KH3_REQUIRED_SEED_STAGING_FILES = (
    "KINGDOM HEARTS III/Content/Blueprints/GoA/Data/InitialWorldFlags_DT.uasset",
    "KINGDOM HEARTS III/Content/Blueprints/GoA/Data/InitialWorldFlags_DT.uexp",
    KH3_RANDOMIZER_BACKEND_FINGERPRINT_RELATIVE_PATH,
    KH3_AP_SEED_METADATA_RELATIVE_PATH,
)


class KH3PakGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class KH3PakResult:
    slot: int
    slot_name: str
    pak_path: Path
    spoiler_log_path: Path | None


def load_multidata_from_ap_zip(ap_zip_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(ap_zip_path) as archive:
            archipelago_name = next(
                (name for name in archive.namelist() if name.endswith(".archipelago")),
                None,
            )
            if archipelago_name is None:
                raise KH3PakGenerationError(f"No .archipelago payload found in {ap_zip_path}.")
            payload = archive.read(archipelago_name)
    except zipfile.BadZipFile as exc:
        raise KH3PakGenerationError(f"Generated AP zip is malformed: {ap_zip_path}") from exc

    if len(payload) < 2:
        raise KH3PakGenerationError(f"Generated AP payload is empty or truncated: {ap_zip_path}")

    try:
        decoded = Utils.restricted_loads(zlib.decompress(payload[1:]))
    except zlib.error as exc:
        raise KH3PakGenerationError(f"Generated AP payload could not be decompressed: {ap_zip_path}") from exc

    if not isinstance(decoded, dict):
        raise KH3PakGenerationError("Generated AP payload did not decode to a mapping.")
    return decoded


def iter_kh3_slots(multidata: dict[str, Any], requested_slot: int | None = None) -> list[dict[str, Any]]:
    from .RaceMode import race_slot_data
    from .SlotData import decode_slot_data
    slot_info = multidata.get("slot_info")
    slot_data = multidata.get("slot_data")
    if not isinstance(slot_info, dict) or not isinstance(slot_data, dict):
        raise KH3PakGenerationError("Generated AP payload is missing slot_info or slot_data.")

    kh3_slots: list[dict[str, Any]] = []
    for raw_slot, info in sorted(slot_info.items(), key=lambda entry: int(entry[0])):
        slot_id = int(raw_slot)
        if requested_slot is not None and slot_id != requested_slot:
            continue
        game = getattr(info, "game", None)
        if game != "Kingdom Hearts III":
            continue

        data = slot_data.get(slot_id) or slot_data.get(raw_slot)
        if not isinstance(data, dict):
            continue

        randomizer_config = data.get("kh3_randomizer")
        if not isinstance(randomizer_config, dict):
            continue

        runtime_data = decode_slot_data(data)
        if multidata.get("race_mode"):
            runtime_data["race_mode"] = True
        runtime_data = race_slot_data(runtime_data)
        kh3_slots.append(
            {
                "slot": slot_id,
                "name": str(getattr(info, "name", f"Player {slot_id}")),
                "slot_data": runtime_data,
                "randomizer_config": runtime_data["kh3_randomizer"],
            }
        )

    if requested_slot is not None and not kh3_slots:
        raise KH3PakGenerationError(f"Slot {requested_slot} is not a KH3 slot in this AP zip.")
    if not kh3_slots:
        raise KH3PakGenerationError("This AP zip does not contain a Kingdom Hearts III slot.")
    return kh3_slots


def safe_file_fragment(value: str, fallback: str = "kh3") -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._") or fallback


def kh3_ap_pak_glob_for_seed(seed: str) -> str:
    match = re.fullmatch(r"(AP_\d+)-(\d+)-(.+)", seed)
    if not match:
        match = re.fullmatch(r"(AP_\d+)_P(\d+)_(.+)", seed)
    if match:
        return f"pakchunk9-*-randomizer-{match.group(1)}-{match.group(2)}_99_P.pak"
    return f"pakchunk99-randomizer-{seed}*_99_P.pak"


def _host_tool_env() -> dict[str, str]:
    """Return an environment safe for host tools started by a packaged client."""

    # The AppImage appends private libraries to LD_LIBRARY_PATH and Steam
    # injects its game overlay through LD_PRELOAD. Host download tools
    # must not inherit either loader override.
    env = dict(Utils.env_cleared_lib_path())
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


def executable_on_path(executable: str) -> str | None:
    return shutil.which(executable)


def _tool_download_ssl_context() -> ssl.SSLContext:
    return create_download_ssl_context()


def _tool_download_uses_python_https() -> bool:
    # The Linux AppImage's bundled OpenSSL can SIGBUS while creating a new SSL
    # context from the KH3 generation worker thread. Host curl/wget run TLS in
    # a separate process and avoid taking down the client.
    return not (Utils.is_linux and Utils.is_frozen())


def _tool_download_env() -> dict[str, str]:
    """Use host trust settings without injecting an application CA bundle."""
    return _host_tool_env()


def _completed_process_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        output.strip()
        for output in (result.stdout or "", result.stderr or "")
        if output.strip()
    )


def _download_file(url: str, destination: Path) -> None:
    """Download a KH3 resource using system trust, then host curl/wget as fallbacks."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": KH3_TOOL_DOWNLOAD_USER_AGENT})
    failures: list[str] = []

    if _tool_download_uses_python_https():
        try:
            with urllib.request.urlopen(
                request,
                timeout=60,
                context=_tool_download_ssl_context(),
            ) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except OSError as exc:
            failures.append(f"bundled Python HTTPS: {exc}")
    else:
        failures.append("bundled Python HTTPS: skipped in the frozen Linux client")

    downloader_commands = (
        (
            "curl",
            lambda executable: [
                executable,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--output",
                str(destination),
                url,
            ],
        ),
        (
            "wget",
            lambda executable: [
                executable,
                "--quiet",
                "--output-document",
                str(destination),
                url,
            ],
        ),
    )
    for downloader_name, build_command in downloader_commands:
        executable = executable_on_path(downloader_name)
        if not executable:
            failures.append(f"{downloader_name}: not found on PATH")
            continue
        try:
            result = subprocess.run(
                build_command(executable),
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=_tool_download_env(),
            )
        except OSError as exc:
            failures.append(f"{downloader_name}: {exc}")
            continue
        if result.returncode == 0 and destination.is_file():
            return
        details = _completed_process_output(result)
        failure = f"{downloader_name}: exit code {result.returncode}"
        if details:
            failure = f"{failure}: {details}"
        failures.append(failure)

    raise OSError("; ".join(failures))


def ensure_kh3_generation_tools_available() -> dict[str, Any]:
    """Generation uses the client's bundled Python runtime and standard library."""
    return {"randomizer": {"implementation": "python", "bundled": True},
            "pak": {"implementation": "python", "bundled": True}}


def generate_python_seed(randomizer_root: Path, output: Path, seed: str,
                         config_path: Path, options_path: Path, metadata_path: Path) -> None:
    from .seedgen.generator import generate_seed

    try:
        generate_seed(randomizer_root, output, seed,
                      json.loads(config_path.read_text(encoding="utf-8")),
                      json.loads(options_path.read_text(encoding="utf-8")),
                      json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise KH3PakGenerationError(f"KH3 Python seed generation failed: {exc}") from exc


def get_apworld_archive_context() -> tuple[Path, str] | None:
    raw_file = str(Path(__file__))
    marker = ".apworld"
    marker_index = raw_file.lower().find(marker)
    if marker_index < 0:
        return None

    archive_path = Path(raw_file[:marker_index + len(marker)])
    inner_path = raw_file[marker_index + len(marker):].lstrip("\\/").replace("\\", "/")
    package_root = inner_path.split("/", 1)[0]
    if not archive_path.is_file() or not package_root:
        return None
    return archive_path, package_root


def copy_apworld_tree(relative_path: str, destination: Path) -> None:
    if destination.exists():
        if destination.is_symlink() or destination.resolve().parent != destination.parent.resolve():
            raise KH3PakGenerationError(f"Unsafe KH3 resource destination: {destination}")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    context = get_apworld_archive_context()
    if context is None:
        raise KH3PakGenerationError("KH3 APWorld archive path could not be resolved for bundled resource extraction.")

    archive_path, package_root = context
    archive_prefix = f"{package_root}/{relative_path.strip('/')}/"
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith(archive_prefix):
                continue
            relative_member = member.filename[len(archive_prefix):]
            if not relative_member:
                continue
            target = destination / relative_member
            if not target.resolve().is_relative_to(destination.resolve()):
                raise KH3PakGenerationError(f"KH3 bundled resource escapes its destination: {relative_member}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def copy_apworld_file(relative_path: str, destination: Path) -> Path:
    context = get_apworld_archive_context()
    if context is None:
        raise KH3PakGenerationError("KH3 APWorld archive path could not be resolved for bundled resource extraction.")

    archive_path, package_root = context
    archive_name = f"{package_root}/{relative_path.strip('/')}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        destination.write_bytes(archive.read(archive_name))
    return destination


def prepare_randomizer_root(work_dir: Path) -> Path:
    destination = work_dir / "bundled-randomizer"
    if get_apworld_archive_context() is not None:
        copy_apworld_tree("randomizer", destination)
        return destination

    if (KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.json").is_file():
        # Keep extracted templates and generated staging out of the source checkout.
        if destination.exists():
            if destination.is_symlink() or destination.resolve().parent != work_dir.resolve():
                raise KH3PakGenerationError(f"Unsafe KH3 template destination: {destination}")
            shutil.rmtree(destination)
        # Match distribution selection, including negated exceptions such as the
        # Avalanche map. Generated output and retired templates are not inputs.
        from pathspec import GitIgnoreSpec
        patterns = []
        for ignore_path in (ROOT / "data" / "GLOBAL.apignore", KH3_RANDOMIZER_ROOT.parent / ".apignore"):
            if ignore_path.is_file():
                patterns.extend(ignore_path.read_text(encoding="utf-8").splitlines())
        selection = GitIgnoreSpec.from_lines(patterns)

        def ignored(directory: str, names: list[str]) -> list[str]:
            return [name for name in names if name in {"bin", "obj", "Seeds"}
                    or selection.match_file("randomizer/" +
                        (Path(directory) / name).relative_to(KH3_RANDOMIZER_ROOT).as_posix())]

        shutil.copytree(
            KH3_RANDOMIZER_ROOT,
            destination,
            copy_function=shutil.copyfile,
            ignore=ignored,
        )
        return destination

    copy_apworld_tree("randomizer", destination)
    return destination


def prepare_extracted_data(work_dir: Path) -> Path:
    if get_apworld_archive_context() is not None:
        return copy_apworld_file(
            "data/extracted_locations.json",
            work_dir / "bundled-data" / "extracted_locations.json",
        )

    if KH3_EXTRACTED_DATA.is_file():
        return KH3_EXTRACTED_DATA
    return copy_apworld_file("data/extracted_locations.json", work_dir / "bundled-data" / "extracted_locations.json")


def build_olympus_coliseum_compat_pak(
    source_pak_path: Path | str,
    output_pak_path: Path | str,
    *,
    suppress_cup_rewards: bool = True,
) -> Path:
    """Build the verified chest/cup Coliseum companion without changing the full mod."""

    source_pak_path = Path(source_pak_path).expanduser().resolve()
    output_pak_path = Path(output_pak_path).expanduser().resolve()
    if not source_pak_path.is_file():
        raise KH3PakGenerationError(f"Olympus Coliseum source pak not found: {source_pak_path}")
    if output_pak_path.exists():
        raise KH3PakGenerationError(
            f"Refusing to overwrite an existing Olympus Coliseum compatibility pak: {output_pak_path}"
        )

    from .seedgen.coliseum import build_compatibility

    try:
        build_compatibility(source_pak_path, output_pak_path, suppress_cup_rewards=suppress_cup_rewards)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        raise KH3PakGenerationError(f"Olympus Coliseum AP compatibility generation failed: {exc}") from exc

    try:
        output_size = output_pak_path.stat().st_size
    except OSError as exc:
        raise KH3PakGenerationError(
            f"The Olympus Coliseum compatibility build did not create {output_pak_path}."
        ) from exc
    if output_size < 1024:
        raise KH3PakGenerationError(
            "The Olympus Coliseum compatibility build produced an incomplete pak "
            f"({output_size} bytes): {output_pak_path}"
        )
    return output_pak_path


def remove_existing_pak_candidates(work_dir: Path, seed: str) -> None:
    """Prevent a stale output from being mistaken for the result of this invocation."""
    for candidate in work_dir.glob(kh3_ap_pak_glob_for_seed(seed)):
        try:
            candidate.unlink()
        except OSError as exc:
            raise KH3PakGenerationError(f"Could not remove stale KH3 pak output: {candidate}") from exc


def kh3_pak_staging_directory_name(seed: str) -> str:
    """Return the short directory name used by the Python pak staging implementation."""

    return hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest()[:KH3_PAK_STAGING_HASH_LENGTH]


def select_generated_pak(work_dir: Path, seed: str, slot_name: str) -> Path:
    pak_candidates = sorted(
        work_dir.glob(kh3_ap_pak_glob_for_seed(seed)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not pak_candidates:
        raise KH3PakGenerationError(f"KH3 randomizer did not produce a pak for slot '{slot_name}'.")

    pak_path = pak_candidates[0]
    try:
        pak_size = pak_path.stat().st_size
    except OSError as exc:
        raise KH3PakGenerationError(f"Could not inspect generated KH3 pak: {pak_path}") from exc
    if pak_size < KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES:
        raise KH3PakGenerationError(
            f"KH3 randomizer produced an incomplete pak for slot '{slot_name}' "
            f"({pak_size} bytes; minimum {KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES} bytes). "
            "Please regenerate the seed file."
        )
    return pak_path


def validate_generated_seed_staging(randomizer_root: Path, seed: str, slot_name: str) -> None:
    staging_directory = (
        randomizer_root
        / "wwwroot"
        / "pak"
        / "Seeds"
        / kh3_pak_staging_directory_name(seed)
    )
    missing_files = [
        relative_path
        for relative_path in KH3_REQUIRED_SEED_STAGING_FILES
        if not (staging_directory / relative_path).is_file()
    ]
    if missing_files:
        missing_display = ", ".join(missing_files)
        raise KH3PakGenerationError(
            f"KH3 randomizer produced an incomplete seed for slot '{slot_name}'; required pak staging "
            f"artifacts are missing: {missing_display}. The configured randomizer backend may be stale."
        )

    fingerprint_path = staging_directory / KH3_RANDOMIZER_BACKEND_FINGERPRINT_RELATIVE_PATH
    metadata_path = staging_directory / KH3_AP_SEED_METADATA_RELATIVE_PATH
    try:
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KH3PakGenerationError(
            f"KH3 randomizer produced malformed backend provenance for slot '{slot_name}'."
        ) from exc

    if not isinstance(fingerprint, dict) or fingerprint.get("format") != "kh3_randomizer_backend":
        raise KH3PakGenerationError(
            f"KH3 randomizer produced an invalid backend fingerprint for slot '{slot_name}'."
        )
    source_sha256 = fingerprint.get("source_sha256")
    if (fingerprint.get("implementation") != "python" or fingerprint.get("format_version") != 2
            or not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)):
        raise KH3PakGenerationError(
            f"KH3 randomizer backend fingerprint has no usable Python source identity for slot '{slot_name}'."
        )
    if not isinstance(metadata, dict) or metadata.get("randomizer_backend") != fingerprint:
        raise KH3PakGenerationError(
            f"KH3 randomizer backend fingerprint does not match the AP seed metadata for slot '{slot_name}'."
        )


def export_exact_randomized_options(
    ap_zip_path: Path,
    output_dir: Path,
    slot_id: int,
    seed: str,
    randomizer_root: Path,
    extracted_data_path: Path,
) -> Path:
    from .export_ap_kh3_randomized_options import export_randomized_options

    randomized_options_path = output_dir / f"{seed}.randomized-options.json"
    foreign_item_manifest_path = output_dir / f"{seed}.foreign-items.json"
    placement_summary_path = output_dir / f"{seed}.placement-summary.json"
    result = export_randomized_options(SimpleNamespace(
        ap_zip=ap_zip_path,
        default_kh3=randomizer_root / "wwwroot" / "DefaultKH3.json",
        default_kh3_overrides=randomizer_root / "wwwroot" / "DefaultKH3.ArchipelagoOverrides.json",
        extracted_data=extracted_data_path,
        runtime_root=ROOT,
        slot=slot_id,
        chrinit_sora_have_ability=[],
        event_reward_override=[],
    ))
    randomized_options_path.write_text(json.dumps(result.randomized_options, indent=2), encoding="utf-8")
    foreign_item_manifest_path.write_text(json.dumps(result.foreign_item_manifest, indent=2), encoding="utf-8")
    placement_summary_path.write_text(json.dumps(result.placement_summary, indent=2), encoding="utf-8")
    return randomized_options_path


def write_archipelago_seed_metadata(
    kh3_output_dir: Path,
    slot: dict[str, Any],
    seed: str,
) -> Path:
    from .RaceMode import race_slot_data
    foreign_manifest_path = kh3_output_dir / f"{seed}.foreign-items.json"
    placement_summary_path = kh3_output_dir / f"{seed}.placement-summary.json"
    metadata_path = kh3_output_dir / f"{seed}.archipelago-seed-metadata.json"
    metadata = {
        "format": "kh3_ap_seed_metadata",
        "format_version": 1,
        "seed_creation_version": slot["slot_data"].get("kh3_seed", {}).get("seed_creation_version"),
        "seed": seed,
        "slot": slot["slot"],
        "slot_name": slot["name"],
        "slot_data": race_slot_data(slot["slot_data"]),
        "foreign_item_manifest": json.loads(foreign_manifest_path.read_text(encoding="utf-8")),
        "placement_summary": json.loads(placement_summary_path.read_text(encoding="utf-8")),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return metadata_path


def load_kh3_container_data(container_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(container_path) as archive:
            manifest = json.loads(archive.read("archipelago.json").decode("utf-8"))
            payload = json.loads(archive.read(KH3_CONTAINER_PAYLOAD).decode("utf-8"))
    except KeyError as exc:
        raise KH3PakGenerationError(f"KH3 container is missing required entry: {exc}") from exc
    except zipfile.BadZipFile as exc:
        raise KH3PakGenerationError(f"KH3 container is malformed: {container_path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KH3PakGenerationError(f"KH3 container contains invalid JSON: {container_path}") from exc

    if not isinstance(manifest, dict):
        raise KH3PakGenerationError("KH3 container manifest is not a JSON object.")
    if manifest.get("game") != "Kingdom Hearts III":
        raise KH3PakGenerationError(f"Container is for {manifest.get('game')!r}, not Kingdom Hearts III.")
    if not isinstance(payload, dict) or payload.get("format") != "kh3_ap_seed":
        raise KH3PakGenerationError("KH3 container payload is missing or has an unknown format.")
    from .Bridge import BridgeProtocolError
    from .SlotData import decode_slot_data
    try:
        payload["slot_data"] = decode_slot_data(payload.get("slot_data"))
    except BridgeProtocolError as exc:
        raise KH3PakGenerationError(str(exc)) from exc
    if payload["slot_data"].get("race_mode"):
        from .RaceMode import race_slot_data
        payload["slot_data"] = race_slot_data(payload["slot_data"])
        payload["randomizer_config"] = payload["slot_data"]["kh3_randomizer"]
        payload["remote_hint_placements"] = []
    return payload


def export_exact_randomized_options_from_container(
    container_data: dict[str, Any],
    output_dir: Path,
    seed: str,
    randomizer_root: Path,
    extracted_data_path: Path,
) -> Path:
    from .export_ap_kh3_randomized_options import export_randomized_options_from_bridge_data

    slot_data = container_data.get("slot_data")
    if not isinstance(slot_data, dict):
        raise KH3PakGenerationError("KH3 container is missing slot_data.")
    bridge = slot_data.get("bridge")
    if not isinstance(bridge, dict) or not isinstance(bridge.get("locations"), list):
        raise KH3PakGenerationError("KH3 container slot_data is missing bridge locations.")

    randomized_options_path = output_dir / f"{seed}.randomized-options.json"
    foreign_item_manifest_path = output_dir / f"{seed}.foreign-items.json"
    placement_summary_path = output_dir / f"{seed}.placement-summary.json"
    result = export_randomized_options_from_bridge_data(
        default_kh3=randomizer_root / "wwwroot" / "DefaultKH3.json",
        default_kh3_overrides=randomizer_root / "wwwroot" / "DefaultKH3.ArchipelagoOverrides.json",
        extracted_data_path=extracted_data_path,
        bridge_locations=bridge["locations"],
        slot=int(container_data["slot"]),
        slot_name=str(container_data["slot_name"]),
        archive_name=str(container_data.get("archive_name") or ""),
        randomizer_config=container_data.get("randomizer_config"),
        chrinit_sora_have_ability=[],
        event_reward_override=[],
        remote_hint_placements=container_data.get("remote_hint_placements"),
        race_mode=bool(slot_data.get("race_mode")),
    )
    randomized_options_path.write_text(json.dumps(result.randomized_options, indent=2), encoding="utf-8")
    foreign_item_manifest_path.write_text(json.dumps(result.foreign_item_manifest, indent=2), encoding="utf-8")
    placement_summary_path.write_text(json.dumps(result.placement_summary, indent=2), encoding="utf-8")
    return randomized_options_path


def write_container_seed_metadata(
    kh3_output_dir: Path,
    container_data: dict[str, Any],
    seed: str,
) -> Path:
    from .RaceMode import race_slot_data
    foreign_manifest_path = kh3_output_dir / f"{seed}.foreign-items.json"
    placement_summary_path = kh3_output_dir / f"{seed}.placement-summary.json"
    metadata_path = kh3_output_dir / f"{seed}.archipelago-seed-metadata.json"
    metadata = {
        "format": "kh3_ap_seed_metadata",
        "format_version": 1,
        "seed_creation_version": container_data["slot_data"].get("kh3_seed", {}).get("seed_creation_version"),
        "seed": seed,
        "slot": int(container_data["slot"]),
        "slot_name": str(container_data["slot_name"]),
        "slot_data": race_slot_data(container_data["slot_data"]),
        "foreign_item_manifest": json.loads(foreign_manifest_path.read_text(encoding="utf-8")),
        "placement_summary": json.loads(placement_summary_path.read_text(encoding="utf-8")),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return metadata_path


def generate_kh3_pak_from_container(
    container_path: Path | str,
    output_dir: Path | str | None = None,
) -> KH3PakResult:
    container_path = Path(container_path).expanduser().resolve()
    if not container_path.is_file():
        raise KH3PakGenerationError(f"KH3 container not found: {container_path}")

    # Reject malformed inputs before downloading tools, restoring dependencies,
    # or replacing the previous generation's working assets.
    container_data = load_kh3_container_data(container_path)
    try:
        slot_id = int(container_data["slot"])
        slot_name = str(container_data["slot_name"])
        config_payload = dict(container_data["randomizer_config"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KH3PakGenerationError("KH3 container is missing valid player or randomizer settings.") from exc
    seed = safe_file_fragment(str(container_data.get("pak_seed") or container_path.stem))

    output_dir = Path(output_dir).expanduser().resolve() if output_dir else container_path.parent / "kh3-player-paks"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ensure_kh3_generation_tools_available()
    randomizer_root = prepare_randomizer_root(work_dir)
    extracted_data_path = prepare_extracted_data(work_dir)

    config_path = work_dir / f"{seed}.json"
    config_payload["seed"] = seed
    config_path.write_text(json.dumps(config_payload, indent=2, sort_keys=True), encoding="utf-8")

    randomized_options_path = export_exact_randomized_options_from_container(
        container_data,
        work_dir,
        seed,
        randomizer_root,
        extracted_data_path,
    )
    metadata_path = write_container_seed_metadata(work_dir, container_data, seed)
    generate_python_seed(randomizer_root, work_dir, seed, config_path, randomized_options_path, metadata_path)
    validate_generated_seed_staging(randomizer_root, seed, slot_name)

    pak_path = select_generated_pak(work_dir, seed, slot_name)
    destination = output_dir / pak_path.name
    shutil.copy2(pak_path, destination)

    if container_data["slot_data"].get("race_mode"):
        from .RaceMode import remove_race_spoilers
        remove_race_spoilers(output_dir, seed)
    spoiler_candidates = [] if container_data["slot_data"].get("race_mode") else sorted(
        work_dir.glob(f"SpoilerLog-{seed}*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    spoiler_destination = None
    if spoiler_candidates:
        spoiler_destination = output_dir / spoiler_candidates[0].name
        shutil.copy2(spoiler_candidates[0], spoiler_destination)

    return KH3PakResult(
        slot=slot_id,
        slot_name=slot_name,
        pak_path=destination,
        spoiler_log_path=spoiler_destination,
    )


def generate_kh3_paks_from_ap_zip(
    ap_zip_path: Path | str,
    output_dir: Path | str | None = None,
    requested_slot: int | None = None,
) -> list[KH3PakResult]:
    ap_zip_path = Path(ap_zip_path).expanduser().resolve()
    if not ap_zip_path.is_file():
        raise KH3PakGenerationError(f"AP zip not found: {ap_zip_path}")

    multidata = load_multidata_from_ap_zip(ap_zip_path)
    kh3_slots = iter_kh3_slots(multidata, requested_slot)

    output_dir = Path(output_dir).expanduser().resolve() if output_dir else ap_zip_path.parent / "kh3-player-paks"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ensure_kh3_generation_tools_available()
    randomizer_root = prepare_randomizer_root(work_dir)
    extracted_data_path = prepare_extracted_data(work_dir)

    results: list[KH3PakResult] = []

    for slot in kh3_slots:
        slot_id = int(slot["slot"])
        slot_name = str(slot["name"])
        seed = safe_file_fragment(f"{ap_zip_path.stem}-{slot_id}-{slot_name}")
        config_path = work_dir / f"{seed}.json"
        config_payload = dict(slot["randomizer_config"])
        config_payload["seed"] = seed
        config_path.write_text(json.dumps(config_payload, indent=2, sort_keys=True), encoding="utf-8")

        randomized_options_path = export_exact_randomized_options(
            ap_zip_path,
            work_dir,
            slot_id,
            seed,
            randomizer_root,
            extracted_data_path,
        )
        metadata_path = write_archipelago_seed_metadata(work_dir, slot, seed)
        generate_python_seed(randomizer_root, work_dir, seed, config_path, randomized_options_path, metadata_path)
        validate_generated_seed_staging(randomizer_root, seed, slot_name)

        pak_path = select_generated_pak(work_dir, seed, slot_name)
        destination = output_dir / pak_path.name
        shutil.copy2(pak_path, destination)

        if slot["slot_data"].get("race_mode"):
            from .RaceMode import remove_race_spoilers
            remove_race_spoilers(output_dir, seed)
        spoiler_candidates = [] if slot["slot_data"].get("race_mode") else sorted(
            work_dir.glob(f"SpoilerLog-{seed}*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        spoiler_destination = None
        if spoiler_candidates:
            spoiler_destination = output_dir / spoiler_candidates[0].name
            shutil.copy2(spoiler_candidates[0], spoiler_destination)

        results.append(
            KH3PakResult(
                slot=slot_id,
                slot_name=slot_name,
                pak_path=destination,
                spoiler_log_path=spoiler_destination,
            )
        )

    return results
