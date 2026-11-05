"""KH3 seed-container generation, managed import, and missing-pak preparation."""

from __future__ import annotations

import copy
import hashlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..PakGenerator import (
    KH3PakGenerationError,
    KH3PakResult,
    generate_kh3_pak_from_container,
    generate_kh3_paks_from_ap_zip,
    iter_kh3_slots,
    load_multidata_from_ap_zip,
    safe_file_fragment,
)
from .run_manager import KH3RunInfo, KH3RunManagerError, KH3RunManagerService
from .save_file_policy import checked_root
from .seed_guard import SeedGuardService
from .seed_paks import extract_kh3_ap_seed_metadata_from_pak, kh3_pak_player_name

KH3_SEED_CONTAINER_SUFFIX = ".apkh3"
KH3_GENERATED_ARCHIVE_SUFFIX = ".zip"
KH3_IMPORTABLE_SEED_SUFFIXES = frozenset({".pak", KH3_SEED_CONTAINER_SUFFIX, KH3_GENERATED_ARCHIVE_SUFFIX})
KH3_GENERATED_WORK_HASH_LENGTH = 12

logger = logging.getLogger("Client")


class KH3SeedImportError(RuntimeError):
    """A user-actionable KH3 seed source generation or selection failure."""


@dataclass(frozen=True, slots=True)
class KH3SeedImportResult:
    """Managed runs produced from one pak, player container, or generated archive."""

    source_path: Path
    runs: tuple[KH3RunInfo, ...]
    generated_paks: tuple[Path, ...]
    archived_source_path: Path | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KH3SeedPreparationResult:
    """Result of looking for and installing a container when no AP pak exists."""

    status: str
    source_path: Path | None = None
    installed_run: KH3RunInfo | None = None
    generated_paks: tuple[Path, ...] = ()
    archived_source_path: Path | None = None
    warnings: tuple[str, ...] = ()
    activated_save_dir: Path | None = None


PathSource = Path | Callable[[], Path]
ContainerGenerator = Callable[[Path, Path], KH3PakResult]
ArchiveGenerator = Callable[..., list[KH3PakResult]]
ArchiveLoader = Callable[[Path], dict[str, Any]]
SlotIterator = Callable[[dict[str, Any], int | None], list[dict[str, Any]]]
ProgressCallback = Callable[[str], None]
SeedContainerWriter = Callable[[Path, dict[str, Any], str], None]
SeedMetadataLoader = Callable[[Path], dict[str, Any] | None]
UnidentifiedSavePreparer = Callable[[Path], dict[str, Any]]


