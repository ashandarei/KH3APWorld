from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Utils

from .ap_archive import ArchiveValidationError, archive_report_location_lookup, find_archipelago_entry_name
from .PackageResources import safe_file_fragment
from .installer_support.release_archive import verified_archive
from .platform_support.download_tls import create_download_ssl_context

ROOT = Path(Utils.local_path()).resolve()
KH3_LOOSE_WORLD_ROOT = ROOT / "worlds" / "kh3"
KH3_RANDOMIZER_ROOT = KH3_LOOSE_WORLD_ROOT / "randomizer"
KH3_EXTRACTED_DATA = KH3_LOOSE_WORLD_ROOT / "data" / "extracted_locations.json"
KH3_CONTAINER_PAYLOAD = "kh3_seed.json"
KH3_TEMPLATE_URL = "https://github.com/ashandarei/AP-GOA-KH3/releases/download/2.0.2/KH3-SeedTemplates.zip"
KH3_TEMPLATE_SIZE = 227837
KH3_TEMPLATE_SHA256 = "f6665cea301c2f5ecdc47f63dc95163137beb4975cd1ab6d6e4e837037bcb17a"
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
            archipelago_name = find_archipelago_entry_name(archive.namelist())
            payload = archive.read(archipelago_name)
    except ArchiveValidationError as exc:
        raise KH3PakGenerationError(str(exc)) from exc
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
    from .SlotData import attach_seed_rewards, decode_slot_data, needs_seed_rewards, seed_reward_assignment
    from .export_ap_kh3_randomized_options import build_game_item_id_to_name
    slot_info = multidata.get("slot_info")
    slot_data = multidata.get("slot_data")
    if not isinstance(slot_info, dict) or not isinstance(slot_data, dict):
        raise KH3PakGenerationError("Generated AP payload is missing slot_info or slot_data.")

    location_name = archive_report_location_lookup(multidata)
    item_names = build_game_item_id_to_name(multidata)
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

        runtime_data = decode_slot_data(data, slot_info=slot_info, location_name=location_name)
        if not isinstance(runtime_data.get("kh3_randomizer"), dict):
            continue
        if multidata.get("race_mode"):
            runtime_data["race_mode"] = True
        runtime_data = race_slot_data(runtime_data)
        if needs_seed_rewards(runtime_data):
            rewards = {}
            try:
                for address, (item, recipient, flags) in multidata["locations"][slot_id].items():
                    recipient_info = slot_info[recipient]
                    rewards[address] = seed_reward_assignment(
                        item, recipient, flags, slot_id, recipient_info.name, recipient_info.game,
                        item_names[recipient_info.game][item],
                    )
            except (KeyError, AttributeError) as exc:
                raise KH3PakGenerationError(f"AP archive is missing reward data for KH3 slot {slot_id}.") from exc
            runtime_data = attach_seed_rewards(runtime_data, rewards)
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
                         config_path: Path, options_path: Path, metadata_path: Path) -> tuple[Path, Path | None]:
    from .seedgen.generator import generate_seed

    try:
        return generate_seed(randomizer_root, output, seed,
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


def _fetch_seed_templates(url: str, size: int) -> bytes:
    with tempfile.TemporaryDirectory(prefix="kh3-seed-templates-") as directory:
        path = Path(directory) / "templates.zip"
        _download_file(url, path)
        with path.open("rb") as stream:
            return stream.read(size + 1)


def _released_seed_templates() -> bytes:
    cache = Path(Utils.user_path("kh3", "cache", "seed-templates"))
    try:
        return verified_archive(KH3_TEMPLATE_URL, KH3_TEMPLATE_SIZE, KH3_TEMPLATE_SHA256,
                                _fetch_seed_templates, cache)
    except (OSError, ValueError) as exc:
        raise KH3PakGenerationError(f"Could not download or verify KH3 seed templates: {exc}") from exc


def prepare_randomizer_root(work_dir: Path) -> Path:
    destination = work_dir / "bundled-randomizer"
    if get_apworld_archive_context() is None and (KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.json").is_file():
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
        # Source builds use the loose template inputs for offline development.
        selection = GitIgnoreSpec.from_lines(p for p in patterns if p != "/randomizer/Content/**")

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

    data = _released_seed_templates()
    copy_apworld_tree("randomizer", destination)
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        for entry in bundle.infolist():
            parts = entry.filename.split("/")
            if (len(parts) < 2 or parts[0] != "Content" or any(part in ("", ".", "..") for part in parts)
                    or "\\" in entry.filename or ":" in entry.filename):
                raise KH3PakGenerationError(f"Invalid KH3 seed template: {entry.filename}")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
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
    output_dir = Path(output_dir).expanduser().resolve() if output_dir else container_path.parent / "kh3-player-paks"
    return _generate_player_paks([container_data], output_dir)[0]


def _generate_player_paks(players: list[dict[str, Any]], output_dir: Path) -> list[KH3PakResult]:
    for player in players:
        if (not isinstance(player.get("slot"), int) or player["slot"] <= 0
                or not isinstance(player.get("slot_name"), str) or not player["slot_name"].strip()
                or not isinstance(player.get("randomizer_config"), dict)
                or not isinstance(player.get("pak_seed"), str) or not player["pak_seed"].strip()):
            raise KH3PakGenerationError("KH3 seed is missing valid player, seed or randomizer settings.")
    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ensure_kh3_generation_tools_available()
    randomizer_root = prepare_randomizer_root(work_dir)
    extracted_data_path = prepare_extracted_data(work_dir)
    return [
        _generate_player_pak(player, output_dir, work_dir, randomizer_root, extracted_data_path)
        for player in players
    ]


def _generate_player_pak(container_data: dict[str, Any], output_dir: Path, work_dir: Path,
                         randomizer_root: Path, extracted_data_path: Path) -> KH3PakResult:
    slot_id = int(container_data["slot"])
    slot_name = str(container_data["slot_name"])
    config_payload = dict(container_data["randomizer_config"])
    seed = safe_file_fragment(container_data["pak_seed"])
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
    pak_path, spoiler_path = generate_python_seed(
        randomizer_root, work_dir, seed, config_path, randomized_options_path, metadata_path,
    )
    validate_generated_seed_staging(randomizer_root, seed, slot_name)
    destination = output_dir / pak_path.name
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        copy = Path(temporary) / pak_path.name
        shutil.copy2(pak_path, copy)
        copy.replace(destination)

    if container_data["slot_data"].get("race_mode"):
        from .RaceMode import remove_race_spoilers
        remove_race_spoilers(output_dir, seed)
    spoiler_destination = None
    if spoiler_path is not None:
        spoiler_destination = output_dir / spoiler_path.name
        shutil.copy2(spoiler_path, spoiler_destination)

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
    return generate_kh3_paks_from_archive_slots(ap_zip_path, output_dir, kh3_slots)


def generate_kh3_paks_from_archive_slots(
    ap_zip_path: Path, output_dir: Path, slots: list[dict[str, Any]],
) -> list[KH3PakResult]:
    players = []
    for slot in slots:
        slot_data = slot["slot_data"]
        identity = slot_data.get("kh3_seed", {})
        if not isinstance(identity, dict) or not isinstance(identity.get("archive_name"), str) or not identity["archive_name"]:
            raise KH3PakGenerationError(f"KH3 slot {slot['slot']} is missing its embedded seed identity.")
        players.append({
            "pak_seed": identity.get("pak_seed"),
            "archive_name": identity["archive_name"],
            "slot": slot["slot"],
            "slot_name": slot["name"],
            "slot_data": slot_data,
            "randomizer_config": slot["randomizer_config"],
        })
    results = _generate_player_paks(players, output_dir)
    for player in players:
        if player["slot_data"].get("race_mode"):
            from .RaceMode import remove_race_spoilers
            # Prior ZIP imports named spoiler files after the downloaded archive.
            previous_seed = safe_file_fragment(f"{ap_zip_path.stem}-{player['slot']}-{player['slot_name']}")
            for directory in (output_dir, output_dir / "work"):
                remove_race_spoilers(directory, previous_seed)
    return results
