"""Stable filenames and path construction for shared KH3 bridge sidecars."""

from __future__ import annotations

from pathlib import Path

AP_ITEMS_FILE = "ap_items.json"
AP_STATUS_FILE = "ap_status.json"
BRIDGE_NOTIFICATIONS_FILE = "bridge_notifications.json"
BRIDGE_RUNTIME_STATUS_FILE = "bridge_runtime_status.json"
GAME_STATE_FILE = "game_state.json"
ITEM_DELIVERY_FILE = "item_delivery.json"
ITEM_DELIVERY_LEDGER_FILE = "item_delivery_ledger.json"
ITEM_DELIVERY_STATE_FILE = "item_delivery_state.json"
LUCKY_MARK_NATIVE_AP_RESOLVER_DIAGNOSTICS_FILE = "lucky_mark_native_ap_resolver_diagnostics.jsonl"
RUNTIME_AP_MESSAGE_FILE = "runtime_ap_message.json"
RUNTIME_BLOCKING_UI_STATE_FILE = "runtime_blocking_ui_state.json"
RUNTIME_CLASSIC_KINGDOM_STATE_FILE = "runtime_classic_kingdom_state.json"
RUNTIME_INGREDIENT_STATE_FILE = "runtime_ingredient_state.json"
RUNTIME_ADVERSARY_STATE_FILE = "runtime_adversary_state.json"
RUNTIME_OLYMPUS_COLISEUM_STATE_FILE = "runtime_olympus_coliseum_state.json"
RUNTIME_CONTEXT_FILE = "runtime_context.json"
RUNTIME_CURRENT_SAVE_SLOT_FILE = "runtime_current_save_slot.json"
RUNTIME_DEATH_RETRY_REPLAY_WINDOW_FILE = "runtime_death_retry_replay_window.json"
RUNTIME_DEATHLINK_STATE_FILE = "runtime_deathlink_state.json"
RUNTIME_GRANT_CPP_PROFILE_FILE = "runtime_grant_cpp_profile.jsonl"
RUNTIME_GRANT_PROFILE_FILE = "runtime_grant_profile.jsonl"
RUNTIME_ITEM_APPLY_EVENT_FILE = "runtime_item_apply_event.json"
RUNTIME_RECEIPT_ACK_EVENT_FILE = "runtime_receipt_ack_event.json"
RUNTIME_ITEM_APPLY_STATE_FILE = "runtime_item_apply_state.json"
RUNTIME_ITEM_GRANT_FILE = "runtime_item_grant.json"
RUNTIME_ITEM_GRANT_PRIORITY_FILE = "runtime_item_grant.priority.json"
RUNTIME_ITEM_GRANT_READINESS_FILE = "runtime_item_grant_readiness.json"
RUNTIME_ITEM_GRANT_STATE_FILE = "runtime_item_grant_state.json"
RUNTIME_ITEM_LATENCY_TRACE_FILE = "runtime_item_latency_trace.jsonl"
RUNTIME_KEYBLADE_CHESTS_FILE = "runtime_keyblade_chests.json"
RUNTIME_LOOT_EVENT_FILE = "runtime_loot_event.json"
RUNTIME_MAP_READY_FILE = "runtime_map_ready.json"
RUNTIME_MOM_CLEAR_STATE_FILE = "runtime_mom_clear_state.json"
RUNTIME_MUSIC_CHESTS_FILE = "runtime_music_chests.json"
RUNTIME_SORA_STATE_FILE = "runtime_sora_state.json"
SLOT_DATA_FILE = "slot_data.json"
NATIVE_SAVE_RECEIPTS_FILE = "runtime_save_receipts.json"
NATIVE_ITEM_DESCRIPTIONS_FILE = "runtime_item_descriptions.json"
NATIVE_RUNTIME_STATUS_FILE = "runtime_status.json"
NATIVE_SECRET_REPORT_HINTS_FILE = "runtime_secret_report_hints.json"

SHARED_SIDECAR_FILENAMES = frozenset(
    {
        AP_ITEMS_FILE,
        AP_STATUS_FILE,
        BRIDGE_NOTIFICATIONS_FILE,
        BRIDGE_RUNTIME_STATUS_FILE,
        GAME_STATE_FILE,
        ITEM_DELIVERY_FILE,
        ITEM_DELIVERY_LEDGER_FILE,
        ITEM_DELIVERY_STATE_FILE,
        LUCKY_MARK_NATIVE_AP_RESOLVER_DIAGNOSTICS_FILE,
        RUNTIME_AP_MESSAGE_FILE,
        RUNTIME_BLOCKING_UI_STATE_FILE,
        RUNTIME_CLASSIC_KINGDOM_STATE_FILE,
        RUNTIME_INGREDIENT_STATE_FILE,
        RUNTIME_ADVERSARY_STATE_FILE,
        "runtime_minigame_rewards.json",
        "runtime_space_checks.json",
        "runtime_vbonus_checks.json",
        "vbonus_progress.json",
        "event_progress.json",
        "chest_progress.json",
        "runtime_event_checks.json",
        "runtime_chest_checks.json",
        "runtime_lucky_mark_checks.json",
        "runtime_workshop.json",
        RUNTIME_OLYMPUS_COLISEUM_STATE_FILE,
        RUNTIME_CONTEXT_FILE,
        RUNTIME_CURRENT_SAVE_SLOT_FILE,
        RUNTIME_DEATH_RETRY_REPLAY_WINDOW_FILE,
        RUNTIME_DEATHLINK_STATE_FILE,
        "runtime_trap_queue_request.json",
        "runtime_trap_queue_ack.json",
        "runtime_trap_queue_state.json",
        "runtime_reconciliation_state.json", "runtime_receipt_catalog.json", "runtime_save_receipts.json",
        "runtime_death_retry_baseline.json", "runtime_display_save_slot.json",
        RUNTIME_GRANT_CPP_PROFILE_FILE,
        RUNTIME_GRANT_PROFILE_FILE,
        RUNTIME_ITEM_APPLY_EVENT_FILE,
        RUNTIME_RECEIPT_ACK_EVENT_FILE,
        "item_delivery_ledger_summary.json",
        "item_consumer_plan.json",
        RUNTIME_ITEM_APPLY_STATE_FILE,
        RUNTIME_ITEM_GRANT_FILE,
        RUNTIME_ITEM_GRANT_PRIORITY_FILE,
        RUNTIME_ITEM_GRANT_STATE_FILE,
        RUNTIME_ITEM_GRANT_READINESS_FILE,
        RUNTIME_ITEM_LATENCY_TRACE_FILE,
        RUNTIME_KEYBLADE_CHESTS_FILE,
        RUNTIME_LOOT_EVENT_FILE,
        "runtime_notification_event.json",
        "runtime_check_state.json",
        RUNTIME_MAP_READY_FILE,
        RUNTIME_MOM_CLEAR_STATE_FILE,
        RUNTIME_MUSIC_CHESTS_FILE,
        RUNTIME_SORA_STATE_FILE,
        SLOT_DATA_FILE,
        NATIVE_SAVE_RECEIPTS_FILE,
        NATIVE_ITEM_DESCRIPTIONS_FILE,
        NATIVE_RUNTIME_STATUS_FILE,
        NATIVE_SECRET_REPORT_HINTS_FILE,
    }
)


def bridge_sidecar_path(bridge_dir: Path, filename: str) -> Path:
    """Return a sidecar path without resolving or creating the bridge directory."""
    return bridge_dir / filename
