"""Seed identity and scoped save-association support for the KH3 client."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol

import Utils

KH3_SAVE_SEED_GUARD_VERSION = 2
KH3_SAVE_SEED_ASSOCIATIONS_KEY = "save_seed_associations"


class SeedGuardSource(Protocol):
    """The current seed and run identity needed by save association policy."""

    seed_name: str | None
    offline_seed_name: str | None
    kh3_active_connection_profile_run_id: str | None


class SeedGuardService:
    """Own seed identity parsing and persisted save-directory associations."""

    def __init__(self, source: SeedGuardSource) -> None:
        self.source = source

    @staticmethod
    def extract_seed_id(seed_name: Any) -> str | None:
        match = re.search(r"\d{8,}", str(seed_name or ""))
        return match.group(0) if match else None

    def expected_seed_id(self) -> str | None:
        return self.extract_seed_id(self.source.seed_name or getattr(self.source, "offline_seed_name", None))

    @staticmethod
    def save_seed_scope_key(save_dir: Path | str) -> str:
        return os.path.normcase(str(Path(save_dir).expanduser().resolve()))

    @staticmethod
    def save_seed_scope_label(save_dir: Path | str) -> str:
        path = Path(save_dir).expanduser().resolve()
        if path.name.casefold() == "savegames":
            account_root = path.parent
            if account_root.parent.name.casefold() == "steam":
                return account_root.name
        return str(path)

    @classmethod
    def load_save_seed_associations(cls, persistent_kh3: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_associations = persistent_kh3.get(KH3_SAVE_SEED_ASSOCIATIONS_KEY)
        if not isinstance(raw_associations, dict):
            return {}

        associations: dict[str, dict[str, Any]] = {}
        for raw_key, raw_record in raw_associations.items():
            key = os.path.normcase(str(raw_key or "").strip())
            if not key:
                continue
            if isinstance(raw_record, dict):
                seed_id = cls.extract_seed_id(raw_record.get("seed_id"))
                run_id = str(raw_record.get("run_id") or "").strip()
                try:
                    guard_version = int(raw_record.get("guard_version", 0) or 0)
                except (TypeError, ValueError):
                    guard_version = 0
            else:
                seed_id = cls.extract_seed_id(raw_record)
                run_id = ""
                guard_version = 0
            if seed_id:
                record = {
                    "seed_id": seed_id,
                    "guard_version": guard_version,
                }
                if run_id:
                    record["run_id"] = run_id
                associations[key] = record
        return associations

    def save_seed_association_for_dir(
        self,
        save_dir: Path | str,
        persistent_kh3: dict[str, Any] | None = None,
    ) -> tuple[str | None, int]:
        if persistent_kh3 is None:
            persistent_kh3 = Utils.persistent_load().get("kh3_client", {})
        associations = self.load_save_seed_associations(persistent_kh3)
        record = associations.get(self.save_seed_scope_key(save_dir))
        if not isinstance(record, dict):
            return None, 0
        seed_id = self.extract_seed_id(record.get("seed_id"))
        try:
            guard_version = int(record.get("guard_version", 0) or 0)
        except (TypeError, ValueError):
            guard_version = 0
        return seed_id, guard_version

    @classmethod
    def legacy_run_platforms(cls, persistent_kh3: dict[str, Any]) -> dict[str, str]:
        """Infer old record ownership only when every recorded save scope agrees."""
        owners: dict[str, set[str]] = {}
        for scope, record in cls.load_save_seed_associations(persistent_kh3).items():
            run_id = str(record.get("run_id") or "")
            if not run_id:
                continue
            parts = scope.replace("\\", "/").casefold().split("/")
            platform = "steam" if "steam" in parts else "egs" if "epic games store" in parts else ""
            owners.setdefault(run_id, set()).add(platform)
        return {
            run_id: next(iter(platforms))
            for run_id, platforms in owners.items()
            if len(platforms) == 1 and "" not in platforms
        }

    def save_run_association_for_dir(
        self,
        save_dir: Path | str,
        persistent_kh3: dict[str, Any] | None = None,
    ) -> str | None:
        if persistent_kh3 is None:
            persistent_kh3 = Utils.persistent_load().get("kh3_client", {})
        associations = self.load_save_seed_associations(persistent_kh3)
        record = associations.get(self.save_seed_scope_key(save_dir))
        if not isinstance(record, dict):
            return None
        return str(record.get("run_id") or "").strip() or None

    def store_save_seed_associations_for_targets(
        self,
        targets: list[Path],
        seed_id: str,
        run_id: str | None = None,
    ) -> None:
        persistent_kh3 = Utils.persistent_load().get("kh3_client", {})
        associations = self.load_save_seed_associations(persistent_kh3)
        normalized_run_id = str(
            run_id or getattr(self.source, "kh3_active_connection_profile_run_id", "") or ""
        ).strip()
        for target in targets:
            record = {
                "seed_id": seed_id,
                "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
            }
            if normalized_run_id:
                record["run_id"] = normalized_run_id
            associations[self.save_seed_scope_key(target)] = record
        Utils.persistent_store("kh3_client", KH3_SAVE_SEED_ASSOCIATIONS_KEY, associations)
        # Retain the legacy keys for archive labels and older client versions.
        Utils.persistent_store("kh3_client", "last_save_seed_id", seed_id)
        Utils.persistent_store("kh3_client", "last_save_seed_guard_version", KH3_SAVE_SEED_GUARD_VERSION)

    def clear_save_seed_associations_for_targets(
        self,
        targets: list[Path],
        *,
        expected_run_id: str | None = None,
    ) -> None:
        """Forget deleted active-run ownership without changing any other save folder's association."""

        persistent_kh3 = Utils.persistent_load().get("kh3_client", {})
        associations = self.load_save_seed_associations(persistent_kh3)
        normalized_run_id = str(expected_run_id or "").strip()
        changed = False
        for target in targets:
            scope = self.save_seed_scope_key(target)
            record = associations.get(scope)
            if not isinstance(record, dict):
                continue
            stored_run_id = str(record.get("run_id") or "").strip()
            if normalized_run_id and stored_run_id and stored_run_id != normalized_run_id:
                continue
            associations.pop(scope, None)
            changed = True
        if changed:
            Utils.persistent_store("kh3_client", KH3_SAVE_SEED_ASSOCIATIONS_KEY, associations)


__all__ = [
    "KH3_SAVE_SEED_ASSOCIATIONS_KEY",
    "KH3_SAVE_SEED_GUARD_VERSION",
    "SeedGuardService",
]
