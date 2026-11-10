"""Typed, headless presentation models for the Kingdom Hearts III client."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..Ingredients import INGREDIENT_SPOTS_BY_ID, INGREDIENT_SPOT_DESCRIPTIONS, INGREDIENT_STATE_SCHEMA
from .replay_slot_presentation import (
    ReplaySlotSelectionInput,
    ReplaySlotSummary,
    build_replay_slot_summaries,
)

logger = logging.getLogger(__name__)
Payload = Mapping[str, Any]
CLIENT_STATUS_KEYS = "slot_replay_indices checks received_items highest_ap_index pending_items applied_items runtime_blocking_ui special_battle_mode sora_state current_map map_safe runtime_safe_context ap_items_grantable last_ingredient_pickup".split()


@dataclass(frozen=True, slots=True)
class RuntimeGrantSummary:
    blocking_ui: str
    special_battle_mode: str
    sora_state: str
    current_map: str
    map_safe: str
    safe_context: str
    ap_items_grantable: str


@dataclass(frozen=True, slots=True)
class ClientStatusSnapshot:
    replay_slots: tuple[ReplaySlotSummary, ...]
    checks: str
    received_items: str
    highest_ap_index: str
    pending_items: str
    applied_items: str
    runtime_grant: RuntimeGrantSummary
    last_ingredient_pickup: str = "-"

    @property
    def item_counts(self) -> str:
        return f"{self.received_items} / {self.pending_items} / {self.applied_items}"

    @property
    def slot_replay_indices(self) -> str:
        return " | ".join(summary.text for summary in self.replay_slots) or "-"

    def value_for(self, key: str) -> str:
        runtime_fields = {
            "runtime_blocking_ui": self.runtime_grant.blocking_ui,
            "special_battle_mode": self.runtime_grant.special_battle_mode,
            "sora_state": self.runtime_grant.sora_state,
            "current_map": self.runtime_grant.current_map,
            "map_safe": self.runtime_grant.map_safe,
            "runtime_safe_context": self.runtime_grant.safe_context,
            "ap_items_grantable": self.runtime_grant.ap_items_grantable,
        }
        if key == "slot_replay_indices":
            return self.slot_replay_indices
        if key in runtime_fields:
            return runtime_fields[key]
        value = getattr(self, key, "-")
        return value if isinstance(value, str) else "-"

    def as_legacy_dict(self) -> dict[str, str]:
        return {key: self.value_for(key) for key in CLIENT_STATUS_KEYS}


@dataclass(frozen=True, slots=True)
class InstalledRuntimeSummary:
    installed: bool
    ue4ss_ok: bool
    ue4ss_settings_ok: bool
    ue4ss_conflict: bool
    ue4ss_existing: bool
    probe_layout_ready: bool
    ue4ss_status: str
    ue4ss_compatibility: str
    probe_status: str
    goa_status: str
    patch_tooltip: str
    goa_version: str


def describe_last_ingredient_pickup(payload: Any, slot_data: Any) -> str:
    """Display only the latest native field event for this seed, never stock."""
    if not isinstance(payload, Mapping) or not isinstance(slot_data, Mapping):
        return "-"
    seed = slot_data.get("kh3_seed", {})
    if (not isinstance(seed, Mapping) or not seed.get("seed_name")
            or payload.get("schema") != INGREDIENT_STATE_SCHEMA
            or payload.get("source") != "food_prize"
            or payload.get("seed_name") != seed["seed_name"]
            or type(payload.get("slot")) is not int or payload["slot"] != seed.get("slot")):
        return "-"
    spot = payload.get("last_pickup_spot")
    record = INGREDIENT_SPOTS_BY_ID.get(spot) if isinstance(spot, str) else None
    if not record:
        return "-"
    location = INGREDIENT_SPOT_DESCRIPTIONS[spot].removeprefix("Ingredients+ - ")
    return spot + " | " + location


def describe_highest_ap_index(payload: Any) -> str:
    summary = payload.get("native_delivery_summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return "-"
    index = summary["highest_received_index"]
    return str(index) if index >= 0 else "-"


def display_pending_item_count(delivery: Any) -> str:
    if (not isinstance(delivery, dict) or not delivery.get("native_delivery_ready")
            or not isinstance(delivery.get("native_delivery_summary"), dict)):
        return "Waiting for KH3"
    return str(delivery["native_delivery_summary"]["pending_count"])


def format_probe_dll_status(runtime: Payload) -> str:
    if not runtime.get("probe_dll_installed"):
        return "Missing"
    details = [f"v{runtime.get('probe_dll_version') or 'unknown version'}"]
    if runtime.get("probe_dll_build_stamp"):
        details.append(str(runtime["probe_dll_build_stamp"]))
    if runtime.get("probe_dll_short_hash"):
        details.append(f"sha256 {runtime['probe_dll_short_hash']}")
    return " | ".join(details)


def format_goa_mod_status(goa: Payload) -> str:
    status = str(goa.get("status") or "unknown")
    version = str(goa.get("expected_version") or "").strip()
    suffix = f" v{version}" if version else ""
    if status == "current":
        return f"Current{suffix}"
    if status == "outdated":
        if isinstance(goa.get("installed_size"), int) and isinstance(goa.get("expected_size"), int):
            return f"Outdated; installed {goa['installed_size']} bytes, expected {goa['expected_size']}"
        return f"Outdated; Patch Game will update{suffix}"
    if status == "missing":
        return f"Missing; Patch Game will install{suffix}"
    return "Installed; version check unavailable"


def format_goa_mod_version(goa: Payload) -> str:
    if goa.get("status") == "missing":
        return "Missing"
    # The expected release is the installed version only after verification.
    version = str(goa.get("expected_version") or "").strip()
    if goa.get("status") == "current" and version:
        return "v" + version.removeprefix("v")
    return "Unknown"


def _ue4ss_file_actions(runtime: Payload) -> list[str]:
    file_state = runtime.get("ue4ss_file_state")
    if isinstance(file_state, dict):
        missing = sorted(name for name, state in file_state.items() if state == "missing")
        different = sorted(
            name
            for name, state in file_state.items()
            if state == "different" and not (name == "UE4SS-settings.ini" and runtime.get("ue4ss_settings_compliant"))
        )
    elif isinstance(runtime.get("ue4ss_files"), dict):
        missing = sorted(name for name, present in runtime["ue4ss_files"].items() if not present)
        different = []
    else:
        missing = different = []
    actions = ["Install UE4SS runtime files: " + ", ".join(missing)] if missing else []
    if different:
        actions.append("Update UE4SS runtime files: " + ", ".join(different))
    return actions


def _runtime_patch_actions(runtime: Payload) -> list[str]:
    actions = _ue4ss_file_actions(runtime)
    if not runtime.get("ue4ss_proxy_installed"):
        actions.append("Install UE4SS proxy DLL")
    if runtime.get("xinput_runtime_ready") is False:
        xinput = runtime.get("xinput_runtime") or {}
        actions.append("Configure Wine built-in XInput" if xinput.get("platform") == "linux"
                       else "Install Microsoft's DirectX XInput runtime (license/admin prompts if needed)")
    wine_loader = runtime.get("proton_dwmapi_override")
    if isinstance(wine_loader, dict) and wine_loader.get("applicable") and not wine_loader.get("installed"):
        actions.append("Configure Wine UE4SS loader")
    if not runtime.get("ue4ss_settings_compliant"):
        actions.append("Apply KH3 AP UE4SS hook settings")
    if not runtime.get("probe_dll_installed"):
        actions.append("Install KH3 AP runtime DLL")
    elif not runtime.get("probe_dll_matches_bundle"):
        actions.append("Replace KH3 AP runtime DLL with the bundled build")
    if not runtime.get("probe_mod_enabled"):
        actions.append("Enable KH3 AP probe mod in Mods\\mods.txt")
    if runtime.get("legacy_probe_mod_enabled"):
        actions.append("Disable legacy nested KH3 AP probe mod entry")
    if not runtime.get("probe_config_installed"):
        actions.append("Write KH3 AP bridge config")
    return actions


def patch_game_tooltip(runtime: Payload, goa: Payload) -> str:
    actions = _runtime_patch_actions(runtime)
    pool_config = runtime.get("uobject_config")
    if isinstance(pool_config, dict) and pool_config.get("status") != "ready":
        actions.append("Set the UObject pool minimum to 2,097,152 in the selected account's Engine.ini (next game start)")
    goa_status = str(goa.get("status") or "unknown")
    version = str(goa.get("expected_version") or "").strip()
    suffix = f" v{version}" if version else ""
    if goa_status == "missing":
        actions.append(f"Install Garden of Assemblage pak{suffix}")
    elif goa_status == "outdated":
        actions.append(f"Update Garden of Assemblage pak{suffix}")
    elif goa_status == "unknown":
        actions.append("Check Garden of Assemblage pak version")
    blockers = []
    if str(runtime.get("status_error") or "").strip():
        blockers.append(f"Status check failed: {runtime['status_error']}")
    blockers.extend(
        message
        for field, message in (
            ("win64_exists", "Find KH3 Binaries\\Win64"),
            ("bundle_win64_exists", "Find bundled UE4SS runtime files"),
            ("bundle_probe_exists", "Find KH3 runtime release configuration"),
        )
        if not runtime.get(field, True)
    )
    if not actions and not blockers:
        return "Game runtime files are already ready."
    lines = ["Patch Game will:", *(f"- {action}" for action in actions)] if actions else []
    if blockers:
        if lines:
            lines.extend(("", "Patch Game also needs to:"))
        else:
            lines.append("Patch Game needs to:")
        lines.extend(f"- {blocker}" for blocker in blockers)
    return "\n".join(lines)


def build_installed_runtime_summary(runtime: Payload, goa: Payload) -> InstalledRuntimeSummary:
    files = runtime.get("ue4ss_files")
    required_ok = all(files.values()) if isinstance(files, dict) else False
    wine_loader = runtime.get("proton_dwmapi_override")
    wine_loader_ok = not isinstance(wine_loader, dict) or not wine_loader.get("applicable") or wine_loader.get("installed")
    ue4ss_ok = (required_ok and bool(runtime.get("ue4ss_proxy_installed"))
                and bool(runtime.get("xinput_runtime_ready", True)) and bool(wine_loader_ok))
    settings_ok = bool(runtime.get("ue4ss_settings_compliant"))
    conflict = bool(runtime.get("ue4ss_custom_conflict"))
    probe_ready = bool(runtime.get("probe_single_layout_ready"))
    pool_config = runtime.get("uobject_config")
    pool_ready = not isinstance(pool_config, dict) or pool_config.get("status") == "ready"
    installed = ue4ss_ok and settings_ok and not conflict and probe_ready and goa.get("status") == "current" and pool_ready
    if ue4ss_ok and settings_ok and not conflict:
        ue4ss_status = format_probe_dll_status(runtime)
    else:
        ue4ss_status = "Missing or different"
    existing = bool(runtime.get("ue4ss_existing"))
    compatibility = (
        "Different UE4SS detected; install will back it up and replace it"
        if conflict
        else "KH3 AP bundled runtime active"
        if existing
        else "No UE4SS detected"
    )
    return InstalledRuntimeSummary(
        installed=installed,
        ue4ss_ok=ue4ss_ok,
        ue4ss_settings_ok=settings_ok,
        ue4ss_conflict=conflict,
        ue4ss_existing=existing,
        probe_layout_ready=probe_ready,
        ue4ss_status=ue4ss_status,
        ue4ss_compatibility=compatibility,
        probe_status="Installed in active Mods layouts" if probe_ready else "Missing, different, or legacy enabled",
        goa_status=format_goa_mod_status(goa),
        patch_tooltip=patch_game_tooltip(runtime, goa),
        goa_version=format_goa_mod_version(goa),
    )


class ClientStatusPresenter:
    """Compose context state and bridge payloads into typed GUI snapshots."""

    def __init__(
        self,
        source: Any,
        *,
        runtime_status_loader: Callable[[Path], dict[str, Any]] | None = None,
    ) -> None:
        if runtime_status_loader is None:
            from ..installer_support.status import runtime_install_status

            runtime_status_loader = runtime_install_status
        self.source = source
        self.runtime_status_loader = runtime_status_loader
        self._cached_goa_status: dict[str, Any] | None = None
        self._cached_runtime_status: dict[str, Any] | None = None
        self._runtime_status_root: Path | None = None
        self._runtime_status_revision = 0
        self._goa_status_revision = 0

    def invalidate_runtime_status_cache(self) -> None:
        """Reinspect after an install/uninstall, including partially failed actions."""
        self._runtime_status_revision += 1
        self._cached_runtime_status = None

    def invalidate_goa_status_cache(self) -> None:
        """Force the next runtime summary to recheck the installed GoA pak."""
        self._goa_status_revision += 1
        self._cached_goa_status = None

    def build_status_snapshot_with_replay_slots(self) -> tuple[ClientStatusSnapshot, tuple[ReplaySlotSummary, ...]]:
        payloads = self.source._load_gui_status_payloads()
        snapshot = self.build_status_snapshot_from_payloads(payloads)
        summaries = build_replay_slot_summaries(
            ReplaySlotSelectionInput(
                native_save_receipts=payloads["native_save_receipts"],
                runtime_current_save_slot=payloads["runtime_current_save_slot"],
                runtime_display_save_slot=payloads["runtime_display_save_slot"],
                last_runtime_save_load_event=payloads["last_runtime_save_load_event"],
            )
        )
        return snapshot, summaries

    def build_replay_slot_summaries(self) -> tuple[ReplaySlotSummary, ...]:
        payloads = self.source._load_gui_status_payloads()
        return build_replay_slot_summaries(
            ReplaySlotSelectionInput(
                native_save_receipts=payloads["native_save_receipts"],
                runtime_current_save_slot=payloads["runtime_current_save_slot"],
                runtime_display_save_slot=payloads["runtime_display_save_slot"],
                last_runtime_save_load_event=payloads["last_runtime_save_load_event"],
            )
        )

    def build_status_snapshot_from_payloads(self, payloads: Mapping[str, Payload]) -> ClientStatusSnapshot:
        source = self.source
        delivery = payloads["item_delivery_payload"]
        native_summary = delivery.get("native_delivery_summary")
        checked_count = len(source.checked_locations | source.locations_checked)
        total_locations = source.total_locations or len(source.location_key_to_id)
        native_status = payloads["runtime_status"].get("presentation", {})
        def status_text(key: str) -> str:
            value = native_status.get(key) if isinstance(native_status, Mapping) else None
            return value if isinstance(value, str) and value else "Unavailable"

        runtime = RuntimeGrantSummary(
            blocking_ui=status_text("blocking_ui"),
            special_battle_mode=status_text("special_battle_mode"),
            sora_state=status_text("sora_state"),
            current_map=status_text("current_map"),
            map_safe=status_text("map_safe"),
            safe_context=status_text("safe_context"),
            ap_items_grantable=status_text("ap_items_grantable"),
        )
        replay_slots = build_replay_slot_summaries(
            ReplaySlotSelectionInput(
                native_save_receipts=payloads["native_save_receipts"],
                runtime_current_save_slot=payloads["runtime_current_save_slot"],
                runtime_display_save_slot=payloads["runtime_display_save_slot"],
                last_runtime_save_load_event=payloads["last_runtime_save_load_event"],
            )
        )
        return ClientStatusSnapshot(
            replay_slots=replay_slots,
            checks=f"{checked_count}/{total_locations}" if total_locations else str(checked_count),
            received_items=str(native_summary["received_count"]) if native_summary is not None else "-",
            highest_ap_index=describe_highest_ap_index(delivery),
            pending_items=display_pending_item_count(delivery),
            applied_items=(str(native_summary["applied_count"]) if native_summary is not None
                           else "-"),
            runtime_grant=runtime,
            last_ingredient_pickup=describe_last_ingredient_pickup(
                payloads.get("runtime_ingredient_state"), getattr(source, "slot_data", {})),
        )

    def build_installed_runtime_summary(self, *, now: float) -> InstalledRuntimeSummary:
        del now  # Installation inspection lasts until the root or managed files change.
        source = self.source
        root = Path(source.kh3_dir)
        if root != self._runtime_status_root:
            self.invalidate_runtime_status_cache()
            self.invalidate_goa_status_cache()
            self._runtime_status_root = root
        revision = self._runtime_status_revision
        try:
            runtime_status = self._cached_runtime_status
            if runtime_status is None:
                runtime_status = self.runtime_status_loader(root)
                if revision == self._runtime_status_revision:
                    self._cached_runtime_status = runtime_status
        except Exception as exc:
            logger.warning("Could not refresh KH3 runtime install status: %s", exc, exc_info=True)
            runtime_status = {
                "ue4ss_files": {},
                "ue4ss_proxy_installed": False,
                "ue4ss_custom_conflict": False,
                "ue4ss_existing": False,
                "bundle_win64_exists": False,
                "bundle_probe_exists": False,
                "probe_dll_installed": False,
                "probe_config_installed": False,
                "probe_mod_enabled": False,
                "status_error": str(exc),
            }
        goa_revision = self._goa_status_revision
        goa_status = self._cached_goa_status
        if goa_status is None:
            try:
                # The native installation view runs this inspection in a worker.
                # Populate the file hash cache instead of permanently caching
                # an unknown result from the cheap, cached-only status accessor.
                refresh_goa = getattr(source, "refresh_goa_mod_status", None)
                goa_status = refresh_goa() if callable(refresh_goa) else source.goa_mod_status()
            except Exception as exc:
                logger.warning("Could not refresh Garden of Assemblage pak status: %s", exc, exc_info=True)
                goa_status = {"status": "unknown", "installed": source.has_goa_mod()}
            if goa_revision == self._goa_status_revision:
                self._cached_goa_status = goa_status

        save_dir = getattr(source, "bridge_save_dir", None)
        if save_dir is not None:
            from ..platform_support.uobject_config import inspect_uobject_config

            runtime_status = {**runtime_status, "uobject_config": inspect_uobject_config(Path(save_dir))}
        return build_installed_runtime_summary(runtime_status, goa_status)

    def active_runtime_save_directory(self) -> tuple[Path | None, str]:
        """Prefer the active bridge save directory and retain configured fallback."""
        source = self.source
        try:
            bridge_status = source._load_bridge_payload("bridge_runtime_status.json") or {}
        except Exception as exc:
            logger.warning("Could not refresh KH3 bridge runtime status: %s", exc, exc_info=True)
            bridge_status = {}
        save_dir = bridge_status.get("save_dir")
        if bridge_status.get("status") == "running" and isinstance(save_dir, str) and save_dir:
            return Path(save_dir), str(bridge_status.get("save_dir_source") or "bridge")
        return getattr(source, "bridge_save_dir", None), "configured"
