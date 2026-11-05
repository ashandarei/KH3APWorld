"""Run the production level checkpoint worker through its real IPC host."""

import json
import os
import subprocess

import pytest

from tools.kh3.tests.test_pipe_transport import NATIVE, RawPeer, wait_until


def metadata(root, ids=(2, 5, 10, 99)):
    payload = dict(
        kh3_seed=dict(seed_name="level-test", slot=1),
        bridge=dict(
            locations=[
                dict(
                    source_table="LevelUp",
                    source_id=str(level),
                    bridge_key=f"LevelUp:{level}",
                    ap_location_id=2000 + level,
                )
                for level in ids
            ]
        ),
    )
    (root / "slot_data.json").write_text(json.dumps(payload))
    return payload


class LevelHost:
    def __init__(self, root):
        self.root = root
        self.process = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(root)], stdout=subprocess.DEVNULL)
        self.peer = RawPeer(root)
        self.serial = 0
        self.closed = False

    def control(self, **fields):
        self.serial += 1
        self.peer.send("runtime_ap_message.json", {"level_up_test": self.serial, **fields})
        return self.peer.result("runtime_ap_message_result.json")

    def until(self, condition):
        result = {}

        def inspect():
            nonlocal result
            result = self.control()
            return condition(result)

        wait_until(inspect)
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.peer.send("runtime_ap_message.json", {"stop": True})
            self.process.wait(timeout=5)
        self.peer.close()


@NATIVE
@pytest.mark.parametrize("padding", [0, 9 * 1024 * 1024])
def test_level_thresholds_commit_once_and_survive_reconnect_and_cold_restart(tmp_path, padding):
    payload = metadata(tmp_path)
    (tmp_path / "slot_data.json").write_text(json.dumps({**payload, "padding": "x" * padding}))
    host = LevelHost(tmp_path)
    journal = tmp_path / "level_up_progress.json"
    try:
        host.until(lambda state: state.get("ready"))
        host.control(level=1)
        assert not journal.exists()
        # A level jump selects only enabled thresholds, including skipped levels.
        host.control(level=8)
        checked = host.until(lambda state: state.get("highest_level") == 8)
        assert checked["completed"] == ["2", "5"]
        assert json.loads(journal.read_text())["completed"] == checked["completed"]
        before = journal.read_bytes()
        for level in (8, 3, 0, 100, -1):
            host.control(level=level)
        host.control(level=99, wrong_root=True)
        host.peer.close()
        host.peer = RawPeer(tmp_path)
        checked = host.until(lambda state: state.get("ready"))
        assert checked["completed"] == ["2", "5"]
        assert journal.read_bytes() == before
        host.close()
        host = LevelHost(tmp_path)
        checked = host.until(lambda state: state.get("ready"))
        assert checked["highest_level"] == 8 and checked["completed"] == ["2", "5"]
        host.control(level=99)
        checked = host.until(lambda state: state.get("highest_level") == 99)
        assert checked["completed"] == ["2", "5", "10", "99"]
        assert json.loads(journal.read_text())["record_count"] == 4
    finally:
        host.close()


def checkpoint(**overrides):
    return dict(
        schema="kh3ap.level_up_progress.v1",
        owner="dll",
        seed_name="level-test",
        slot=1,
        highest_level=5,
        record_count=2,
        completed=["2", "5"],
        complete=True,
        **overrides,
    )


@NATIVE
@pytest.mark.parametrize(
    "saved",
    [
        "{",
        "null",
        json.dumps(dict(schema="wrong")),
        *[
            json.dumps(checkpoint() | override)
            for override in (
                dict(seed_name="other"),
                dict(slot=2),
                dict(owner="client"),
                dict(highest_level=100),
                dict(highest_level=True),
                dict(complete=False),
                dict(record_count=1),
                dict(completed=["2", "2"]),
                dict(completed=["2", "10"]),
                dict(completed=["5"]),
            )
        ],
    ],
)
def test_corrupt_or_mismatched_level_checkpoint_fails_without_overwrite(tmp_path, saved):
    metadata(tmp_path)
    journal = tmp_path / "level_up_progress.json"
    journal.write_text(saved)
    host = LevelHost(tmp_path)
    try:
        failed = host.until(lambda state: state.get("reason"))
        assert failed["ready"] is False and failed["completed"] == []
        host.control(level=99)
        assert host.control()["ready"] is False
        assert journal.read_text() == saved
    finally:
        host.close()


@NATIVE
def test_failed_level_commit_preserves_checkpoint_and_recovers_on_restart(tmp_path):
    metadata(tmp_path)
    journal = tmp_path / "level_up_progress.json"
    host = LevelHost(tmp_path)
    try:
        host.until(lambda state: state.get("ready"))
        host.control(level=5)
        host.until(lambda state: state.get("highest_level") == 5)
        before = journal.read_bytes()
        blocker = tmp_path / "level_up_progress.json.tmp"
        blocker.mkdir()
        host.control(level=99)
        failed = host.until(lambda state: state.get("reason"))
        assert failed["ready"] is False
        assert failed["highest_level"] == 5 and failed["completed"] == ["2", "5"]
        assert journal.read_bytes() == before
        host.close()
        blocker.rmdir()
        host = LevelHost(tmp_path)
        assert host.until(lambda state: state.get("ready"))["highest_level"] == 5
        host.control(level=99)
        assert host.until(lambda state: state.get("highest_level") == 99)["record_count"] == 4
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize(
    "override",
    [
        dict(source_id="02"),
        dict(source_id="1"),
        dict(source_id="100"),
        dict(bridge_key="LevelUp:99"),
        dict(ap_location_id=0),
    ],
)
def test_invalid_selected_level_row_stops_tracking(tmp_path, override):
    data = metadata(tmp_path)
    data["bridge"]["locations"][0].update(override)
    (tmp_path / "slot_data.json").write_text(json.dumps(data))
    host = LevelHost(tmp_path)
    try:
        assert host.until(lambda state: state.get("reason"))["ready"] is False
        assert not (tmp_path / "level_up_progress.json").exists()
    finally:
        host.close()


@NATIVE
def test_all_98_level_checks_include_junk_levels_and_respect_the_level_99_boundary(tmp_path):
    metadata(tmp_path, ids=range(2, 100))
    host = LevelHost(tmp_path)
    try:
        host.until(lambda state: state.get("ready"))
        host.control(level=98)
        state = host.until(lambda state: state.get("highest_level") == 98)
        assert state["completed"] == [str(level) for level in range(2, 99)]
        host.control(level=99)
        state = host.until(lambda state: state.get("highest_level") == 99)
        assert state["completed"] == [str(level) for level in range(2, 100)]
        assert state["record_count"] == 98
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("duplicate_row", [True, False])
def test_duplicate_level_or_ap_location_is_rejected(tmp_path, duplicate_row):
    data = metadata(tmp_path)
    rows = data["bridge"]["locations"]
    if duplicate_row:
        rows.append(dict(rows[0]))
    else:
        rows[1]["ap_location_id"] = rows[0]["ap_location_id"]
    (tmp_path / "slot_data.json").write_text(json.dumps(data))
    host = LevelHost(tmp_path)
    try:
        assert host.until(lambda state: state.get("reason"))["ready"] is False
        assert not (tmp_path / "level_up_progress.json").exists()
    finally:
        host.close()
