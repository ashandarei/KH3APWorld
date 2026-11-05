"""Export cooked seed values for passive KH3 stat and equipment comparisons."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import types


WORLD_ROOT = Path(__file__).resolve().parents[2] / "worlds" / "kh3"


def read_expectations(pak_path: Path) -> tuple[dict, dict]:
    # Load the same Python readers without initializing the AP world registry.
    package_name = "kh3_seed_expectation_tools"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(WORLD_ROOT)]
        sys.modules[package_name] = package
    pak_module = importlib.import_module(package_name + ".Pak")
    assets = importlib.import_module(package_name + ".seedgen.assets")
    tables = importlib.import_module(package_name + ".seedgen.tables")
    reader = pak_module.PakReader(pak_path)

    def load(table: str):
        path = "KINGDOM HEARTS III/" + tables.PATHS[table]
        return assets.Asset(reader.read(path + ".uasset"), reader.read(path + ".uexp"))

    metadata = json.loads(reader.read("KINGDOM HEARTS III/Content/Archipelago/kh3_ap_seed_metadata.json"))
    defaults = json.loads((WORLD_ROOT / "randomizer/wwwroot/DefaultKH3.json").read_text(encoding="utf-8-sig"))
    equipment = load("EquipItem")
    abilities = {}
    for row_name, fields in defaults["EquipItem"].items():
        values = assets.get(equipment.rows[row_name], "m_AppendAbility").value
        abilities[row_name] = {field: values[int(field.removeprefix("Ability"))] for field in fields}
    stats = {}
    for logical, physical in {
        "BaseCharStat": "BaseCharStat", "LevelUpStat": "LevelUp",
        "WeaponEnhanceStat": "WeaponEnhance", "EquipItemStat": "EquipItem",
        "FoodItemEffectStat": "FoodItemEffectStat",
    }.items():
        asset = load(physical)
        stats[logical] = {
            row_name: {field: assets.get(asset.rows["Default" if logical == "BaseCharStat" else row_name],
                                       "m_" + field).value for field in fields}
            for row_name, fields in defaults[logical].items()
        }
    return {"seed": metadata["seed"], "source": str(pak_path.resolve()), "abilities": abilities}, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pak", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Directory for equipment.json and stats.json")
    args = parser.parse_args()
    equipment, stats = read_expectations(args.pak)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, payload in (("equipment", equipment), ("stats", stats)):
        path = args.output / (name + ".json")
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
