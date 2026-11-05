"""Client latency diagnostics consume completed DLL fields."""
from datetime import datetime, timedelta, timezone

from ..bridge_support import runtime_ack


def test_latency_preserves_missing_and_negative_measurements():
    now = datetime(2026, 9, 28, 17, tzinfo=timezone.utc)
    before = (now - timedelta(seconds=1)).isoformat()
    result = runtime_ack.latency_record({"indices": [1], "applied_at": before}, {}, queued_at_utc=now.isoformat(), processed_at=now)
    assert result["queue_to_runtime_apply_ms"] == -1000
    assert result["runtime_apply_to_ack_ms"] == 1000
    result = runtime_ack.latency_record({"indices": []}, {}, queued_at_utc=None, processed_at=now)
    assert result["queue_to_ack_ms"] is None and result["runtime_apply_to_ack_ms"] is None
