from __future__ import annotations

import asyncio
from typing import Any


def run_kh3_gui(ctx: Any) -> None:
    from .gui.manager import KH3Manager

    ctx.ui = KH3Manager(ctx)
    ctx.ui_task = asyncio.create_task(ctx.ui.async_run(), name="UI")
