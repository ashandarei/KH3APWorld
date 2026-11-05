"""On-demand Heroic authentication for the owned Linux Epic KH0.2 companion.

Exchange codes travel only through the existing authenticated local IPC session.
They are never persisted, logged, or reused after a disconnect.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

from ..platform_support.game_launch import _linux_clean_launch_env
from ..transport.pipe_transport import transport_for
from . import installation
from .linux_source_access import source_executable_for_host

REQUEST = "runtime_kh02_launch_request.json"
RESPONSE = "runtime_kh02_launch_response.json"
HEROIC_APP = "com.heroicgameslauncher.hgl"
ARGUMENT_KEYS = frozenset({"-AUTH_LOGIN", "-AUTH_PASSWORD", "-AUTH_TYPE", "-epicapp", "-epicenv",
                           "-EpicPortal", "-epicusername", "-epicuserid", "-epiclocale", "-epicsandboxid"})
LOG = logging.getLogger("Client")


def _legendary_command(config: Path, flatpak: bool) -> list[str]:
    if flatpak:
        binary = shutil.which("flatpak")
        if binary:
            return [binary, "run", "--env=LEGENDARY_CONFIG_PATH=" + str(config),
                    "--command=/app/bin/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
                    HEROIC_APP]
    else:
        candidates = []
        if executable := shutil.which("legendary"):
            candidates.append(Path(executable))
        if heroic := shutil.which("heroic"):
            candidates.append(Path(heroic).resolve().parent / "resources/app.asar.unpacked/build/bin/x64/linux/legendary")
        candidates.extend(Path(root) / "resources/app.asar.unpacked/build/bin/x64/linux/legendary"
                          for root in ("/opt/Heroic", "/usr/lib/heroic"))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return [str(candidate)]
    raise RuntimeError("Heroic's Epic launcher is unavailable. Install Heroic and sign in to Epic.")


def _validated_arguments(plan: dict, app_id: str) -> list[str]:
    arguments = plan.get("egl_parameters")
    if not isinstance(arguments, list) or not 1 <= len(arguments) <= 16:
        raise ValueError("Invalid Epic launch response")
    fields = {}
    for argument in arguments:
        if not isinstance(argument, str) or len(argument) > 2048 or any(ch in argument for ch in "\r\n\0"):
            raise ValueError("Invalid Epic launch argument")
        key, separator, value = argument.partition("=")
        if key not in ARGUMENT_KEYS or key in fields or (key != "-EpicPortal" and not separator):
            raise ValueError("Unexpected Epic launch argument")
        fields[key] = value
    if (fields.get("-epicapp") != app_id or fields.get("-AUTH_TYPE") != "exchangecode"
            or not fields.get("-AUTH_PASSWORD") or "-EpicPortal" not in fields
            or len(json.dumps(arguments).encode()) > 7000):
        raise ValueError("Authenticated Epic launch data unavailable")
    return arguments


def authenticated_arguments(source: installation.Installation) -> list[str]:
    """Ask Heroic's bundled Legendary for a fresh plan, without launching a game."""
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    configs = ((config_home / "heroic/legendaryConfig/legendary", False),
               (home / ".var/app" / HEROIC_APP / "config/heroic/legendaryConfig/legendary", True))
    for config, flatpak in configs:
        try:
            installed = json.loads((config / "installed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(installed, dict):
            continue
        for app_id, entry in installed.items():
            if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", app_id) or not isinstance(entry, dict):
                continue
            root_text = entry.get("install_path")
            if not isinstance(root_text, str) or not Path(root_text).is_absolute():
                continue
            root = Path(root_text).resolve()
            if root != source.root.parent.resolve():
                continue
            relative_executable = source.executable.relative_to(root).as_posix()
            command = _legendary_command(config, flatpak)
            environment = _linux_clean_launch_env()
            environment["LEGENDARY_CONFIG_PATH"] = str(config)
            # Backend output may contain an exchange code, even on failure.
            # Capture it and report only our fixed, actionable error below.
            try:
                result = subprocess.run(command + ["launch", app_id, "--json", "--override-exe", relative_executable],
                                        capture_output=True, text=True, env=environment, timeout=40, check=False)
                if result.returncode:
                    raise ValueError("Epic launch plan failed")
                return _validated_arguments(json.loads(result.stdout), app_id)
            except Exception:
                raise RuntimeError("Epic KH0.2 authentication failed. Sign in to Epic in Heroic and retry.") from None
    raise RuntimeError("The selected Epic KH0.2 installation is not registered in Heroic.")


def _prepare_arguments(ctx, request: dict) -> list[str]:
    from ..installer_support.paths import wine_visible_path_text
    from ..platform_support.game_discovery import normalize_kh3_root_dir
    source = installation.selected_install()
    if os.name == "nt" or source is None or source.platform != "egs" or request.get("source_platform") != "egs":
        raise RuntimeError("Select the Epic KH0.2 source in the KH3 client.")
    source = installation.inspect_install(source.root, verify=True)
    executable = source_executable_for_host(normalize_kh3_root_dir(ctx.kh3_dir), source.executable)
    if request.get("source_executable") != wine_visible_path_text(executable):
        raise RuntimeError("The KH0.2 source selection changed. Restart KH3 before trying again.")
    return authenticated_arguments(source)


class StoreLaunchService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.task = None
        self.binding = None

    @property
    def selection_revision(self):
        return installation.selection_revision

    async def observe(self):
        root = self.ctx.bridge_dir
        transport = transport_for(root / REQUEST) if root is not None else None
        request = transport.read(REQUEST) if transport is not None and transport.connected else None
        request_id = request.get("request_id") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+", request_id):
            request = None
        binding = (transport, getattr(transport, "connection_generation", None), request_id,
                   self.selection_revision) if request else None
        if binding == self.binding:
            return
        self.close()
        self.binding = binding
        if request:
            self.task = asyncio.create_task(self._respond(root, transport, request, binding))

    async def _respond(self, root, transport, request, binding):
        response = {"request_id": request["request_id"], "status": "failed"}
        try:
            arguments = await asyncio.to_thread(_prepare_arguments, self.ctx, request)
            response.update(status="ready", arguments=arguments)
        except asyncio.CancelledError:
            return
        except Exception:
            # Never log exception text from a credential-bearing subprocess.
            LOG.warning("Could not authenticate Epic KH0.2. Check the selected source and sign in to Epic in Heroic.")
        with transport.lock:
            if (self.ctx.bridge_dir == root and transport.connected and self.binding == binding
                    and transport.connection_generation == binding[1] and self.selection_revision == binding[3]
                    and transport.read(REQUEST) == request):
                transport.write(RESPONSE, response)

    def close(self):
        if self.task is not None:
            self.task.cancel()
        self.task = None
        self.binding = None
