from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worlds.kh3.PakGenerator import KH3PakGenerationError, generate_kh3_paks_from_ap_zip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate KH3 player .pak files from a generated Archipelago AP_*.zip."
    )
    parser.add_argument("ap_zip", type=Path, help="Generated AP_*.zip containing a KH3 slot.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output folder for generated KH3 .pak files. Defaults to kh3-player-paks next to the AP zip.",
    )
    parser.add_argument("--slot", type=int, default=None, help="Generate only the specified KH3 slot.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = generate_kh3_paks_from_ap_zip(args.ap_zip, args.output, args.slot)
    except KH3PakGenerationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = [
        {
            "slot": result.slot,
            "slot_name": result.slot_name,
            "pak_path": str(result.pak_path),
            "spoiler_log_path": str(result.spoiler_log_path) if result.spoiler_log_path else None,
        }
        for result in results
    ]
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for result in payload:
            print(f"{result['slot_name']}: {result['pak_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
