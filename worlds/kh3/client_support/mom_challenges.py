"""Persist seed-scoped MoM launches and turn verified clears into AP checks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..bridge_support.files import atomic_write_json, file_signature
from ..bridge_support.paths import RUNTIME_MOM_CLEAR_STATE_FILE, bridge_sidecar_path
from ..MelodyOfMemory import MOM_LOCATION_ID_BASE, MOM_SONG_BY_ID
from .json_snapshot import freeze_json
from .mom_installation import MOM_EXE, SONG_REQUEST, install_lock
from .mom_results import SongResultScan, read_song_results
from .mom_settings import read_launch_settings

logger = logging.getLogger("Client")
STARTUP_TIMEOUT = 180
PLAYBACK_TIMEOUT = 190


def _read_launch_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def _log_signature(path: Path) -> tuple[int | None, int | None]:
    try:
        return file_signature(path)
    except OSError:
        return None, None


@dataclass
class LaunchObservation:
    request_id: str
    game_dir: Path
    dispatched: float
    log_before: tuple[int | None, int | None]
    step: str | None = None
    accepted: float | None = None
    next_read: float = 0

    def update(self) -> bool:
        """Read launch status only while a dispatched request needs confirmation."""
        now = time.monotonic()
        if now < self.next_read:
            return False
        self.next_read = now + 1
        user_data = self.game_dir / "UserData"
        result = _read_launch_record(user_data / "MoMSongLaunch-result.json")
        if result.get("RequestId") == self.request_id:
            status, step, message = result.get("status"), result.get("step"), result.get("message")
            if status == "ready":
                logger.info("MoM request %s reached verified playback after %.1fs: %s",
                            self.request_id, now - self.dispatched, message)
                return True
            if status == "failed":
                logger.error("MoM request %s failed in the mod at %s: %s", self.request_id, step, message)
                return True
            if status == "pending":
                if self.accepted is None:
                    self.accepted = now
                    logger.info("MoM mod accepted request %s after %.1fs", self.request_id, now - self.dispatched)
                if step != self.step:
                    self.step = step
                    logger.info("MoM request %s startup step %s: %s", self.request_id, step, message)
        deadline = self.dispatched + STARTUP_TIMEOUT if self.accepted is None else self.accepted + PLAYBACK_TIMEOUT
        if now < deadline:
            return False
        loader_log = self.game_dir / "MelonLoader/Latest.log"
        pending = _read_launch_record(self.game_dir / SONG_REQUEST)
        claimed = (user_data / f"MoMSongLaunch-active-{self.request_id}.json").is_file()
        processes = [(process.pid, process.info.get("exe")) for process in _mom_processes()] if os.name == "nt" else []
        logger.warning(
            "MoM request %s did not report %s after %.1fs. Last step: %s. "
            "Pending request: %s. Claim file exists: %s. MoM processes: %s. Loader log changed: %s (%s).",
            self.request_id, "startup" if self.accepted is None else "playback", now - self.dispatched,
            self.step, pending.get("RequestId"), claimed, processes, _log_signature(loader_log) != self.log_before,
            loader_log,
        )
        return True


def challenge_scope(slot_data: dict) -> dict:
    seed = slot_data.get("kh3_seed", {})
    return {key: seed.get(key) for key in ("seed_name", "slot", "slot_name")}


def discover_mom_install() -> tuple[Path, str]:
    if os.name != "nt":
        from .mom_linux import configuration
        game_dir, _command, _port = configuration()
        if not (game_dir / "Mods/MoMController.dll").is_file():
            raise RuntimeError("Install the MoM controller in the configured Linux MoM directory first.")
        return game_dir, "kh3ap-linux-mom"
    from .mom_installation import selected_install
    game_dir, uri = selected_install()
    if not (game_dir / "Mods/MoMController.dll").is_file():
        raise RuntimeError("Click Install MoM Mod in the KH3 Config tab before launching a challenge.")
    return game_dir, uri


def _mom_processes():
    import psutil

    return (process for process in psutil.process_iter(["exe", "name"])
            if (process.info.get("name") or "").casefold() == MOM_EXE.casefold())


def stop_mom(game_dir: Path) -> None:
    if os.name != "nt":
        from .mom_linux import require_closed
        require_closed()
        return
    import psutil

    target = (game_dir / MOM_EXE).resolve()
    for process in _mom_processes():
        executable = process.info.get("exe")
        if not executable or Path(executable).resolve() != target:
            raise RuntimeError("Cannot verify the running MoM installation. Close MoM before launching this song.")
        try:
            logger.info("Stopping prior MoM process %s at %s", process.pid, executable)
            process.terminate()
            process.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        logger.info("Prior MoM process %s exited", process.pid)


class MoMChallengeService:
    def __init__(self, bridge_dir: Path, slot_data: dict):
        self.seed_launch_options = {key: slot_data[key] for key in (
            "melody_of_memory_party", "melody_of_memory_play_style", "melody_of_memory_starting_item")
            if key in slot_data}
        self.scope = challenge_scope(slot_data)
        self.challenges = {}
        for row in slot_data.get("bridge", {}).get("locations", []):
            converted = row.get("type") == "treasure" and row.get("music_chest_kind") == "vanilla"
            if row.get("type") != "melody_of_memory" and not converted:
                continue
            placed = row.get("music_chest_placement_version") == 1
            song_id, difficulty = row.get("mom_song_id"), row.get("mom_difficulty")
            location_id = row.get("ap_location_id")
            if (type(song_id) is not int or song_id not in MOM_SONG_BY_ID
                    or type(difficulty) is not int or difficulty not in range(3)
                    or type(location_id) is not int
                    or (not converted and not placed and location_id != MOM_LOCATION_ID_BASE + song_id)
                    or location_id in self.challenges):
                raise ValueError("Invalid Melody of Memory challenge metadata in this seed.")
            if placed:
                from ..Locations import location_name_to_id
                from ..MelodyOfMemory import placed_music_chest
                expected = placed_music_chest(row.get("music_chest_placement_id"), song_id, difficulty)
                if (location_id != location_name_to_id[expected["name"]]
                        or any(row.get(k) != expected[k] for k in ("source_table", "source_id", "world", "music_chest_kind", "music_chest_map"))
                        or any(not isinstance(row.get(k), (list, tuple))
                               or tuple(row[k]) != tuple(expected[k])
                               for k in ("music_chest_position", "music_chest_rotation"))):
                    raise ValueError("Invalid placed music chest identity.")
            if converted:
                from ..Locations import location_name_to_id
                if location_name_to_id.get(row.get("ap_location_name")) != location_id:
                    raise ValueError("Invalid converted music chest location identity.")
            self.challenges[location_id] = dict(row)
        self.challenges = freeze_json(self.challenges)
        if self.challenges and (not self.scope["seed_name"] or not self.scope["slot"]):
            raise ValueError("MoM challenges require a loaded KH3 seed and slot.")
        identity = {"scope": self.scope, "checks": [
            [i, row["mom_song_id"], row["mom_difficulty"]] for i, row in sorted(self.challenges.items())
        ]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        self.path = Path(bridge_dir) / "mom_challenges" / f"{digest}.json"
        self.state = {"schema_version": 1, "identity": identity, "requests": {}, "cleared": []}
        self._dirty = False
        self._launch_observation: LaunchObservation | None = None
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if (saved.get("schema_version") != 1 or saved.get("identity") != identity
                    or not isinstance(saved.get("requests"), dict)
                    or not isinstance(saved.get("cleared"), list)
                    or any(i not in self.challenges for i in saved["cleared"])):
                raise ValueError(f"Invalid MoM challenge state: {self.path}")
            self.state = saved

    def save(self) -> None:
        self._dirty = True
        atomic_write_json(self.path, self.state)
        self._dirty = False

    def launch(self, location_id: int) -> None:
        challenge = self.challenges[location_id]
        settings = read_launch_settings(self.path.parent.parent, seed_difficulty=challenge["mom_difficulty"],
                                        seed_options=self.seed_launch_options)
        game_dir, uri = discover_mom_install()
        request_path = game_dir / SONG_REQUEST
        request_id = str(uuid4())
        record = {
            "location_id": location_id, "game_dir": str(game_dir), "seen": [],
            "play_style": settings["PlayStyle"], "starting_item": settings["StartingItem"],
            "party": settings["Party"], "difficulty": settings["Difficulty"],
        }
        request = {"RequestId": request_id, "SongId": challenge["mom_song_id"],
                   "FastBoot": True, **settings}
        logger.info("Preparing MoM request %s for location %s, song %s, options %s at %s",
                    request_id, location_id, request["SongId"], settings, game_dir)
        with install_lock(game_dir):
            stop_mom(game_dir)
            self.state["requests"][request_id] = record
            try:
                self.save()  # Persist the binding before the game can emit a result.
            except Exception:
                del self.state["requests"][request_id]
                raise

            # A fresh chest interaction replaces the mailbox in one operation.
            # The previous request's durable binding can still validate results.
            if request_path.exists():
                logger.info("MoM request %s supersedes pending request %s at %s", request_id,
                            _read_launch_record(request_path).get("RequestId") or "(unreadable or incomplete)",
                            request_path)
            atomic_write_json(request_path, request)
            self._launch_observation = None
            logger.info("Published MoM request %s to %s", request_id, request_path)
            observation = LaunchObservation(request_id, game_dir, time.monotonic(),
                                            _log_signature(game_dir / "MelonLoader/Latest.log"))
            try:
                if os.name == "nt":
                    logger.info("Dispatching MoM request %s through Epic (%s)", request_id, uri)
                    try:
                        os.startfile(uri)
                    except FileNotFoundError as exc:
                        raise RuntimeError("Windows could not open the Epic Games Launcher link for MoM. "
                                           "Open Epic Games Launcher and verify that Melody of Memory launches "
                                           "from your library.") from exc
                else:
                    from .mom_linux import launch
                    logger.info("Dispatching MoM request %s through the Linux runner", request_id)
                    launch()
            except Exception:
                if _read_launch_record(request_path).get("RequestId") == request_id:
                    try:
                        request_path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Could not remove failed MoM request %s at %s", request_id, request_path,
                                       exc_info=True)
                raise
            self._launch_observation = observation
            logger.info("MoM request %s dispatched. Waiting for the mod to confirm startup.", request_id)

    def poll(self) -> set[int]:
        if self._launch_observation is not None and self._launch_observation.update():
            self._launch_observation = None
        cleared = set(self.state["cleared"])
        seen_by_directory: dict[Path, set[str]] = {}
        for record in self.state["requests"].values():
            if record["location_id"] not in self.challenges or record["location_id"] in cleared:
                continue
            directory = Path(record["game_dir"]) / "UserData/MoMBridge"
            seen_by_directory.setdefault(directory, set()).update(record["seen"])
        scans = {directory: SongResultScan(directory, seen) for directory, seen in seen_by_directory.items()}
        for request_id, record in self.state["requests"].items():
            location_id = record["location_id"]
            if location_id not in self.challenges or location_id in cleared:
                continue
            challenge = self.challenges[location_id]
            game_dir = Path(record["game_dir"])
            events = read_song_results(
                game_dir / "UserData/MoMBridge", request_id=request_id,
                song_id=challenge["mom_song_id"], difficulty=record["difficulty"],
                seen_event_ids=record["seen"],
                play_style=record.get("play_style", "Normal"),
                scan=scans[game_dir / "UserData/MoMBridge"],
            )
            for event in events:
                self._dirty = True
                record["seen"].append(event["eventId"])
                logger.info("MoM result %s for request %s, location %s: %s, eligible=%s",
                            event["eventId"], request_id, location_id, event["outcome"], event["eligibleForChallenge"])
                if event["eligibleForChallenge"]:
                    self.state["cleared"].append(location_id)
                    cleared.add(location_id)
                    break
        if self._dirty:
            self.save()
        # Idempotent LocationChecks may be resent until the server acknowledges.
        return cleared

    def pending_checks(self, completed: set[int]) -> set[int]:
        # A converted chest's clear unlocks Open. Its existing treasure path
        # sends the AP check only when the player actually opens that chest.
        return {i for i in completed if self.challenges[i].get("music_chest_kind") != "vanilla"
                and self.challenges[i].get("music_chest_placement_version") != 1}

    def publish_clears(self, bridge_dir: Path) -> None:
        """Publish verified external results; the DLL builds chest gates and placements."""
        inputs = (str(bridge_dir), frozenset(self.state["cleared"]))
        if inputs == getattr(self, "_clear_inputs", None):
            return
        payload = {"schema": "kh3ap.mom_clear_state.v1", **self.scope,
                   "cleared_locations": sorted(inputs[1])}
        atomic_write_json(bridge_sidecar_path(Path(bridge_dir), RUNTIME_MOM_CLEAR_STATE_FILE), payload)
        self._clear_inputs = inputs
