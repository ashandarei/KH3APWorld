"""UTC parsing, formatting, and injectable clock helpers for the KH3 bridge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class UtcClock(Protocol):
    """Callable clock used by bridge services that persist UTC timestamps."""

    def __call__(self) -> datetime: ...


def system_utc_now() -> datetime:
    """Read the current system time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize an aware datetime to UTC, treating a naive value as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_now(clock: UtcClock = system_utc_now) -> datetime:
    """Read and normalize an injectable clock value."""
    return as_utc(clock())


def format_utc_datetime(value: datetime) -> str:
    """Format a datetime using the bridge's existing ISO-8601 UTC representation."""
    return as_utc(value).isoformat()


def utc_now_text(clock: UtcClock = system_utc_now) -> str:
    """Read an injectable clock and format it for persisted bridge payloads."""
    return format_utc_datetime(utc_now(clock))


def parse_utc_datetime(value: object) -> datetime | None:
    """Parse a bridge ISO-8601 timestamp, returning ``None`` for invalid input."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_utc(parsed)