class KH3SeedImportService:
    """Generate and import KH3 player paks while keeping selection policy headless."""

    def __init__(
        self,
        *,
        run_manager: KH3RunManagerService,
        mods_dir: PathSource,
        generated_root: PathSource,
        process_running: Callable[[], bool],
        container_generator: ContainerGenerator = generate_kh3_pak_from_container,
        archive_generator: ArchiveGenerator = generate_kh3_paks_from_ap_zip,
        archive_loader: ArchiveLoader = load_multidata_from_ap_zip,
        slot_iterator: SlotIterator = iter_kh3_slots,
        container_writer: SeedContainerWriter | None = None,
        seed_metadata_loader: SeedMetadataLoader | None = extract_kh3_ap_seed_metadata_from_pak,
        unidentified_save_preparer: UnidentifiedSavePreparer | None = None,
    ) -> None:
        self.run_manager = run_manager
        self._mods_dir_source = mods_dir
        self._generated_root_source = generated_root
        self.process_running = process_running
        self.container_generator = container_generator
        self.archive_generator = archive_generator
        self.archive_loader = archive_loader
        self.slot_iterator = slot_iterator
        self.container_writer = container_writer or self._write_seed_container
        self.unidentified_save_preparer = unidentified_save_preparer
        self.seed_metadata_loader = seed_metadata_loader

    def _preserve_unidentified_active_saves(
        self,
        *,
        save_dir_override: Path | str | None,
        active_save_seed_id: str | None,
        active_save_run_id: str | None,
        progress: ProgressCallback | None = None,
    ) -> tuple[tuple[str, ...], str | None, str | None]:
        associated_seed_id = SeedGuardService.extract_seed_id(active_save_seed_id)
        associated_run_id = str(active_save_run_id or "").strip() or None

        if associated_run_id:
            try:
                associated_run = self.run_manager.get_run(associated_run_id)
            except KH3RunManagerError:
                associated_run = None
            if (associated_run is not None and associated_run.managed and associated_run.pak_available
                    and self.run_manager.matches_platform(associated_run)):
                if associated_seed_id and associated_seed_id != associated_run.seed_id:
                    logger.warning(
                        "KH3 active-save seed association %s disagrees with managed run %s (seed %s); "
                        "using the exact managed run association.",
                        associated_seed_id,
                        associated_run.run_id,
                        associated_run.seed_id,
                    )
                return (), associated_run.seed_id, associated_run.run_id

        if associated_seed_id:
            matching_runs = [
                run
                for run in self.run_manager.list_runs()
                if run.managed and self.run_manager.matches_platform(run) and run.pak_available and run.seed_id == associated_seed_id
            ]
            if len(matching_runs) == 1:
                matching_run = matching_runs[0]
                return (), matching_run.seed_id, matching_run.run_id

        if self.unidentified_save_preparer is None:
            return (), None, None

        save_dir = (
            checked_root(Path(save_dir_override))
            if save_dir_override is not None
            else self.run_manager.save_dir
        )
        self._report(progress, "Protecting unidentified active KH3 saves before installing the AP run...")
        result = self.unidentified_save_preparer(save_dir)
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        if errors:
            raise KH3SeedImportError(
                "Could not safely preserve the unidentified KH3 saves before installing the AP run: "
                + "; ".join(str(error) for error in errors[:5])
            )

        preserved_files = int(result.get("preserved_files", 0))
        if preserved_files <= 0:
            return (), None, None
        preserved_dir = str(result.get("preserved_dir") or "a protected backup")
        if result.get("mode") == "vanilla_backup":
            warnings = (
                f"Protected {preserved_files} unidentified active save file(s) as the vanilla backup at "
                f"{preserved_dir} before installing the AP run.",
            )
        else:
            warnings = (
                f"Archived {preserved_files} unidentified active save file(s) to {preserved_dir} before installing "
                "the AP run; the existing vanilla backup was left unchanged.",
            )
        return warnings, None, None

    def activate_installed_run_for_connection(
        self,
        pak_path: Path,
        *,
        expected_seed_id: str,
        expected_slot: int,
        server_address: str,
        connection_slot_name: str,
        room_url: str | None,
        save_dir_override: Path,
        active_save_seed_id: str | None,
        active_save_run_id: str | None,
    ) -> KH3SeedPreparationResult:
        """Reconcile a copied pak with its save profile before starting the bridge."""
        if self.process_running():
            raise KH3SeedImportError("Close KINGDOM HEARTS III, then reconnect to activate this run automatically.")
        identity = self.run_manager.pak_identity(pak_path)
        if identity.seed_id != expected_seed_id or identity.player != expected_slot:
            raise KH3SeedImportError("The installed pak no longer matches the connected seed and player.")
        if self.run_manager.installed_pak_count() != 1 or pak_path.resolve().parent != self.mods_dir:
            raise KH3SeedImportError("Automatic activation requires exactly one installed seed pak.")
        enrollment = self.run_manager.ensure_pak_managed(
            pak_path, expected_seed_id=expected_seed_id, server_address=server_address,
            connection_slot_name=connection_slot_name, room_url=room_url,
        )
        warnings, save_seed, save_run = self._preserve_unidentified_active_saves(
            save_dir_override=save_dir_override,
            active_save_seed_id=active_save_seed_id,
            active_save_run_id=active_save_run_id,
        )
        # Without a configured backup handler, retain ownership so activation
        # fails closed rather than assigning unknown saves to the copied pak.
        if self.unidentified_save_preparer is None and save_seed is None and save_run is None:
            raise KH3SeedImportError("Automatic activation requires a save backup handler for unidentified saves.")
        result = self.run_manager.activate(
            enrollment.run.run_id, save_dir_override=save_dir_override,
            active_save_seed_id=save_seed, active_save_run_id=save_run,
        )
        return KH3SeedPreparationResult(
            "activated", source_path=pak_path, installed_run=result.run,
            activated_save_dir=result.save_dir, warnings=warnings + tuple(result.warnings),
        )

    @staticmethod
    def _resolve_path(source: PathSource) -> Path:
        value = source() if callable(source) else source
        return Path(value).expanduser().resolve()

    def _activated_save_dir(self, save_dir_override: Path | str | None) -> Path:
        if save_dir_override is not None:
            return checked_root(Path(save_dir_override))
        return self.run_manager.save_dir

    @property
    def mods_dir(self) -> Path:
        return self._resolve_path(self._mods_dir_source)

    @property
    def generated_root(self) -> Path:
        return self._resolve_path(self._generated_root_source)

    def discover_container_sources(self) -> tuple[Path, ...]:
        mods_dir = self.mods_dir
        if not mods_dir.is_dir():
            return ()
        sources = [
            path.resolve()
            for path in mods_dir.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() in {KH3_SEED_CONTAINER_SUFFIX, KH3_GENERATED_ARCHIVE_SUFFIX}
        ]
        return tuple(sorted(sources, key=lambda path: path.name.casefold()))

    def has_container_source(self) -> bool:
        return bool(self.discover_container_sources())

    def _output_dir(self, source_path: Path) -> Path:
        path_key = str(source_path).casefold().encode("utf-8", errors="surrogatepass")
        digest = hashlib.sha256(path_key).hexdigest()[:KH3_GENERATED_WORK_HASH_LENGTH]
        return self.generated_root / digest

    @staticmethod
    def _report(progress: ProgressCallback | None, message: str) -> None:
        if progress is not None:
            progress(message)

    def _store_embedded_seed_metadata(
        self,
        pak_path: Path,
        run: KH3RunInfo,
        *,
        progress: ProgressCallback | None = None,
    ) -> KH3RunInfo:
        """Read exact embedded metadata once and cache it with the managed run."""

        loader = self.seed_metadata_loader
        if loader is None:
            return run
        if any((bridge_dir / "kh3_ap_seed_metadata.json").is_file() for bridge_dir in run.bridge_dirs):
            return run

        self._report(progress, f"Reading embedded seed metadata for {run.slot_name}...")
        try:
            metadata = loader(pak_path)
        except Exception as exc:
            logger.warning("Could not read embedded KH3 seed metadata from %s: %s", pak_path, exc)
            return run
        if metadata is None:
            logger.warning(
                "Could not cache embedded KH3 seed metadata for %s; activation will retry from the installed pak.",
                run.slot_name,
            )
            return run
        self.run_manager.store_seed_metadata(run.run_id, metadata)
        return self.run_manager.get_run(run.run_id)

    def _is_direct_mods_container(self, source_path: Path) -> bool:
        return (
            source_path.parent == self.mods_dir
            and source_path.suffix.casefold() in {KH3_SEED_CONTAINER_SUFFIX, KH3_GENERATED_ARCHIVE_SUFFIX}
            and source_path.is_file()
            and not source_path.is_symlink()
        )

    def _archive_processed_source(self, source_path: Path) -> tuple[Path | None, str | None]:
        """Move a successfully processed direct-child container out of the live mods folder."""

        if not self._is_direct_mods_container(source_path):
            return None, None

        archive_dir = self._output_dir(source_path) / "processed-sources"
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            destination = archive_dir / source_path.name
            counter = 2
            while destination.exists():
                destination = archive_dir / f"{source_path.stem}-{counter}{source_path.suffix}"
                counter += 1
            shutil.move(str(source_path), str(destination))
            return destination.resolve(), None
        except (OSError, shutil.Error) as exc:
            return None, f"Could not move processed seed source out of ~mods ({source_path.name}): {exc}"

    @staticmethod
    def _write_seed_container(path: Path, data: dict[str, Any], server_address: str) -> None:
        """Write a server-delivered seed as a normal player container, atomically."""

        from .. import KH3Container

        path.parent.mkdir(parents=True, exist_ok=True)
        stage_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            KH3Container(
                path=str(stage_path),
                player=int(data["slot"]),
                player_name=str(data["slot_name"]),
                server=str(server_address or ""),
                data=data,
            ).write()
            os.replace(stage_path, path)
        finally:
            try:
                stage_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _seed_id(value: Any) -> str | None:
        match = re.search(r"\d{8,}", str(value or ""))
        return match.group(0) if match else None

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return path.stat().st_size, digest.hexdigest()

    @classmethod
    def _container_data_from_server_slot_data(
        cls,
        slot_data: dict[str, Any],
        *,
        expected_slot: int | None = None,
        expected_slot_name: str | None = None,
        expected_seed_name: str | None = None,
    ) -> dict[str, Any]:
        seed_data = slot_data.get("kh3_seed")
        if not isinstance(seed_data, dict) or seed_data.get("format") != "kh3_server_seed":
            raise KH3SeedImportError(
                "This room does not include a KH3 client-generation payload. "
                "Generate it with a KH3 world version that supports seed-pak creation after connecting."
            )
        if seed_data.get("format_version") != 1:
            raise KH3SeedImportError(
                f"Unsupported KH3 server seed payload version: {seed_data.get('format_version')!r}."
            )

        try:
            slot = int(seed_data["slot"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KH3SeedImportError("The room's KH3 seed payload has an invalid player slot.") from exc
        slot_name = str(seed_data.get("slot_name") or "").strip()
        seed_name = str(seed_data.get("seed_name") or "").strip()
        pak_seed = str(seed_data.get("pak_seed") or "").strip()
        all_players = seed_data.get("all_players")
        remote_hint_placements = seed_data.get("remote_hint_placements")
        randomizer_config = slot_data.get("kh3_randomizer")
        bridge = slot_data.get("bridge")
        if (
            not slot_name
            or not seed_name
            or not pak_seed
            or not isinstance(all_players, list)
            or not isinstance(remote_hint_placements, list)
            or not isinstance(randomizer_config, dict)
            or not isinstance(bridge, dict)
            or not isinstance(bridge.get("locations"), list)
        ):
            raise KH3SeedImportError("The room's KH3 seed payload is incomplete and cannot generate a pak.")
        if expected_slot is not None and slot != expected_slot:
            raise KH3SeedImportError(
                f"The room returned KH3 seed data for slot {slot}, but the client connected as slot {expected_slot}."
            )
        expected_name = str(expected_slot_name or "").strip()
        if expected_name and slot_name.casefold() != expected_name.casefold():
            raise KH3SeedImportError(
                f"The room returned KH3 seed data for {slot_name!r}, not the requested slot {expected_name!r}."
            )
        expected_seed_id = cls._seed_id(expected_seed_name)
        delivered_seed_id = cls._seed_id(seed_name)
        if expected_seed_id and delivered_seed_id != expected_seed_id:
            raise KH3SeedImportError(
                f"The room returned KH3 seed {seed_name!r}, not the connected seed {expected_seed_name!r}."
            )

        from ..SlotData import encode_slot_data
        copied_slot_data = encode_slot_data(slot_data)
        return {
            "format": "kh3_ap_seed",
            "format_version": 1,
            "seed_name": seed_name,
            "archive_name": str(seed_data.get("archive_name") or f"AP_{seed_name}.zip"),
            "slot": slot,
            "slot_name": slot_name,
            "all_players": copy.deepcopy(all_players),
            "pak_seed": pak_seed,
            "randomizer_config": copy.deepcopy(randomizer_config),
            "remote_hint_placements": copy.deepcopy(remote_hint_placements),
            "slot_data": copied_slot_data,
        }

    def _generate_source(
        self,
        source_path: Path,
        *,
        requested_slot: int | None = None,
        progress: ProgressCallback | None = None,
        require_single_player: bool = False,
    ) -> list[KH3PakResult]:
        output_dir = self._output_dir(source_path)
        suffix = source_path.suffix.casefold()
        if suffix == KH3_SEED_CONTAINER_SUFFIX:
            self._report(progress, "Generating the KH3 seed pak. This can take about a minute...")
            return [self.container_generator(source_path, output_dir)]
        if suffix == KH3_GENERATED_ARCHIVE_SUFFIX:
            slots = self.slot_iterator(self.archive_loader(source_path), requested_slot)
            count = len(slots)
            if require_single_player and count != 1:
                raise KH3SeedImportError(
                    "Connection details can be assigned during import only when the seed source has one KH3 player."
                )
            noun = "pak" if count == 1 else "paks"
            duration = "about a minute" if count == 1 else "a few minutes"
            self._report(progress, f"Generating {count} KH3 player {noun}. This can take {duration}...")
            return self.archive_generator(source_path, output_dir, requested_slot=requested_slot)
        raise KH3SeedImportError(
            f"Unsupported KH3 seed source {source_path.name}; choose a .pak, .apkh3, or generated .zip file."
        )

    def import_source(
        self,
        source_path: Path | str,
        *,
        progress: ProgressCallback | None = None,
        server_address: str | None = None,
        connection_slot_name: str | None = None,
        room_url: str | None = None,
    ) -> KH3SeedImportResult:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise KH3SeedImportError(f"KH3 seed source does not exist: {source}")
        profile_requested = bool(server_address or connection_slot_name or room_url is not None)
        try:
            if source.suffix.casefold() == ".pak":
                self._report(progress, "Copying the seed pak into managed run storage...")
                run = (
                    self.run_manager.ensure_pak_managed(
                        source,
                        server_address=server_address,
                        connection_slot_name=connection_slot_name,
                        room_url=room_url,
                    ).run
                    if profile_requested
                    else self.run_manager.import_pak(source)
                )
                run = self._store_embedded_seed_metadata(source, run, progress=progress)
                return KH3SeedImportResult(source, (run,), (source,))
            generated = self._generate_source(source, progress=progress, require_single_player=profile_requested)
            if not generated:
                raise KH3SeedImportError(f"No KH3 player paks were generated from {source.name}.")
            if profile_requested and len(generated) != 1:
                raise KH3SeedImportError(
                    "Connection details can be assigned during import only when the seed source has one KH3 player."
                )
            self._report(progress, "Adding the generated player run(s) to managed storage...")
            managed_runs: list[KH3RunInfo] = []
            for generated_result in generated:
                run = (
                    self.run_manager.ensure_pak_managed(
                        generated_result.pak_path,
                        server_address=server_address,
                        connection_slot_name=connection_slot_name,
                        room_url=room_url,
                    ).run
                    if profile_requested
                    else self.run_manager.import_pak(generated_result.pak_path)
                )
                managed_runs.append(
                    self._store_embedded_seed_metadata(generated_result.pak_path, run, progress=progress)
                )
            runs = tuple(managed_runs)
            if self._is_direct_mods_container(source):
                self._report(progress, "Moving the processed seed source out of ~mods...")
            archived_source, cleanup_warning = self._archive_processed_source(source)
            return KH3SeedImportResult(
                source_path=source,
                runs=runs,
                generated_paks=tuple(result.pak_path.resolve() for result in generated),
                archived_source_path=archived_source,
                warnings=(cleanup_warning,) if cleanup_warning else (),
            )
        except KH3SeedImportError:
            raise
        except (KH3PakGenerationError, KH3RunManagerError, OSError, ValueError) as exc:
            raise KH3SeedImportError(str(exc)) from exc

    def _select_archive_slot(self, source_path: Path, preferred_slot_name: str | None) -> int:
        multidata = self.archive_loader(source_path)
        slots = self.slot_iterator(multidata, None)
        if len(slots) == 1:
            return int(slots[0]["slot"])
        preferred = str(preferred_slot_name or "").strip().casefold()
        matching = [slot for slot in slots if str(slot.get("name") or "").strip().casefold() == preferred]
        if preferred and len(matching) == 1:
            return int(matching[0]["slot"])
        names = ", ".join(str(slot.get("name") or f"Player {slot.get('slot')}") for slot in slots)
        raise KH3SeedImportError(
            f"{source_path.name} contains multiple KH3 players ({names}). Set the client slot name to one of them, "
            "or import the archive from Save Manager and activate the desired player."
        )

    def prepare_missing_installed_pak(
        self,
        *,
        preferred_slot_name: str | None = None,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> KH3SeedPreparationResult:
        if self.run_manager.installed_pak_count():
            return KH3SeedPreparationResult("already_installed")
        sources = self.discover_container_sources()
        if not sources:
            return KH3SeedPreparationResult("no_source")
        if len(sources) > 1:
            names = ", ".join(path.name for path in sources)
            raise KH3SeedImportError(
                f"Multiple KH3 seed containers are in {self.mods_dir}: {names}. Keep only the one to prepare."
            )
        if self.process_running():
            raise KH3SeedImportError("Close KINGDOM HEARTS III before generating and installing its seed pak.")

        source = sources[0]
        try:
            self._report(progress, f"Found {source.name} in ~mods; preparing its KH3 player pak...")
            requested_slot = None
            if source.suffix.casefold() == KH3_GENERATED_ARCHIVE_SUFFIX:
                requested_slot = self._select_archive_slot(source, preferred_slot_name)
            generated = self._generate_source(source, requested_slot=requested_slot, progress=progress)
            if len(generated) != 1:
                raise KH3SeedImportError(
                    f"Expected one KH3 player pak from {source.name}, but generated {len(generated)}."
                )
            self._report(progress, "Adding the generated player run to managed storage...")
            enrollment = self.run_manager.ensure_pak_managed(generated[0].pak_path)
            managed_run = self._store_embedded_seed_metadata(
                generated[0].pak_path,
                enrollment.run,
                progress=progress,
            )
            save_warnings, effective_save_seed_id, effective_save_run_id = (
                self._preserve_unidentified_active_saves(
                    save_dir_override=save_dir_override,
                    active_save_seed_id=active_save_seed_id,
                    active_save_run_id=active_save_run_id,
                    progress=progress,
                )
            )
            self._report(progress, "Installing the generated KH3 seed pak...")
            installed = self.run_manager.install_managed_pak_when_none(
                managed_run.run_id,
                save_dir_override=save_dir_override,
                active_save_seed_id=effective_save_seed_id,
                active_save_run_id=effective_save_run_id,
            )
            if self._is_direct_mods_container(source):
                self._report(progress, "Moving the processed seed source out of ~mods...")
            archived_source, cleanup_warning = self._archive_processed_source(source)
            return KH3SeedPreparationResult(
                status="installed",
                source_path=source,
                installed_run=installed,
                activated_save_dir=self._activated_save_dir(save_dir_override),
                generated_paks=(generated[0].pak_path.resolve(),),
                archived_source_path=archived_source,
                warnings=save_warnings + ((cleanup_warning,) if cleanup_warning else ()),
            )
        except KH3SeedImportError:
            raise
        except (KH3PakGenerationError, KH3RunManagerError, OSError, ValueError) as exc:
            raise KH3SeedImportError(str(exc)) from exc

    def prepare_server_slot_data(
        self,
        slot_data: dict[str, Any],
        *,
        server_address: str = "",
        expected_slot: int | None = None,
        expected_slot_name: str | None = None,
        expected_seed_name: str | None = None,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> KH3SeedPreparationResult:
        """Generate, manage, and install the authenticated player's pak from Connected slot data."""

        if self.run_manager.installed_pak_count():
            raise KH3SeedImportError(
                "A KH3 AP seed pak appeared after connecting; disconnect and select that run before trying again."
            )
        if self.process_running():
            raise KH3SeedImportError("Close KINGDOM HEARTS III before generating and installing its seed pak.")

        container_data = self._container_data_from_server_slot_data(
            slot_data,
            expected_slot=expected_slot,
            expected_slot_name=expected_slot_name,
            expected_seed_name=expected_seed_name,
        )
        source_path = (
            self.generated_root
            / "server-seeds"
            / f"{safe_file_fragment(str(container_data['pak_seed']))}{KH3_SEED_CONTAINER_SUFFIX}"
        )
        bridge = slot_data.get("bridge")
        bridge_locations = bridge.get("locations") if isinstance(bridge, dict) else None
        location_count = len(bridge_locations) if isinstance(bridge_locations, list) else 0
        logger.info(
            "KH3 inline seed payload received: server=%s seed=%s pak_seed=%s archive=%s slot=%s "
            "slot_name=%r bridge_locations=%d",
            str(server_address or "") or "(unknown)",
            container_data["seed_name"],
            container_data["pak_seed"],
            container_data.get("archive_name") or "(unknown)",
            container_data["slot"],
            container_data["slot_name"],
            location_count,
        )
        try:
            self._report(progress, "Received the KH3 seed from the server; preparing its player file...")
            self.container_writer(source_path, container_data, str(server_address or ""))
            container_size, container_sha256 = self._file_identity(source_path)
            logger.info(
                "KH3 inline seed container written: file=%s bytes=%d sha256=%s path=%s",
                source_path.name,
                container_size,
                container_sha256,
                source_path.resolve(),
            )
            generated = self._generate_source(source_path, progress=progress)
            if len(generated) != 1:
                raise KH3SeedImportError(
                    f"Expected one KH3 player pak from the server seed, but generated {len(generated)}."
                )
            generated_size, generated_sha256 = self._file_identity(generated[0].pak_path)
            logger.info(
                "KH3 inline seed pak generated: file=%s bytes=%d sha256=%s path=%s",
                generated[0].pak_path.name,
                generated_size,
                generated_sha256,
                generated[0].pak_path.resolve(),
            )
            self._report(progress, "Adding the generated player run to managed storage...")
            expected_seed_id = self._seed_id(container_data["seed_name"])
            enrollment = self.run_manager.ensure_pak_managed(
                generated[0].pak_path,
                expected_seed_id=expected_seed_id,
                server_address=str(server_address or "").strip() or None,
                connection_slot_name=(
                    str(expected_slot_name or container_data["slot_name"]).strip()
                    if str(server_address or "").strip()
                    else None
                ),
            )
            run = self._store_embedded_seed_metadata(
                generated[0].pak_path,
                enrollment.run,
                progress=progress,
            )
            save_warnings, effective_save_seed_id, effective_save_run_id = (
                self._preserve_unidentified_active_saves(
                    save_dir_override=save_dir_override,
                    active_save_seed_id=active_save_seed_id,
                    active_save_run_id=active_save_run_id,
                    progress=progress,
                )
            )
            self._report(progress, "Installing the generated KH3 seed pak...")
            installed = self.run_manager.install_managed_pak_when_none(
                run.run_id,
                save_dir_override=save_dir_override,
                active_save_seed_id=effective_save_seed_id,
                active_save_run_id=effective_save_run_id,
            )
            return KH3SeedPreparationResult(
                status="installed",
                source_path=source_path.resolve(),
                installed_run=installed,
                activated_save_dir=self._activated_save_dir(save_dir_override),
                generated_paks=(generated[0].pak_path.resolve(),),
                warnings=save_warnings,
            )
        except KH3SeedImportError as exc:
            logger.warning(
                "KH3 inline seed preparation failed: server=%s seed=%s slot=%s slot_name=%r source=%s error=%s",
                str(server_address or "") or "(unknown)",
                container_data["seed_name"],
                container_data["slot"],
                container_data["slot_name"],
                source_path,
                exc,
            )
            raise
        except (KH3PakGenerationError, KH3RunManagerError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "KH3 inline seed preparation failed: server=%s seed=%s slot=%s slot_name=%r source=%s error=%s",
                str(server_address or "") or "(unknown)",
                container_data["seed_name"],
                container_data["slot"],
                container_data["slot_name"],
                source_path,
                exc,
            )
            raise KH3SeedImportError(str(exc)) from exc

    def prepare_player_artifact(
        self,
        source_path: Path | str,
        *,
        server_address: str = "",
        expected_slot: int | None = None,
        expected_slot_name: str | None = None,
        expected_seed_name: str | None = None,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> KH3SeedPreparationResult:
        """Manage and install one authenticated player's hosted pak or seed container."""

        if self.run_manager.installed_pak_count():
            raise KH3SeedImportError(
                "A KH3 AP seed pak appeared after connecting; disconnect and select that run before trying again."
            )
        if self.process_running():
            raise KH3SeedImportError("Close KINGDOM HEARTS III before installing its downloaded seed file.")

        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise KH3SeedImportError(f"The downloaded KH3 player file does not exist: {source}")
        if source.suffix.casefold() not in KH3_IMPORTABLE_SEED_SUFFIXES:
            raise KH3SeedImportError(
                f"Unsupported downloaded KH3 player file {source.name}; expected a .pak, .apkh3, or generated .zip."
            )

        try:
            if source.suffix.casefold() == ".pak":
                self._report(progress, "Adding the downloaded KH3 seed pak to managed storage...")
                pak_paths = [source]
            else:
                requested_slot = None
                if source.suffix.casefold() == KH3_GENERATED_ARCHIVE_SUFFIX:
                    requested_slot = self._select_archive_slot(source, expected_slot_name)
                generated = self._generate_source(source, requested_slot=requested_slot, progress=progress)
                if len(generated) != 1:
                    raise KH3SeedImportError(
                        f"Expected one KH3 player pak from {source.name}, but generated {len(generated)}."
                    )
                self._report(progress, "Adding the generated KH3 seed pak to managed storage...")
                pak_paths = [generated[0].pak_path.resolve()]

            expected_seed_id = self._seed_id(expected_seed_name)
            identity = self.run_manager.pak_identity(pak_paths[0])
            if expected_seed_id is not None and identity.seed_id != expected_seed_id:
                raise KH3SeedImportError(
                    f"The downloaded KH3 player file is for seed {identity.seed_id}, not {expected_seed_id}."
                )
            requested_name = str(expected_slot_name or "").strip()
            if requested_name and identity.slot_name.casefold() != kh3_pak_player_name(requested_name).casefold():
                raise KH3SeedImportError(
                    f"The downloaded KH3 player file is for {identity.slot_name!r}, not slot {requested_name!r}."
                )
            if expected_slot is not None and identity.player != int(expected_slot):
                raise KH3SeedImportError(
                    f"The downloaded KH3 player file is for slot {identity.player}, not connected slot {expected_slot}."
                )
            enrollment = self.run_manager.ensure_pak_managed(
                pak_paths[0],
                expected_seed_id=expected_seed_id,
                server_address=str(server_address or "").strip() or None,
                connection_slot_name=(
                    requested_name or identity.slot_name
                    if str(server_address or "").strip()
                    else None
                ),
            )
            run = self._store_embedded_seed_metadata(pak_paths[0], enrollment.run, progress=progress)
            save_warnings, effective_save_seed_id, effective_save_run_id = (
                self._preserve_unidentified_active_saves(
                    save_dir_override=save_dir_override,
                    active_save_seed_id=active_save_seed_id,
                    active_save_run_id=active_save_run_id,
                    progress=progress,
                )
            )
            self._report(progress, "Installing the downloaded KH3 seed pak...")
            installed = self.run_manager.install_managed_pak_when_none(
                run.run_id,
                save_dir_override=save_dir_override,
                active_save_seed_id=effective_save_seed_id,
                active_save_run_id=effective_save_run_id,
            )
            return KH3SeedPreparationResult(
                status="installed",
                source_path=source,
                installed_run=installed,
                activated_save_dir=self._activated_save_dir(save_dir_override),
                generated_paks=tuple(pak_paths),
                warnings=save_warnings,
            )
        except KH3SeedImportError as exc:
            logger.warning(
                "KH3 downloaded player artifact preparation failed: server=%s expected_seed=%s "
                "expected_slot=%s expected_slot_name=%r source=%s error=%s",
                str(server_address or "") or "(unknown)",
                str(expected_seed_name or "") or "(unknown)",
                expected_slot if expected_slot is not None else "(unknown)",
                str(expected_slot_name or "") or "(unknown)",
                source,
                exc,
            )
            raise
        except (KH3PakGenerationError, KH3RunManagerError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "KH3 downloaded player artifact preparation failed: server=%s expected_seed=%s "
                "expected_slot=%s expected_slot_name=%r source=%s error=%s",
                str(server_address or "") or "(unknown)",
                str(expected_seed_name or "") or "(unknown)",
                expected_slot if expected_slot is not None else "(unknown)",
                str(expected_slot_name or "") or "(unknown)",
                source,
                exc,
            )
            raise KH3SeedImportError(str(exc)) from exc


__all__ = [
    "KH3_GENERATED_ARCHIVE_SUFFIX",
    "KH3_IMPORTABLE_SEED_SUFFIXES",
    "KH3_SEED_CONTAINER_SUFFIX",
    "KH3SeedImportError",
    "KH3SeedImportResult",
    "KH3SeedImportService",
    "KH3SeedPreparationResult",
]
