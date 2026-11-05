"""Extract authored ingredient generation rates from cooked EGS table dumps.

Input: KH3UAssetTools inspect-properties dumps named TresFoodstuffDropData??.txt.
These rates do not account for story flags or other actor-level spawn conditions.
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    "EGS cooked TresFoodstuffDropDataBX/CA/FZ/HE/MI/RA/TS/TT, m_GenRate. "
    "These are authored generation rates, not measured respawn probabilities. "
    "A rate of 100 does not rule out story or actor-level conditions."
)


def extract_rates(tables: Path, spot_ids: set[str]) -> dict[str, int]:
    rates = {}
    for path in sorted(tables.glob("TresFoodstuffDropData??.txt")):
        for block in re.split(r"^  ROW ", path.read_text(encoding="utf-8-sig"), flags=re.M)[1:]:
            key = block.splitlines()[0].strip()
            if key not in spot_ids:
                continue
            match = re.search(r"^    m_GenRate .*? (\{.*\})$", block, re.M)
            if match is None:
                raise ValueError(f"Missing m_GenRate: {path.name}:{key}")
            rate = json.loads(match[1])["Value"]
            if type(rate) is not int or not 0 <= rate <= 100 or key in rates:
                raise ValueError(f"Invalid or duplicate generation rate: {path.name}:{key}: {rate!r}")
            rates[key] = rate
    missing = spot_ids - rates.keys()
    if missing:
        raise ValueError(f"Missing ingredient table rows: {sorted(missing)}")
    return dict(sorted(rates.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "worlds/kh3/data/ingredient_spawn_rates.json")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "worlds/kh3/data/ingredient_spots.json").read_text())
    rates = extract_rates(args.tables, {spot["id"] for spot in catalog["spots"]})
    args.output.write_text(json.dumps({"schema": 1, "source": SOURCE, "spots": rates}, indent=2)
                           + "\n", encoding="utf-8")
    print(f"{len(rates)} spots; {sum(rate < 100 for rate in rates.values())} below 100")


if __name__ == "__main__":
    main()
