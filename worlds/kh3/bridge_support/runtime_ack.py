"""Pure interpretation and records for native item-grant acknowledgements.

The session owns reads, clocks, request retirement, and publication order.
These helpers never mark items applied or advance a save checkpoint.
"""

from datetime import datetime
from typing import Any

from .time import parse_utc_datetime


def event_key(payload: dict[str, Any]) -> str:
    return payload["event_key"]


def event_indices(payload: dict[str, Any]) -> list[int]:
    return list(payload["indices"])


def action_kind(payload: dict[str, Any]) -> str:
    return str(payload.get("action_kind") or "")


def _latency_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000.0, 3)


def latency_record(
    payload: dict[str, Any], event_record: dict[str, Any], *, queued_at_utc: str | None, processed_at: datetime
) -> dict[str, Any]:
    queued_at = parse_utc_datetime(queued_at_utc)
    applied_at = parse_utc_datetime(payload.get("applied_at"))
    return dict(
        recorded_at_utc=processed_at.isoformat(),
        request_id=payload.get("request_id"),
        action_kind=action_kind(payload),
        indices=event_indices(payload),
        status=event_record.get("status"),
        original_status=event_record.get("original_status"),
        newly_applied_indices=event_record.get("newly_applied_indices", []),
        queued_at_utc=queued_at_utc,
        runtime_applied_at_utc=payload.get("applied_at"),
        runtime_request_wait_ms=payload.get("runtime_request_wait_ms"),
        runtime_grant_apply_elapsed_ms=payload.get("runtime_grant_apply_elapsed_ms"),
        runtime_receipt_commit_elapsed_ms=payload.get("runtime_receipt_commit_elapsed_ms"),
        runtime_receipt_projection_elapsed_ms=payload.get("runtime_receipt_projection_elapsed_ms"),
        runtime_receipt_worker_elapsed_ms=payload.get("runtime_receipt_worker_elapsed_ms"),
        runtime_receipt_batch_count=payload.get("runtime_receipt_batch_count"),
        queue_to_runtime_apply_ms=_latency_ms(queued_at, applied_at),
        runtime_apply_to_ack_ms=_latency_ms(applied_at, processed_at),
        queue_to_ack_ms=_latency_ms(queued_at, processed_at),
    )
