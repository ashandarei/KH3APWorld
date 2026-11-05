"""Build encounter data and compatibility policy for the runtime data export."""
from __future__ import annotations

from pathlib import Path

from catalog_imports import import_world_module

ROOT = Path(__file__).resolve().parents[2]


def catalog_data() -> dict:
    policy = import_world_module("seedgen.remaps")
    root = ROOT / "worlds/kh3/randomizer"
    rules = policy.enemy_rules(root)
    targets = policy.load(root, "DefaultEnemyRuntimeTargets.json")["targets"]
    groups = []
    for world in policy.load(root, "DefaultEnemies.json").values():
        for name, group in world.items():
            target = targets[name]
            if len(target["actors"]) != len(group["Addresses"]):
                raise ValueError(f"Enemy target count mismatch: {name}")
            groups.append(dict(name=name, path=group["EnemyPath"].rstrip("\0"),
                               package=target["package"], actors=target["actors"],
                               excluded=Path(group["FilePath"].replace("\\", "/")).stem in policy.EXCLUDED_ENEMY_FILES,
                               mini="(Mini-Boss)" in name, boss="(Boss)" in name,
                               data="Data " in name, endgame=policy.endgame(name), yozora="Yozora" in name))
    bosses = {key: value.rstrip("\0") for key, value in policy.load(root, "DefaultBosses.json").items()}
    payload = dict(groups=groups, rules=[dict(path=path, category=category, mini=mini, pinned=pinned)
                                        for path, (category, mini, pinned) in rules.items()],
                   bosses=[dict(id=key, pawn=pawn, path=policy.boss_class(pawn), pinned=key in policy.PINNED_BOSSES,
                                allowed=[other for other, candidate in bosses.items() if policy.boss_compatible(key, candidate)])
                           for key, pawn in bosses.items()])
    return payload


if __name__ == "__main__":
    from export_dll_inputs import main
    main()
