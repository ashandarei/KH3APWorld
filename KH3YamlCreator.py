# SPDX-License-Identifier: GPL-3.0-only
"""Source entry point for the native KH3 player YAML editor."""

import json
import multiprocessing
import os
from pathlib import Path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.environ["AP_WORLD_LOAD_PATHS"] = json.dumps([str(Path(__file__).parent / "worlds" / "kh3")])
    import Utils
    from worlds.kh3.gui.yaml_creator import launch

    Utils.init_logging("KH3YamlCreator")
    launch()
