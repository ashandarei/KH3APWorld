"""Measure first painted client controls in isolated, fresh Python processes.

Uses temporary game/save/bridge paths by default, with optional read-only
installed-runtime inspection. Saves always remain temporary; no AP connection
or game launch. Dependency installation checks are excluded, as in an installed client.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def worker(ui, *, observe_seconds=0, kh3_dir=None):
    import asyncio
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    sys.path.insert(0, str(REPO))
    import KH3Client  # noqa: F401 - restrict world loading just like the normal entrypoint
    from worlds.kh3.Client import KH3Context

    async def run():
        with TemporaryDirectory() as directory, patch("Utils.persistent_load", return_value={}):
            root = Path(directory)
            ctx = KH3Context(None, None, str(root / "bridge"), integrated_bridge=False,
                             kh3_dir=kh3_dir or str(root / "game"), save_dir=str(root / "saves"))
            from worlds.kh3.gui.manager import KH3Manager
            app = KH3Manager(ctx)
            ctx.ui = app
            app.root.update()
            assert app.connect_button.instate(["!disabled"])
            assert app.launch_button.instate(["!disabled"])
            assert not any(name == "kvui" or name.startswith("kivy") for name in sys.modules)
            print(f"KH3_STARTUP_READY {time.perf_counter()}", flush=True)
            if observe_seconds:
                # Exercise background runtime inspection after paint while measuring
                # actual Tk callbacks. No offline enrollment, server, or game startup.
                gaps = []
                previous = time.perf_counter()
                def heartbeat():
                    nonlocal previous
                    current = time.perf_counter()
                    gaps.append(current - previous)
                    previous = current
                    app.root.after(10, heartbeat)
                app.root.after(10, heartbeat)
                ui_task = asyncio.create_task(app.async_run())
                try:
                    await asyncio.sleep(observe_seconds)
                finally:
                    app.stop()
                    await ui_task
                print("KH3_UI_RESPONSIVENESS " + json.dumps({
                    "max_callback_gap_seconds": max(gaps, default=0),
                    "callback_count": len(gaps),
                    "runtime_inspection_complete": app.views["KH3 Config"].next_status > 0,
                }), flush=True)
            else:
                app.stop()
    asyncio.run(run())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", choices=("native",), default="native")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--observe-seconds", type=float, default=0,
                        help="Measure native UI callback gaps during post-paint background inspection.")
    parser.add_argument("--kh3-dir", help="Inspect this installed runtime; saves remain in a temporary directory.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.observe_seconds < 0 or args.observe_seconds > 20:
        parser.error("--observe-seconds must be between 0 and 20")
    if args.kh3_dir and not args.observe_seconds:
        parser.error("--kh3-dir requires --observe-seconds to exercise runtime inspection")
    if args.worker:
        worker(args.ui, observe_seconds=args.observe_seconds, kh3_dir=args.kh3_dir)
        return
    if args.runs < 1:
        parser.error("--runs must be positive")
    results = {"native": []}
    responsiveness = []
    environment = {**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1"}
    for _ in range(args.runs):
        for ui, timings in results.items():
            started = time.perf_counter()
            command = [sys.executable, "-u", str(Path(__file__).resolve()), "--worker", "--ui", ui,
                       "--observe-seconds", str(args.observe_seconds)]
            if args.kh3_dir:
                command.extend(("--kh3-dir", args.kh3_dir))
            process = subprocess.Popen(
                command,
                cwd=REPO, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                output, _stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _stderr = process.communicate()
                raise RuntimeError(f"{ui} startup timed out:\n{output}") from None
            markers = [line for line in output.splitlines() if line.startswith("KH3_STARTUP_READY ")]
            if process.returncode or len(markers) != 1:
                raise RuntimeError(output)
            timings.append(float(markers[0].split()[1]) - started)
            responsiveness.extend(json.loads(line.split(" ", 1)[1]) for line in output.splitlines()
                                  if line.startswith("KH3_UI_RESPONSIVENESS "))
    report = {ui: {"seconds": timings, "median_seconds": statistics.median(timings)}
              for ui, timings in results.items()}
    if responsiveness:
        report["native"]["post_paint"] = responsiveness
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
