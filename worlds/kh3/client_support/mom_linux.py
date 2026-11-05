"""Native Linux MoM launch lifetime, with no periodic process discovery.

The configured command must stay alive for the game's entire lifetime (a
foreground Wine runner or an exec wrapper, not a fire-and-forget launcher URI).
Linux process IDs never cross the Wine boundary; TCP connection lifetime is the
probe's activity signal. Closing the client also closes that signal.
"""
from __future__ import annotations

import json
import logging
import os
import selectors
import socket
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_active = None


def configuration() -> tuple[Path, list[str], int]:
    if sys.platform != "linux":
        raise RuntimeError("Native MoM tracking requires Linux.")
    try:
        manual = any(key in os.environ for key in ("KH3AP_MOM_DIR", "KH3AP_MOM_COMMAND", "KH3AP_MOM_ACTIVITY_PORT"))
        if manual:
            root = Path(os.environ["KH3AP_MOM_DIR"]).expanduser().resolve(strict=True)
            command = json.loads(os.environ["KH3AP_MOM_COMMAND"])
            port = int(os.environ["KH3AP_MOM_ACTIVITY_PORT"])
        else:
            from .mom_linux_setup import read_json, settings_path
            saved = read_json(settings_path())
            if saved.get("schema") != 1:
                raise ValueError("no saved setup")
            root = Path(saved["game_dir"]).resolve(strict=True)
            command = saved["query"]
            port = saved["port"]
            probe = read_json(Path(saved["probe_config"]))
            if probe.get("port") != port or not saved.get("token") or probe.get("token") != saved["token"]:
                raise ValueError("probe configuration changed")
        if (not isinstance(command, list) or not command
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
                or type(port) is not int or not 1024 <= port <= 65535
                or not (root / "KINGDOM HEARTS Melody of Memory.exe").is_file()):
            raise ValueError("invalid configuration")
    except (KeyError, ValueError, TypeError, OSError) as exc:
        raise RuntimeError("Click Install MoM Mod in the KH3 Config tab to configure MoM launching, or configure the manual "
                           "environment variables described in tools/kh3/MOM_LINUX.md.") from exc
    return root, command, port


def require_closed() -> None:
    # The worker owns its child until the kernel signals exit. No PID lookup,
    # wait timeout loop, or PID-based termination is needed here.
    with _lock:
        if _active is not None:
            raise RuntimeError("Close the current MoM game before launching another challenge.")


def wait_for_exit(exit_signal: socket.socket, connection: socket.socket) -> None:
    """Block on kernel events; connection loss must not launch/restart a game."""
    with selectors.DefaultSelector() as events:
        events.register(exit_signal, selectors.EVENT_READ, "exit")
        events.register(connection, selectors.EVENT_READ, "bridge")
        for key, _ in events.select():
            if key.data == "bridge":
                logging.getLogger("KH3Client").warning(
                    "MoM activity bridge disconnected; reconnect after closing MoM.")
                # Stop advertising activity immediately, but keep ownership of
                # the running game until its actual exit (no duplicate launch).
                connection.close()
                events.unregister(key.fd)
                while not any(k.data == "exit" for k, _ in events.select()):
                    pass
            return


def launch() -> None:
    global _active
    root, command, port = configuration()
    with _lock:
        if _active is not None:
            raise RuntimeError("Close the current MoM game before launching another challenge.")
        # Connect before launch so a missing probe is a visible launch error.
        connection = socket.create_connection(("127.0.0.1", port), timeout=5)
        process = None
        exit_signal = exit_sender = None
        reaper = None
        try:
            environment = None
            cwd = root
            if not any(key in os.environ for key in ("KH3AP_MOM_DIR", "KH3AP_MOM_COMMAND", "KH3AP_MOM_ACTIVITY_PORT")):
                from .mom_linux_setup import read_json, resolve_launch, settings_path
                saved = read_json(settings_path())
                expected = ("KH3AP_MOM_V1 " + saved["token"] + "\n").encode("ascii")
                received = b""
                while len(received) < len(expected):
                    part = connection.recv(len(expected) - len(received))
                    if not part:
                        break
                    received += part
                if received != expected:
                    raise RuntimeError("KH3 activity connection did not match this setup. Restart KH3 or run setup again.")
                command, environment, cwd = resolve_launch(saved)
            connection.settimeout(None)
            exit_signal, exit_sender = socket.socketpair()
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       close_fds=True)
            _active = process

            def reap_child():
                # A blocking wait for our owned child works even in bundled
                # Python builds without os.pidfd_open. EOF wakes the selector.
                try:
                    process.wait()
                finally:
                    exit_sender.close()

            def worker():
                global _active
                try:
                    wait_for_exit(exit_signal, connection)
                finally:
                    connection.close()
                    # Even an observer error must not allow a duplicate launch.
                    reaper.join()
                    exit_signal.close()
                    with _lock:
                        _active = None

            reaper = threading.Thread(target=reap_child, name="KH3MoMLinuxReaper", daemon=True)
            reaper.start()
            threading.Thread(target=worker, name="KH3MoMLinuxLifetime", daemon=True).start()
        except Exception:
            connection.close()
            if process is not None:
                # Only our newly launched child; never terminate a discovered PID.
                process.kill()
                process.wait()
            if reaper is not None and reaper.ident is not None:
                reaper.join()
            elif exit_sender is not None:
                exit_sender.close()
            if exit_signal is not None:
                exit_signal.close()
            _active = None
            raise
