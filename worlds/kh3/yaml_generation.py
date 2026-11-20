# SPDX-License-Identifier: GPL-3.0-only
"""Generate a multiworld and host its archive with the active Archipelago installation."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import yaml

import Utils
from worlds.LauncherComponents import get_exe
from . import KH3_WORLD_LOAD_PATHS_ENV


@dataclass(frozen=True)
class GenerationSettings:
    spoiler: int
    hint_cost: int
    release_mode: str
    collect_mode: str
    remaining_mode: str

    @classmethod
    def from_installation(cls):
        settings = Utils.get_settings()
        server = settings.server_options
        return cls(int(settings.generator.spoiler), int(server.hint_cost), str(server.release_mode),
                   str(server.collect_mode), str(server.remaining_mode))

    def server_options(self):
        return {"hint_cost": self.hint_cost, "release_mode": self.release_mode,
                "collect_mode": self.collect_mode, "remaining_mode": self.remaining_mode}


def _command(component):
    command = get_exe(component)
    if not command or not Path(command[-1]).is_file():
        raise FileNotFoundError(f"Could not find the Archipelago {component} component.")
    command = list(command)
    if Path(command[0]).name.lower() == "pythonw.exe":
        command[0] = str(Path(command[0]).with_name("python.exe"))
    return command


def _environment():
    env = os.environ.copy()
    env.pop(KH3_WORLD_LOAD_PATHS_ENV, None)
    return env


def _failed(action, log):
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 1800))
        details = stream.read().decode("utf-8", errors="replace").strip()
    return RuntimeError(f"{action}\n\n{details}\n\nLog: {log}")


def generate_archive(content, additional_files, settings=None):
    if content is None and not additional_files:
        raise ValueError("Add at least one player YAML.")
    command = _command("Generate")
    output = Path(Utils.output_path()).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kh3-yamls-") as staging:
        cwd = Utils.local_path()
        if settings is not None:
            command.extend(["--spoiler", str(settings.spoiler)])
            source = Utils.get_settings().filename
            if source:
                cwd = str(Path(source).resolve().parent)
            server_options = settings.server_options()
            if server_options != GenerationSettings.from_installation().server_options():
                # Generate bakes server options from host.yaml in its working directory.
                # Keep unrelated world settings and give this run its own config.
                config = {}
                if source:
                    config = Utils.parse_yaml(Path(source).read_text(encoding="utf-8-sig")) or {}
                config.setdefault("server_options", {}).update(server_options)
                (Path(staging) / "host.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
                cwd = staging
        players = Path(staging) / "Players"
        players.mkdir()
        if content is not None:
            (players / "0000-current.yaml").write_text(content, encoding="utf-8")
        for path in additional_files:
            # Generate uses the filename for players who omit their name.
            name = path.name.lstrip(".")
            target = players / name
            number = 2
            while target.exists():
                target = players / f"{Path(name).stem}-{number}{path.suffix}"
                number += 1
            shutil.copyfile(path, target)

        run = Path(tempfile.mkdtemp(prefix="KH3-", dir=output))
        log = run / "generation.log"
        # Scope inputs to this selection even if host.yaml has a global weights
        # or meta file. A dropped weights.yaml or meta.yaml is a player file.
        with log.open("wb") as stream:
            result = subprocess.run([
                *command, "--player_files_path", str(players), "--outputpath", str(run), "--multi", "1",
                "--weights_file_path", str(Path(staging) / "weights.yaml"),
                "--meta_file_path", str(Path(staging) / "meta.yaml"),
            ], cwd=cwd, env=_environment(), stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise _failed("Generation failed.", log)
        archives = list(run.glob("*.zip"))
        if len(archives) != 1:
            raise _failed("Generation finished without a single output ZIP.", log)
        return archives[0]


def _available_port(host, preferred):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as probe:
        if os.name == "nt":
            # A wildcard bind on Windows can succeed while another room is
            # already listening on the same port on the loopback interface.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, preferred))
        except OSError:
            probe.bind((host, 0))
        return probe.getsockname()[1]


def _launch_server_terminal(command, pid_file):
    if os.name == "nt":
        return subprocess.Popen(command, cwd=Utils.local_path(), env=_environment(),
                                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    if not Utils.is_linux:
        # Keep the existing detached host on platforms without this terminal integration.
        return subprocess.Popen(command, cwd=Utils.local_path(), env=_environment(), start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # A Linux terminal may hand the window off to an existing process. Record
    # the foreground shell's PID, then replace it with the server in that PTY.
    script = 'cd "$1" && echo "$$" > "$2" || exit 1; shift 2; exec "$@"'
    args = ["env", "-u", KH3_WORLD_LOAD_PATHS_ENV, "sh", "-c", script, "kh3-server",
            Utils.local_path(), str(pid_file), *command]
    if not Utils.run_in_terminal(args):
        raise RuntimeError("Could not open a terminal window. Install a terminal such as xterm and try again.")
    return None


def start_server(archive, generation_settings=None):
    settings = Utils.get_settings().server_options
    host = settings.host or ""
    port = _available_port(host, settings.port)
    address = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    command = [*_command("MultiServer"), str(archive), "--port", str(port)]
    if generation_settings is not None:
        for option, value in generation_settings.server_options().items():
            command.extend([f"--{option}", str(value)])
    logs = Utils.user_path("logs")
    with tempfile.TemporaryDirectory(prefix="kh3-server-") as staging:
        pid_file = Path(staging) / "server.pid"
        process = _launch_server_terminal(command, pid_file)
        pid = process.pid if process else None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                break
            if pid is None and pid_file.exists():
                value = pid_file.read_text().strip()
                if value.isdecimal():
                    pid = int(value)
            if pid is not None:
                if process is None:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                try:
                    with socket.create_connection((address, port), timeout=0.5):
                        endpoint = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
                        return f"{endpoint}\nPID {pid}\nServer logs: {logs}"
                except OSError:
                    pass
            time.sleep(0.25)
        else:
            raise RuntimeError(f"The server did not start listening within 90 seconds. Check the terminal.\nLogs: {logs}")
    raise RuntimeError(f"The server closed during startup.\nLogs: {logs}")
