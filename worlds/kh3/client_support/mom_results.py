"""Read request-scoped Melody of Memory results without touching game saves.

Callers retain seen event IDs only after applying a result. Reading never deletes
events, sends AP checks, or advances a cursor, so an offline client can catch up.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from uuid import UUID


def _guid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a UUID string")
    return str(UUID(value))


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Expected a timezone-aware timestamp")
    return parsed


class SongResultScan:
    """One poll's directory scan, shared by requests for the same installation.

    Results are indexed by request before challenge-specific validation. Do not
    retain this object across polls: partial files and newly arrived results
    must be read again. Reading never acknowledges an event.
    """

    def __init__(self, bridge_dir: Path, seen_event_ids: Collection[str] = ()):
        self.bridge_dir = Path(bridge_dir)
        self.seen = {_guid(event_id) for event_id in seen_event_ids}
        self._by_request: dict[str, list[tuple[str, dict]]] | None = None

    def records(self, request_id: str) -> list[tuple[str, dict]]:
        if self._by_request is None:
            grouped: dict[str, list[tuple[str, dict]]] = {}
            for path in (self.bridge_dir / "results").glob("*.json"):
                try:
                    event_id = _guid(path.stem)
                    if event_id in self.seen or path.stat().st_size > 65536:
                        continue
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    if not isinstance(data, dict):
                        continue
                    key = _guid(data.get("requestId"))
                    grouped.setdefault(key, []).append((event_id, data))
                except (OSError, ValueError, TypeError, OverflowError):
                    continue
            self._by_request = grouped
        return self._by_request.get(request_id, [])


def read_song_results(
    bridge_dir: Path,
    *,
    request_id: str,
    song_id: int,
    difficulty: int,
    seen_event_ids: Collection[str] = (),
    play_style: str = "Normal",
    scan: SongResultScan | None = None,
) -> list[dict]:
    """Return validated terminal attempts matching an explicitly pending challenge.

    The directory is MoM's UserData/MoMBridge, not the KH3 bridge directory.
    Invalid, partial, unrelated, and previously acknowledged events are ignored.
    Only results with eligibleForChallenge=True should satisfy a song challenge.
    """
    expected_request = _guid(request_id)
    if type(song_id) is not int or not 0 <= song_id <= 0xFFFFFFFF:
        raise ValueError("song_id must be an unsigned 32-bit integer")
    if type(difficulty) is not int or difficulty not in (0, 1, 2):
        raise ValueError("difficulty must be 0, 1 or 2")
    if play_style not in ("Normal", "OneButton", "Performer"):
        raise ValueError("Unknown requested play style")
    seen = {_guid(event_id) for event_id in seen_event_ids}
    found = []
    if scan is None:
        scan = SongResultScan(bridge_dir, seen)
    elif scan.bridge_dir != Path(bridge_dir):
        raise ValueError("Result scan belongs to another MoM bridge directory")
    for file_event_id, source in scan.records(expected_request):
        try:
            if file_event_id in seen:
                continue
            data = dict(source)  # Eligibility is specific to this request's settings.
            if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
                continue
            event_id = _guid(data.get("eventId"))
            if event_id != file_event_id or event_id in seen:
                continue
            if _guid(data.get("requestId")) != expected_request:
                continue
            _guid(data.get("sessionId"))
            if type(data.get("songId")) is not int or data["songId"] != song_id:
                continue
            if type(data.get("difficulty")) is not int or data["difficulty"] != difficulty:
                continue
            outcome = data.get("outcome")
            if outcome not in ("cleared", "failed", "aborted"):
                continue
            if data.get("cleared") is not (outcome == "cleared"):
                continue
            if outcome == "cleared" and data.get("nativeExit") != "Clear":
                continue
            if outcome == "failed" and data.get("nativeExit") != "GameOver":
                continue
            if _time(data.get("finishedAt")) < _time(data.get("startedAt")):
                continue
            # Do not trust a lone completion/eligibility flag. Require the
            # native terminal result and the requested ordinary play settings.
            eligible = (
                outcome == "cleared"
                and data.get("eligibleForChallenge") is True
                and type(data.get("sceneId")) is int and data["sceneId"] == 7
                and data.get("playMode") == play_style
                and all(data.get(flag) is False for flag in ("demoPlay", "autoPlay", "assisted"))
                and type(data.get("startSeekTime")) in (int, float) and data["startSeekTime"] == 0
                and type(data.get("score")) is int and 0 <= data["score"] <= 9999999
            )
            data["eventId"] = event_id
            data["requestId"] = expected_request
            data["eligibleForChallenge"] = eligible
            found.append(data)
            seen.add(event_id)
        except (OSError, ValueError, TypeError, OverflowError):
            continue
    return sorted(found, key=lambda result: (_time(result["finishedAt"]), result["eventId"]))
