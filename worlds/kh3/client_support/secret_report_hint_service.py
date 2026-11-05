"""Publish KH3 Secret Report contents to Archipelago's persistent hint tracker."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class SecretReportHintCallbacks:
    ready: Callable[[], bool]
    payload: Callable[[str], dict[str, Any] | None]
    send: Callable[[list[dict[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class SecretReportHintPlan:
    """CreateHints packets and report state produced by one bridge observation."""

    messages: tuple[dict[str, Any], ...] = ()
    report_numbers: frozenset[int] = frozenset()
    newly_missing_report_numbers: frozenset[int] = frozenset()
    target_count: int = 0


class SecretReportHintService:
    """Publish the DLL's revealed reports; track only successful AP sends."""

    def __init__(self) -> None:
        self.sent_report_numbers: set[int] = set()
        self.warned_missing_report_numbers: set[int] = set()

    def reset_for_connection(self) -> None:
        """Replay applied reports after reconnect; the AP server deduplicates existing hints."""
        self.sent_report_numbers.clear()
        self.warned_missing_report_numbers.clear()

    def mark_sent(self, report_numbers: Iterable[int]) -> None:
        self.sent_report_numbers.update(int(number) for number in report_numbers)

    async def publish_pending(self, callbacks: SecretReportHintCallbacks) -> None:
        if not callbacks.ready():
            return
        plan = self.plan(callbacks.payload("runtime_secret_report_hints.json"))
        if plan.newly_missing_report_numbers:
            logger.warning(
                "Installed KH3 seed pak does not contain AP hint targets for Secret Report(s) %s; "
                "regenerate and reinstall this seed pak to enable hint-tracker publication.",
                ", ".join(str(number) for number in sorted(plan.newly_missing_report_numbers)),
            )
        if not plan.report_numbers:
            return
        if plan.messages:
            await callbacks.send(list(plan.messages))
        self.mark_sent(plan.report_numbers)
        logger.info(
            "Published %s AP hint target(s) from Secret Report(s) %s.",
            plan.target_count,
            ", ".join(str(number) for number in sorted(plan.report_numbers)),
        )

    def plan(self, snapshot: dict[str, Any] | None) -> SecretReportHintPlan:
        if (not isinstance(snapshot, dict) or snapshot.get("schema") != "kh3ap.secret_report_hints.v1"
                or snapshot.get("owner") != "dll" or snapshot.get("error")):
            return SecretReportHintPlan()
        report_targets = snapshot.get("reports")
        missing = snapshot.get("missing_report_numbers")
        if not isinstance(report_targets, dict) or not isinstance(missing, list):
            return SecretReportHintPlan()
        missing_reports = {number for number in missing if type(number) is int and 1 <= number <= 13}
        newly_missing = missing_reports - self.warned_missing_report_numbers - self.sent_report_numbers
        self.warned_missing_report_numbers.update(newly_missing)
        recognized_reports: set[int] = set()
        locations_by_player: dict[int, set[int]] = {}
        for key, targets in report_targets.items():
            if not isinstance(key, str) or key not in {str(number) for number in range(1, 14)}:
                continue
            report_number = int(key)
            if report_number in self.sent_report_numbers or not isinstance(targets, list):
                continue
            if any(not isinstance(target, dict) or any(type(target.get(field)) is not int or target[field] <= 0
                                                      for field in ("location", "player")) for target in targets):
                continue
            recognized_reports.add(report_number)
            for target in targets:
                locations_by_player.setdefault(target["player"], set()).add(target["location"])
        messages = tuple(
            {"cmd": "CreateHints", "locations": sorted(locations), "player": player}
            for player, locations in sorted(locations_by_player.items()) if locations
        )
        return SecretReportHintPlan(
            messages=messages,
            report_numbers=frozenset(recognized_reports),
            newly_missing_report_numbers=frozenset(newly_missing),
            target_count=sum(len(locations) for locations in locations_by_player.values()),
        )


__all__ = ["SecretReportHintCallbacks", "SecretReportHintPlan", "SecretReportHintService"]
