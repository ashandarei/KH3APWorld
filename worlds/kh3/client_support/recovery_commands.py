"""Operator recovery commands over the active live transport (never file mailboxes)."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from .map_jump_destinations import AUTO_PLAYER_START, map_path
from ..transport.pipe_transport import transport_for

GAMEFLOW = "runtime_gameflow_command.json"
WARP = "runtime_warp_command.json"
RESULTS = {GAMEFLOW: "runtime_gameflow_result.json", WARP: "runtime_warp_result.json"}


def gameflow_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("Usage: /gameflow <gameflow_name> [integer value]")
    return name if name.startswith("gameflow_") else f"gameflow_{name}"


def gameflow_target(name: str) -> tuple[str, str]:
    if name.casefold() == "kh02":
        return "kh02", "gameflow_DW"
    if name.casefold().startswith("kh02:"):
        return "kh02", gameflow_name(name[5:])
    return "kh3", gameflow_name(name)


class RecoveryCommandService:
    """One bounded operation, advanced by the client's existing bridge watcher."""

    def __init__(self, ctx, output, *, timeout=90.0):
        self.ctx = ctx
        self.output = output
        self.timeout = timeout
        self.active = None

    def gameflow(self, name: str, value: str = "") -> bool:
        try:
            bank, name = gameflow_target(name)
            if value.casefold() == "reset":
                if bank != "kh02" or name != "gameflow_DW":
                    raise ValueError("Use /gameflow kh02 reset to restart the complete 0.2 chapter from Garden.")
                return self._start(GAMEFLOW, dict(flag_name=name, bank=bank, target=None), "reset")
            if value and not re.fullmatch(r"[+-]?[0-9]+", value):
                raise ValueError("Gameflow values must be integers.")
            number = int(value) if value else None
            if number is not None and not -(2**31) <= number < 2**31:
                raise ValueError("Gameflow values must fit a signed 32-bit integer.")
            if bank == "kh02" and name.casefold() == "gameflow_dw" and number == 0:
                return self._start(GAMEFLOW, dict(flag_name="gameflow_DW", bank=bank, target=None), "reset")
            return self._start(GAMEFLOW, dict(flag_name=name, bank=bank, target=number), "read")
        except ValueError as exc:
            self.output(str(exc))
            return False

    def mapjump(self, name: str, playerstart: str = "") -> bool:
        try:
            if not name or (playerstart and not re.fullmatch(r"[A-Za-z0-9_.-]+", playerstart)):
                raise ValueError("Usage: /mapjump <mapname> [playerstart]")
            automatic = not playerstart or playerstart == AUTO_PLAYER_START
            return self._start(WARP, dict(map_name=map_path(name),
                target_tag=playerstart or AUTO_PLAYER_START, automatic_start=automatic), "warp")
        except (ValueError, OSError, KeyError) as exc:
            self.output(str(exc))
            return False

    def _start(self, channel, details, phase):
        self.poll()
        if self.active:
            self.output("A KH3 recovery command is already pending. Wait for its result.")
            return False
        root = self.ctx.bridge_dir
        transport = transport_for(Path(root) / channel) if root is not None else None
        if transport is None:
            self.output("KH3 recovery requires an initialized bridge and a running, connected game.")
            return False
        with transport.lock:
            if not transport.connected or transport.closed:
                self.output("KH3 is not connected to the live bridge. Start the game and wait for it to connect.")
                return False
            context = transport.read("runtime_context.json") or {}
            pid = context.get("process_id")
            if context.get("recovery_protocol") != 1 or type(pid) is not int or pid <= 0:
                self.output("The running KH3 probe does not support client recovery commands. Update it and restart KH3.")
                return False
            if details.get("bank") == "kh02" and context.get("kh02_gameflow_protocol") != 1:
                self.output("The running probe lacks 0.2 gameflow support. Install the updated KH3 probe and restart KH3.")
                return False
            if details.get("automatic_start") and context.get("mapjump_auto_start_protocol") != 1:
                self.output("Automatic PlayerStart selection requires the updated KH3 probe. Install it and restart KH3, or supply an explicit PlayerStart tag.")
                return False
            self.active = dict(
                channel=channel, transport=transport, root=Path(root).absolute(),
                epoch=transport.epoch, generation=transport.connection_generation,
                pid=pid, deadline=time.monotonic() + self.timeout,
                expires=int((time.time() + self.timeout) * 1000), **details,
            )
            self._send(phase)
        self.output("KH3 recovery command queued; waiting for the game.")
        return True

    def _send(self, phase):
        op = self.active
        op.update(phase=phase, request_id=str(uuid.uuid4()), waiting=None)
        request = dict(request_schema="kh3ap.runtime_control.v1", request_id=op["request_id"],
                       process_id=op["pid"], expires_unix_ms=op["expires"])
        if phase == "warp":
            request.update(map_name=op["map_name"], target_tag=op["target_tag"], fade_kind="fadeout")
        else:
            request.update(command={"set": "set_game_flag", "reset": "reset_gameflow"}.get(phase, "get_game_flag"),
                           flag_name=op["flag_name"])
            if op["bank"] == "kh02":
                request["flag_bank"] = "kh02"
            if phase == "set":
                request["flag_value"] = op["target"]
        op["transport"].write(op["channel"], request)

    def cancel(self, reason="Client shutting down"):
        op = self.active
        if op is None:
            return
        transport = op["transport"]
        with transport.lock:
            request = transport.read(op["channel"]) or {}
            if request.get("request_id") == op["request_id"]:
                transport.delete(op["channel"])
        self.active = None
        self.output(f"KH3 recovery stopped: {reason}. It will not be retried automatically; "
                    "a dispatched action may already have taken effect.")

    def poll(self):
        op = self.active
        if op is None:
            return
        transport = op["transport"]
        root = self.ctx.bridge_dir
        with transport.lock:
            if (root is None or Path(root).absolute() != op["root"]
                    or transport_for(op["root"] / op["channel"]) is not transport
                    or not transport.connected or transport.closed
                    or transport.epoch != op["epoch"]
                    or transport.connection_generation != op["generation"]):
                self.cancel("The game connection or bridge changed")
                return
            if time.monotonic() >= op["deadline"]:
                self.cancel("Timed out waiting for the game")
                return
            result = transport.read(RESULTS[op["channel"]]) or {}
            if result.get("request_id") != op["request_id"] or result.get("process_id") != op["pid"]:
                return
            status = result.get("status")
            if status == "waiting":
                reason = result.get("reason", "game not ready")
                if reason != op["waiting"]:
                    self.output(f"KH3 recovery waiting: {reason}.")
                    op["waiting"] = reason
                return
            if status not in {"completed", "failed", "error"}:
                self.cancel("Invalid response from the game")
                return
            # The native handler publishes its result before removing the request.
            # Wait for that removal before sending the next phase into its mailbox.
            if transport.read(op["channel"]) is not None:
                return
            if status != "completed":
                self.cancel(f"Game reported {result.get('reason', status)}")
                return
            phase = op["phase"]
            if op.get("bank") == "kh02" and result.get("flag_bank") != "kh02":
                self.cancel("Game did not confirm the separate 0.2 story bank")
                return
            if phase == "warp":
                start = "automatic PlayerStart" if op.get("automatic_start") else op['target_tag']
                self.output(f"Map jump dispatched: {op['map_name']} -> {start}. "
                            "Wait for the map to finish loading.")
                self.active = None
            elif phase == "reset":
                if (result.get("kh02_reset") is not True or "progress" not in result
                        or result["progress"] is not None or type(result.get("revision")) is not int
                        or result.get("opened_chests_after") != 0
                        or result.get("other_chest_bits_unchanged") is not True):
                    self.cancel("Game did not confirm the complete 0.2 reset")
                else:
                    self.output("0.2 story, visited areas, gears, bosses, challenges and chests reset (verified). "
                                "Enter the 0.2 portal to restart Castle Town; save normally to keep the reset. "
                                "AP checks and received items are preserved.")
                    self.active = None
            elif phase == "set":
                if result.get("game_flag_set") is not True:
                    self.cancel("Game did not confirm the gameflow write")
                else:
                    self._send("verify")
            else:
                value = result.get("flag_value")
                label = ("kh02:" if op.get("bank") == "kh02" else "") + op["flag_name"]
                if (result.get("game_flag_read") is not True or type(value) is not int
                        or result.get("flag_name") != op["flag_name"]):
                    self.cancel("Game did not return a valid gameflow value")
                elif phase == "verify":
                    self.output(f"{label}: {op['original']} -> {value} "
                                + ("(verified)." if value == op["target"]
                                   else f"(requested {op['target']}; readback does not match)."))
                    self.active = None
                elif op["target"] is None or (op["target"] == value and not (
                        op["bank"] == "kh02" and op["flag_name"].casefold() == "gameflow_dw")):
                    self.output(f"{label} = {value}"
                                + (" (already set)." if op["target"] is not None else "."))
                    self.active = None
                else:
                    # Reapplying a 0.2 checkpoint repairs its earlier story
                    # flags, even when the main checkpoint is already equal.
                    op["original"] = value
                    self.output(f"{label}: original value {value}; setting {op['target']}.")
                    self._send("set")
