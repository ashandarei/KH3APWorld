"""Receipt membership in one saved game, independent of delivery order.

Native checkpoints store a contiguous prefix plus a
sparse tail; this is a snapshot, never a union with a subsequently loaded save.
"""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SavedReceipts:
    through: int = -1
    extra: frozenset[int] = frozenset()

    def contains(self, index: int) -> bool:
        return index >= 0 and (index <= self.through or index in self.extra)

    def add(self, indices):
        extra = self.extra | frozenset(i for i in indices if type(i) is int and i > self.through)
        through = self.through
        while through + 1 in extra:
            through += 1
        return SavedReceipts(through, frozenset(i for i in extra if i > through))

    def payload(self):
        return {"version": 1, "through": self.through, "extra": sorted(self.extra)}

    @classmethod
    def read(cls, payload):
        payload = payload if isinstance(payload, Mapping) else {}
        saved = payload.get("saved_receipts")
        if "saved_receipts" in payload:
            # Malformed/future snapshots must not silently claim a scalar range.
            if (not isinstance(saved, Mapping) or type(saved.get("version")) is not int
                    or saved.get("version") != 1 or type(saved.get("through")) is not int
                    or saved["through"] < -1):
                raise ValueError("Invalid saved receipt snapshot")
            extra = saved.get("extra")
            if not isinstance(extra, list) or any(type(i) is not int or i < 0 for i in extra):
                raise ValueError("Invalid saved receipt indices")
            return cls(saved["through"]).add(extra)
        raise ValueError("Missing native saved receipt snapshot")
