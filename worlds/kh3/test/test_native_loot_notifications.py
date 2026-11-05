"""The client consumes native presentation without issuing gameplay commands."""

import json
from unittest.mock import patch

import pytest

from ..bridge_support.files import atomic_write_json
from ..ExternalBridge import SaveFilesBackend
from ..transport.pipe_transport import SET, register, unregister
from .test_native_reconciliation import native_session


def notification(**overrides):
    return dict(
        schema="kh3ap.notification.v1",
        owner="dll",
        process_id=777,
        seed_name="test-seed",
        slot=1,
        delivery_epoch=4,
        request_id="native-notification-777-1",
        event="runtime_loot",
        status="applied",
        bridge_key="Event:FOREIGN",
        location_name="Story reward",
        item_name="Missile Tank",
        message="AP: Found Samus's Missile Tank (p)",
        **overrides,
    )


@pytest.mark.parametrize("field,value", [("owner", "client"), ("process_id", 778), ("seed_name", "other"), ("slot", 2)])
def test_mismatched_native_notice_is_ignored(tmp_path, field, value):
    session, _ = native_session(tmp_path)
    payload = notification()
    payload[field] = value
    assert session.consume_native_notification_event(payload)["status"] == "ignored"
    assert not (tmp_path / "bridge_notifications.json").exists()


def test_native_notices_keep_fifo_and_ack_only_after_persistence(tmp_path):
    session, native = native_session(tmp_path)
    transport = register(tmp_path)
    try:
        transport.accept([(SET, "runtime_reconciliation_state.json", 90, json.dumps(native).encode())])
        first = notification()
        second = dict(
            first,
            request_id="native-notification-777-2",
            status="regrant_queued",
            message="Regrant queued after rollback: Boss bonus",
        )
        transport.accept(
            [
                (SET, "runtime_notification_event.json", 1, json.dumps(first).encode()),
                (SET, "runtime_notification_event.json", 2, json.dumps(second).encode()),
            ]
        )
        with patch.object(session, "append_bridge_notification", side_effect=OSError("disk unavailable")):
            with pytest.raises(OSError):
                SaveFilesBackend._apply_native_notification_if_present(session)
        assert not transport.acks
        SaveFilesBackend._apply_native_notification_if_present(session)
        SaveFilesBackend._apply_native_notification_if_present(session)
        assert transport.read("runtime_notification_event.json") is None
        assert [row["message"] for row in session.load_bridge_notification_state()["notifications"]] == [
            first["message"],
            second["message"],
        ]
        assert [ack[2] for ack in transport.acks] == [1, 2]
    finally:
        unregister(transport)


def test_regrant_projection_and_catalog_ignore_client_repair_intents(tmp_path):
    session, native = native_session(tmp_path)
    native["pending_regrant_bridge_keys"] = ["VBonus:NATIVE_REPAIR"]
    atomic_write_json(tmp_path / "runtime_reconciliation_state.json", native)
    assert session.native_reconciliation_state()["pending_regrant_bridge_keys"] == ["VBonus:NATIVE_REPAIR"]
    session.sync_native_receipt_catalog()
    catalog = session.store.read_optional_json("runtime_receipt_catalog.json")
    assert "repair_keys" not in catalog
    assert "checked_keys" not in catalog
    assert "server_checked_locations" in catalog
    state_before = session.load_item_delivery_state()
    session.build_item_delivery_payload()
    assert session.native_reconciliation_state()["pending_regrant_bridge_keys"] == ["VBonus:NATIVE_REPAIR"]
    assert session.load_item_delivery_state() == state_before
