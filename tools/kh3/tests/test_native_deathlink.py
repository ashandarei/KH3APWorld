"""Real worker/IPC coverage of DeathLink ownership, persistence and recovery."""
import json
import os
import subprocess

import pytest

from tools.kh3.tests.test_pipe_transport import NATIVE, RawPeer, wait_until


class DeathLinkHost:
    def __init__(self, root):
        self.root = root
        if not (root / "slot_data.json").exists():
            (root / "slot_data.json").write_text(json.dumps({
                "kh3_seed": {"seed_name": "test-seed", "slot": 1}, "bridge": {"locations": []}}))
        self.process = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(root)], stdout=subprocess.DEVNULL)
        self.peer = RawPeer(root)
        self.serial = 0
        self.event = 0
        self.closed = False
        self.until(lambda s: s.get("process_id") == self.process.pid)

    def inspect(self, channel=None, payload=None):
        if channel == "runtime_item_apply_event.json":
            payload = {
                "owner": "dll", "process_id": self.process.pid,
                "seed_name": "test-seed", "slot": 1,
                "delivery_epoch": self.recon()["delivery_epoch"],
                "action_kind": "force_game_over", "applied_indices": [],
                "applied_at": "2026-10-05T12:00:00Z", **payload,
            }
        self.serial += 1
        fields = {"deathlink_test": self.serial}
        if channel:
            fields.update(channel=channel, records=[payload])
        self.peer.send("runtime_ap_message.json", fields)
        return self.peer.result("runtime_ap_message_result.json")

    def until(self, predicate):
        result = {}
        def check():
            nonlocal result
            result = self.inspect()
            return predicate(result)
        wait_until(check)
        return result

    def bind(self, *, amnesty=0, enabled=True, online=True, incoming=None, sent_ids=None, **overrides):
        self.peer.send("runtime_deathlink_control.json", {
            "schema": "kh3ap.deathlink_control.v1", "seed_name": "test-seed", "slot": 1,
            "process_id": self.process.pid, "enabled": enabled, "online": online, "amnesty": amnesty,
            "incoming": incoming or [], "sent_ids": sent_ids or [], **overrides})
        self.until(lambda s: s.get("amnesty") == amnesty and s.get("enabled") == enabled)

    def state(self, *, dead=False, unavailable=False, safe=True, special_battle=False):
        self.event += 1
        payload = {"status": "unavailable"} if unavailable else {
            "status": "known", "current_state_id": 118 if dead else 0,
            "state_class": "TresPlayerState_GameOver" if dead else "TresPlayerState_Idle",
            "safety": "not_ok" if dead or not safe else "ok", "polyhook_event_index": self.event,
            "special_battle_mode_active": special_battle}
        self.inspect("runtime_sora_state.json", payload)

    def context(self, name="pawn", **overrides):
        self.event += 1
        self.inspect("runtime_context.json", {
            "hook": "RuntimePlayablePawnContext", "world": "tt_01",
            "object": f"p_ex001_Pawn_C /Game/Levels/tt/tt_01/tt_01.tt_01:PersistentLevel.{name} real=true",
            "opened_at": f"2026-10-04T12:00:{self.event:02d}Z", **overrides})

    def recon(self, kind="inspect", **fields):
        self.serial += 1
        self.peer.send("runtime_ap_message.json", {"reconciliation_test": self.serial, "kind": kind,
                                                 "event": self.serial, **fields})
        return self.peer.result("runtime_ap_message_result.json")

    def until_recon(self, predicate):
        result = {}
        def check():
            nonlocal result
            result = self.recon()
            return predicate(result)
        wait_until(check)
        return result

    def bootstrap(self, *, items=None, saved=None):
        rows = [dict(index=item["index"], item_id=1263022503,
                     from_player=2, location_id=-1) for item in (items or [])]
        self.peer.send("runtime_receipt_catalog.json", {
            "schema": "kh3ap.receipt_catalog.v4", "seed_name": "test-seed", "slot": 1,
            "process_id": self.process.pid, "items": rows, "server_checked_locations": []})
        self.inspect("runtime_new_game_staging.json", {"opened_at": "2026-10-04T12:00:00Z"})
        self.until_recon(lambda s: s.get("ready"))
        if saved is not None:
            indices = list(range(saved["through"] + 1)) + saved["extra"]
            for index in indices:
                self.recon("completion", index=index, epoch=self.recon()["delivery_epoch"])
            self.until_recon(lambda s: s.get("active_receipts") == saved)
            self.recon("auto_begin")
            self.recon("save_open", slot=0, event=5000)
            self.recon("save_closed", event=5000, success=True)
            self.until_recon(lambda s: s["saved_slots"]["slots"].get("kh3sv2/data/KHIII_slot0.bin", {}).get("saved_receipts") == saved)
            self.recon("load", slot=0)
            self.until_recon(lambda s: s.get("ready") and s.get("active_receipts") == saved)

    def die(self):
        self.state()
        self.state(dead=True)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.peer.send("runtime_ap_message.json", {"stop": True})
            self.process.wait(5)
        self.peer.close()


@NATIVE
@pytest.mark.parametrize("padding", [0, 9 * 1024 * 1024])
def test_native_amnesty_episodes_ack_and_cold_restart(tmp_path, padding):
    (tmp_path / "slot_data.json").write_text(json.dumps({
        "kh3_seed": {"seed_name": "test-seed", "slot": 1}, "bridge": {"locations": []},
        "padding": "x" * padding}))
    host = DeathLinkHost(tmp_path)
    journal = tmp_path / "runtime_native_deathlink.json"
    try:
        host.state(dead=True)
        host.bind(amnesty=3)
        host.state(dead=True)
        assert host.inspect()["forgiven_deaths"] == 0  # connecting on a death screen
        for count in range(1, 4):
            host.die()
            state = host.until(lambda s: s.get("forgiven_deaths") == count)
            assert state["outgoing"] == []
        host.state(dead=True)
        host.state(unavailable=True)
        host.state(dead=True)
        assert host.inspect()["forgiven_deaths"] == 3
        host.close()
        host = DeathLinkHost(tmp_path)
        host.bind(amnesty=3)
        assert host.inspect()["forgiven_deaths"] == 3
        host.die()
        event = host.until(lambda s: len(s.get("outgoing", [])) == 1)
        assert event["forgiven_deaths"] == 0
        identity = event["outgoing"][0]["id"]
        assert json.loads(journal.read_text())["outgoing"][0]["id"] == identity
        host.peer.close()
        host.peer = RawPeer(tmp_path)
        host.bind(amnesty=3, sent_ids=[identity])
        host.until(lambda s: s.get("outgoing") == [])
        for count in range(1, 4):
            host.die()
            host.until(lambda s: s.get("forgiven_deaths") == count)
        host.die()
        event = host.until(lambda s: len(s.get("outgoing", [])) == 1)
        assert event["outgoing"][0]["id"] != identity
    finally:
        host.close()


@NATIVE
def test_native_settings_offline_and_foreign_control(tmp_path):
    host = DeathLinkHost(tmp_path)
    try:
        host.state()
        host.bind(amnesty=2)
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 1)
        host.bind(amnesty=2, online=False)
        host.die()
        assert host.inspect()["forgiven_deaths"] == 1
        host.bind(amnesty=2)
        host.state(dead=True)
        assert host.inspect()["forgiven_deaths"] == 1
        host.bind(amnesty=1)
        host.until(lambda s: s.get("forgiven_deaths") == 0)
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 1)
        host.bind(amnesty=1, enabled=False)
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 0)
        assert host.inspect()["outgoing"] == []
        host.peer.send("runtime_deathlink_control.json", {
            "schema": "kh3ap.deathlink_control.v1", "seed_name": "other", "slot": 1,
            "process_id": host.process.pid, "enabled": True, "online": True, "amnesty": 0,
            "incoming": [], "sent_ids": []})
        assert host.inspect()["enabled"] is False
        assert host.inspect()["ready"] is True
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("map_source", ["world", "object", "sora_state"])
def test_station_of_awakening_ignores_incoming_and_outgoing_deathlinks(tmp_path, map_source):
    host = DeathLinkHost(tmp_path)
    try:
        host.bootstrap()
        host.bind(amnesty=1)
        host.state()
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 1)
        host.state()
        station = "/Game/Maps/StationOfAwakening/umap/StationOfAwakening.StationOfAwakening"
        if map_source == "sora_state":
            host.inspect("runtime_sora_state.json", {"status": "unavailable", "runtime_context": station.lower()})
        else:
            host.context(**{map_source: station})
        incoming = {"request_id": "deathlink-station", "source": "Riku", "cause": "Defeated"}
        host.bind(amnesty=1, incoming=[incoming])
        state = host.until(lambda s: incoming["request_id"] in s.get("accepted_ids", []))
        assert not state["fenced"] and state["priority"] is None
        host.die()
        state = host.inspect()
        assert state["outgoing"] == []
        assert state["forgiven_deaths"] == 1

        host.context()
        host.state()
        # Reconnect must not replay the death ignored on the station map.
        host.bind(amnesty=1, incoming=[incoming])
        assert host.inspect()["priority"] is None
        host.die()
        host.until(lambda s: len(s.get("outgoing", [])) == 1)
        next_death = dict(incoming, request_id="deathlink-after-station")
        host.bind(amnesty=1, incoming=[next_death])
        state = host.until(lambda s: s.get("priority") is not None)
        assert state["priority"]["request_id"] == next_death["request_id"]
    finally:
        host.close()


@NATIVE
def test_pending_deathlink_waits_while_on_station_of_awakening(tmp_path):
    host = DeathLinkHost(tmp_path)
    try:
        # Queue before reconciliation is ready, then enter the station map.
        host.bind(incoming=[{"request_id": "deathlink-before-station", "source": "Riku", "cause": "Defeated"}])
        host.until(lambda s: s.get("awaiting_apply"))
        host.context(world="StationOfAwakening")
        host.bootstrap()
        assert host.inspect()["priority"] is None
        host.context()
        host.until(lambda s: s.get("priority") is not None)
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("enabled,online", [(False, True), (True, False), (False, False)])
def test_manual_gameover_dispatches_in_special_battle_without_deathlink(tmp_path, enabled, online):
    host = DeathLinkHost(tmp_path)
    try:
        host.bootstrap()
        host.inspect("runtime_sora_state.json", {
            "status": "known", "current_state_id": 0, "state_class": "TresPlayerState_Idle",
            "safety": "ok", "special_battle_mode_active": True})
        network = {"request_id": "deathlink-gameover-disabled", "source": "Riku", "cause": "Defeated"}
        host.bind(enabled=enabled, online=online, incoming=[network])
        state = host.until(lambda s: network["request_id"] in s.get("accepted_ids", []))
        assert not state["fenced"] and state["priority"] is None
        manual = {"request_id": "manual-gameover-42", "source": "Sora", "cause": "Manual test", "manual": True}
        host.bind(enabled=enabled, online=online, incoming=[manual])
        state = host.until(lambda s: s.get("priority") is not None)
        assert state["fenced"] and state["awaiting_apply"]
        assert state["priority"]["request_id"] == manual["request_id"]
        assert state["priority"]["grants"][0]["kh3_grant_method"] == "bp_tres_start_game_over"
        assert state["priority"]["grants"][0]["action_kind"] == "manual_game_over"
        assert state["request"]["manual"] is True
        assert state["outgoing"] == []
    finally:
        host.close()


@NATIVE
def test_manual_gameover_can_repeat_after_special_battle_retry(tmp_path):
    host = DeathLinkHost(tmp_path)
    try:
        host.bootstrap()
        host.context("combat")
        host.state(special_battle=True)
        for episode in range(2):
            intent = dict(request_id=f"manual-gameover-{episode}", source="Sora",
                          cause="Manual test", manual=True)
            host.bind(enabled=False, online=False, incoming=[intent])
            state = host.until(lambda s: s.get("priority") is not None)
            assert state["priority"]["request_id"] == intent["request_id"]
            host.inspect("runtime_item_apply_event.json", {
                "request_id": intent["request_id"], "status": "applied"})
            host.until(lambda s: s.get("recovery_pending"))
            host.state(dead=True, special_battle=True)
            host.context(f"retry-{episode}")
            host.state(safe=False, special_battle=True)
            assert host.inspect()["fenced"]
            host.state(special_battle=True)
            state = host.until(lambda s: not s.get("fenced"))
            assert state["status"] == "recovered" and state["priority"] is None
            assert state["outgoing"] == []
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("apply_before_death", [False, True])
def test_manual_gameover_owned_alternate_recovery_requires_death_and_new_ready_pawn(tmp_path, apply_before_death):
    host = DeathLinkHost(tmp_path)
    try:
        host.bootstrap()
        host.state(unavailable=True)
        for episode in range(2):
            intent = dict(request_id=f"manual-ally-{episode}", source="Kairi", cause="Manual", manual=True)
            host.bind(enabled=False, online=False, incoming=[intent])
            host.until(lambda s: s.get("priority") is not None)
            if apply_before_death:
                host.inspect("runtime_item_apply_event.json", {"request_id": intent["request_id"], "status": "applied"})
                host.until(lambda s: s.get("recovery_pending"))
            host.inspect("manual_player", dict(player_key="new", dead=False, ready=True))
            assert host.inspect()["fenced"]
            host.inspect("manual_player", dict(player_key="dead", dead=True, ready=False))
            if not apply_before_death:
                host.state(safe=False)
                host.inspect("runtime_item_apply_event.json", {"request_id": intent["request_id"], "status": "applied"})
                host.until(lambda s: s.get("recovery_pending"))
            host.inspect("manual_player", dict(player_key="dead", dead=False, ready=True))
            assert host.inspect()["fenced"]
            host.inspect("manual_player", dict(player_key="new", dead=False, ready=False))
            assert host.inspect()["fenced"]
            host.inspect("manual_player", dict(player_key="new", dead=False, ready=True))
            state = host.until(lambda s: not s.get("fenced"))
            assert state["status"] == "recovered" and state["priority"] is None
            assert state["outgoing"] == []
        network = dict(request_id="network-sora-only", source="Riku", cause="Death")
        host.bind(enabled=True, online=True, incoming=[network])
        host.until(lambda s: s.get("priority") is not None)
        host.inspect("runtime_item_apply_event.json", {"request_id": network["request_id"], "status": "applied"})
        host.until(lambda s: s.get("recovery_pending"))
        host.inspect("manual_player", dict(player_key="dead", dead=True, ready=False))
        host.inspect("manual_player", dict(player_key="new", dead=False, ready=True))
        assert host.inspect()["fenced"]
    finally:
        host.close()


@NATIVE
def test_native_received_death_priority_echo_fence_and_recovery(tmp_path):
    host = DeathLinkHost(tmp_path)
    intent = {"request_id": "deathlink-gameover-1234500", "source": "Riku", "cause": "Defeated"}
    try:
        host.state()
        host.bind(amnesty=3)
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 1)
        host.bind(amnesty=3, incoming=[intent])
        state = host.until(lambda s: s.get("awaiting_apply"))
        assert state["fenced"] and state["priority"] is None  # no loaded receipt context
        host.bootstrap()
        state = host.until(lambda s: s.get("priority") is not None)
        grant = state["priority"]
        assert grant["process_id"] == host.process.pid
        assert grant["grants"][0]["kh3_grant_method"] == "bp_tres_start_game_over"
        assert grant["grants"][0]["index"] == -9001
        assert grant["delivery_epoch"] > 0
        host.inspect("runtime_item_apply_event.json", {"request_id": intent["request_id"], "status": "applied"})
        host.until(lambda s: s.get("recovery_pending"))
        host.state()
        host.context("too_early")
        assert host.inspect()["fenced"]  # a successful call is not Game Over evidence
        host.state(dead=True)
        host.state(unavailable=True)
        assert host.inspect()["fenced"]
        host.context("invalid", object="pawn real=false")
        host.state()
        assert host.inspect()["fenced"]
        host.context("recovered")
        state = host.until(lambda s: not s.get("fenced"))
        assert state["forgiven_deaths"] == 1 and state["outgoing"] == []
        host.bind(amnesty=3, incoming=[intent])  # replayed AP packet stays retired
        assert host.inspect()["status"] == "recovered"
        host.die()
        host.until(lambda s: s.get("forgiven_deaths") == 2)
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("manual", [False, True])
def test_native_pending_and_applied_requests_survive_game_restart(tmp_path, manual):
    intent = {"request_id": "deathlink-gameover-42", "source": "Riku", "cause": "Defeated"}
    if manual:
        intent.update(request_id="manual-gameover-42", manual=True)
    host = DeathLinkHost(tmp_path)
    try:
        host.bind(incoming=[intent])
        host.until(lambda s: s.get("awaiting_apply"))
        host.close()
        host = DeathLinkHost(tmp_path)
        host.bootstrap()
        state = host.until(lambda s: s.get("priority") is not None)
        assert state["priority"]["process_id"] == host.process.pid
        assert state["priority"]["grants"][0]["action_kind"] == (
            "manual_game_over" if manual else "deathlink_game_over")
        host.inspect("runtime_item_apply_event.json", {"request_id": intent["request_id"], "status": "applied"})
        host.until(lambda s: s.get("recovery_pending"))
        host.close()
        host = DeathLinkHost(tmp_path)
        host.context("pre_load")
        host.state()
        assert host.inspect()["fenced"]
        host.bootstrap()
        host.context("after_load")
        state = host.until(lambda s: not s.get("fenced"))
        assert state["priority"] is None and state["outgoing"] == []
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("saved", ["{", "null", json.dumps({"schema": "wrong"})])
def test_native_corrupt_deathlink_checkpoint_is_preserved(tmp_path, saved):
    journal = tmp_path / "runtime_native_deathlink.json"
    journal.write_text(saved)
    host = DeathLinkHost(tmp_path)
    try:
        state = host.until(lambda s: not s.get("ready") and s.get("reason"))
        assert state["fenced"] and state["priority"] is None
        assert journal.read_text() == saved
    finally:
        host.close()


@NATIVE
def test_native_deathlink_failed_commit_cannot_accept_or_dispatch(tmp_path):
    host = DeathLinkHost(tmp_path)
    journal = tmp_path / "runtime_native_deathlink.json"
    try:
        host.bind(amnesty=1)
        before = journal.read_bytes()
        (tmp_path / "runtime_native_deathlink.json.tmp").mkdir()
        intent = {"request_id": "deathlink-gameover-42", "source": "Riku", "cause": "Defeated"}
        host.bind(amnesty=1, incoming=[intent])
        state = host.until(lambda s: not s.get("ready"))
        assert state["accepted_ids"] == [] and state["priority"] is None
        assert journal.read_bytes() == before
    finally:
        host.close()


@NATIVE
def test_native_deathlink_freezes_sparse_retry_receipts_before_later_autosave(tmp_path):
    host = DeathLinkHost(tmp_path)
    before = dict(version=1, through=-1, extra=[1])
    after = dict(version=1, through=-1, extra=[1, 2])
    intent = {"request_id": "deathlink-gameover-42", "source": "Riku", "cause": "Defeated"}
    journal = tmp_path / "runtime_native_reconciliation.json"
    try:
        host.bootstrap(items=[dict(index=i, replay=True, passthrough=False) for i in [1, 2]], saved=before)
        host.context("before_death")
        host.state()
        host.bind(incoming=[intent])
        host.until(lambda s: s.get("priority") is not None)
        assert json.loads(journal.read_text())["deathlink"]["saved_receipts"] == before

        # An in-flight grant finishes and a real save transaction commits a later
        # receipt boundary after the DeathLink has already claimed its checkpoint.
        host.recon("completion", index=2)
        host.until_recon(lambda s: s.get("active_receipts") == after)
        host.recon("auto_begin")
        host.recon("save_open", slot=0, event=200)
        host.recon("save_closed", event=200, success=True)
        host.until_recon(lambda s: s["saved_slots"]["slots"].get("kh3sv2/data/KHIII_slot0.bin", {}).get("saved_receipts") == after)
        assert json.loads(journal.read_text())["deathlink"]["saved_receipts"] == before

        host.inspect("runtime_item_apply_event.json", {"request_id": intent["request_id"], "status": "applied"})
        host.until(lambda s: s.get("recovery_pending"))
        host.until_recon(lambda s: json.loads(journal.read_text())["deathlink"]["confirmed"])
        host.inspect("current_state_write_polyhook_events.jsonl", {
            "state_class": "TresPlayerState_GameOver", "state_comp_matches_cached_pawn": True,
            "recorded_at_utc": "2026-10-04T12:01:00Z"})
        host.until_recon(lambda s: s.get("reason") == "game_over")
        host.state(dead=True)
        host.context("after_death")
        recovered = host.until_recon(lambda s: s.get("recovery_source") == "death_continue_retry" and s.get("ready"))
        assert recovered["active_receipts"] == before
        assert recovered["pending_replay_indices"] == [2]
        assert host.inspect()["fenced"]
        host.state()
        host.until(lambda s: not s.get("fenced"))
        assert json.loads(journal.read_text())["deathlink"] is None
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("amnesty", [0, 20])
def test_native_amnesty_limits_and_disconnected_deaths(tmp_path, amnesty):
    host = DeathLinkHost(tmp_path)
    try:
        host.state()
        host.bind(amnesty=amnesty)
        host.peer.close()
        host.peer = RawPeer(tmp_path)
        host.die()  # an IPC connection without the AP binding remains offline
        assert host.inspect()["forgiven_deaths"] == 0
        assert host.inspect()["outgoing"] == []
        host.bind(amnesty=amnesty)
        for count in range(1, amnesty + 1):
            host.die()
            host.until(lambda s: s.get("forgiven_deaths") == count)
        host.die()
        state = host.until(lambda s: len(s.get("outgoing", [])) == 1)
        assert state["forgiven_deaths"] == 0
    finally:
        host.close()


@NATIVE
def test_native_coalesces_received_deaths_and_preserves_admitted_request_when_disabled(tmp_path):
    host = DeathLinkHost(tmp_path)
    incoming = [dict(request_id=f"deathlink-gameover-{i}", source="Riku", cause="Defeated") for i in [1, 2]]
    try:
        host.state()
        host.bind(incoming=incoming)
        state = host.until(lambda s: len(s.get("accepted_ids", [])) == 2)
        assert state["request_id"] == incoming[0]["request_id"]
        host.bind(enabled=False, online=False)
        host.bootstrap()
        state = host.until(lambda s: s.get("priority") is not None)
        assert state["priority"]["request_id"] == incoming[0]["request_id"]
        assert state["awaiting_apply"] and state["outgoing"] == []
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("invalid", [
    {"amnesty": 21}, {"forgiven_deaths": 1}, {"accepted_ids": ["duplicate", "duplicate"]},
    {"outgoing": [{"id": "1", "cause": ""}]},
    {"request": {"request_id": "unknown", "status": "applied", "process_id": 123}},
])
def test_native_invalid_checkpoint_membership_or_counts_cannot_be_rewritten(tmp_path, invalid):
    saved = json.dumps({"schema": "kh3ap.native_deathlink.v1", "owner": "dll", "seed_name": "test-seed",
                        "slot": 1, "enabled": True, "amnesty": 0, "forgiven_deaths": 0, "episode": 0,
                        "request": None, "accepted_ids": [], "outgoing": [], "complete": True, **invalid})
    journal = tmp_path / "runtime_native_deathlink.json"
    journal.write_text(saved)
    host = DeathLinkHost(tmp_path)
    try:
        state = host.until(lambda s: not s.get("ready") and s.get("reason"))
        assert state["fenced"] and state["priority"] is None
        assert journal.read_text() == saved
    finally:
        host.close()


@NATIVE
def test_native_deathlink_cannot_dispatch_without_durable_receipt_freeze(tmp_path):
    host = DeathLinkHost(tmp_path)
    try:
        host.bootstrap()
        (tmp_path / "runtime_native_reconciliation.json.tmp").mkdir()
        host.bind(incoming=[dict(request_id="deathlink-gameover-1", source="Riku", cause="Defeated")])
        host.until_recon(lambda s: s.get("reason") == "journal_open_failed")
        state = host.until(lambda s: s.get("awaiting_apply"))
        assert state["fenced"] and state["priority"] is None
        assert json.loads((tmp_path / "runtime_native_reconciliation.json").read_text())["deathlink"] is None
    finally:
        host.close()


@NATIVE
def test_native_deathlink_does_not_freeze_a_previous_maps_zone_baseline(tmp_path):
    host = DeathLinkHost(tmp_path)
    before = dict(version=1, through=-1, extra=[1])
    after = dict(version=1, through=-1, extra=[1, 2])
    try:
        host.bootstrap(items=[dict(index=i, replay=True, passthrough=False) for i in [1, 2]], saved=before)
        host.context("zone_entry", pawn_source="VanillaLevelWritePolyhook:level_owner")
        host.until_recon(lambda s: s.get("runtime_context", {}).get("pawn_source") == "VanillaLevelWritePolyhook:level_owner")
        host.recon("completion", index=2)
        host.until_recon(lambda s: s.get("active_receipts") == after)
        host.recon("auto_begin")
        host.recon("save_open", slot=0, event=200)
        host.recon("save_closed", event=200, success=True)
        host.until_recon(lambda s: s["saved_slots"]["slots"].get("kh3sv2/data/KHIII_slot0.bin", {}).get("saved_receipts") == after)
        host.context("different_map", world="fz_01",
                     object="p_ex001_Pawn_C /Game/Levels/fz/fz_01/fz_01.fz_01:PersistentLevel.next real=true")
        host.until_recon(lambda s: s.get("runtime_context", {}).get("world") == "fz_01")
        host.state()
        host.bind(incoming=[dict(request_id="deathlink-gameover-1", source="Riku", cause="Defeated")])
        host.until(lambda s: s.get("priority") is not None)
        frozen = json.loads((tmp_path / "runtime_native_reconciliation.json").read_text())["deathlink"]
        assert frozen["saved_receipts"] == after
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("destination_ready", [False, True])
def test_two_deathlinks_across_world_travel_replay_only_unsaved_receipts(tmp_path, destination_ready):
    """A second death must retain items saved between GoA and the next world."""
    host = DeathLinkHost(tmp_path)
    initial = dict(version=1, through=-1, extra=[1])
    after_first = dict(version=1, through=-1, extra=[1, 2])
    journal = tmp_path / "runtime_native_reconciliation.json"

    def recover(request_id, pawn, world, expected):
        host.bind(incoming=[dict(request_id=request_id, source="Riku", cause="Defeated")])
        host.until(lambda state: state.get("priority") is not None)
        assert json.loads(journal.read_text())["deathlink"]["saved_receipts"] == expected
        host.inspect("runtime_item_apply_event.json", {"request_id": request_id, "status": "applied"})
        host.until(lambda state: state.get("recovery_pending"))
        host.until_recon(lambda state: json.loads(journal.read_text())["deathlink"]["confirmed"])
        host.inspect("current_state_write_polyhook_events.jsonl", {
            "state_class": "TresPlayerState_GameOver", "state_comp_matches_cached_pawn": True,
            "recorded_at_utc": "2026-10-22T14:05:30Z" if request_id == "deathlink-first" else
                                  "2026-10-22T14:06:30Z"})
        host.until_recon(lambda state: state.get("reason") == "game_over")
        host.state(dead=True)
        host.context(pawn, world=world,
                     object=f"p_ex001_Pawn_C {world}:PersistentLevel.{pawn} real=true")
        restored = host.until_recon(lambda state: state.get("ready") and
                                   state.get("recovery_source") == "death_continue_retry")
        assert restored["active_receipts"] == expected
        host.state()
        host.until(lambda state: not state.get("fenced"))
        return restored

    try:
        host.bootstrap(items=[dict(index=index) for index in [1, 2, 3]], saved=initial)
        garden = "/Game/Maps/GardenOfAssemblage/umap/GoA_01.GoA_01"
        host.context("garden", world=garden)
        host.state()
        host.recon("completion", index=2)
        host.until_recon(lambda state: state.get("active_receipts") == after_first)
        first = recover("deathlink-first", "garden_recovered", garden, initial)
        assert first["pending_replay_indices"] == [2, 3]
        host.recon("completion", index=2)
        host.until_recon(lambda state: state.get("active_receipts") == after_first)
        host.recon("auto_begin")
        host.recon("save_open", slot=0, event=6000)
        host.recon("save_closed", event=6000, success=True)
        host.until_recon(lambda state: state["saved_slots"]["slots"]
                        ["kh3sv2/data/KHIII_slot0.bin"]["saved_receipts"] == after_first)

        destination = "/Game/Levels/bx/bx_03/bx_03.bx_03"
        host.context("baymax", world=destination,
                     object=f"p_bx001_Pawn_C {destination}:PersistentLevel.baymax real=true",
                     pawn_source="VanillaLevelWritePolyhook:level_owner" if destination_ready else
                                 "HandleStartingNewPlayer:NewPlayer:Pawn")
        host.until_recon(lambda state: state.get("runtime_context", {}).get("world") == destination)
        host.recon("completion", index=3)
        host.until_recon(lambda state: state.get("active_receipts", {}).get("extra") == [1, 2, 3])
        second = recover("deathlink-second", "baymax_recovered", destination, after_first)
        assert second["pending_replay_indices"] == [3]
        host.recon("completion", index=3)
        host.until_recon(lambda state: state.get("pending_replay_indices") == [])
    finally:
        host.close()


@NATIVE
def test_deathlink_continue_uses_destination_autosave_completed_after_receipt_freeze(tmp_path):
    """Continue loads the actual slot even when arrival saved after DeathLink admission."""
    host = DeathLinkHost(tmp_path)
    before = dict(version=1, through=-1, extra=[1])
    after = dict(version=1, through=-1, extra=[1, 2])
    journal = tmp_path / "runtime_native_reconciliation.json"
    request_id = "deathlink-during-world-arrival"
    try:
        host.bootstrap(items=[dict(index=index) for index in [1, 2]], saved=before)
        host.context("garden")
        host.state()
        host.recon("completion", index=2)
        host.until_recon(lambda state: state.get("active_receipts") == after)
        host.context("destination", world="bx_03",
                     object="p_bx001_Pawn_C /Game/Levels/bx/bx_03/bx_03.bx_03:PersistentLevel.destination real=true",
                     pawn_source="HandleStartingNewPlayer:NewPlayer:Pawn")
        host.until_recon(lambda state: state.get("runtime_context", {}).get("world") == "bx_03")
        host.bind(incoming=[dict(request_id=request_id, source="Riku", cause="Defeated")])
        host.until(lambda state: state.get("priority") is not None)
        assert json.loads(journal.read_text())["deathlink"]["saved_receipts"] == before

        # The game's arrival autosave serializes both items while the pending
        # DeathLink waits for safe gameplay. They will both survive Continue.
        host.recon("auto_begin")
        host.recon("save_open", slot=0, event=7000)
        host.recon("save_closed", event=7000, success=True)
        host.until_recon(lambda state: state["saved_slots"]["slots"]
                        ["kh3sv2/data/KHIII_slot0.bin"]["saved_receipts"] == after)
        host.inspect("runtime_item_apply_event.json", {"request_id": request_id, "status": "applied"})
        host.until(lambda state: state.get("recovery_pending"))
        host.inspect("current_state_write_polyhook_events.jsonl", {
            "state_class": "TresPlayerState_GameOver", "state_comp_matches_cached_pawn": True,
            "recorded_at_utc": "2026-10-22T17:36:00Z"})
        host.until_recon(lambda state: state.get("reason") == "game_over")
        host.state(dead=True)
        host.recon("load", slot=0)
        host.until_recon(lambda state: state.get("ready") and state.get("active_receipts") == after)
        host.context("recovered", world="bx_03",
                     object="p_bx001_Pawn_C /Game/Levels/bx/bx_03/bx_03.bx_03:PersistentLevel.recovered real=true")
        recovered = host.until_recon(lambda state: state.get("runtime_context", {}).get("world") == "bx_03")
        assert recovered["active_receipts"] == after
        assert recovered["pending_replay_indices"] == []
        host.state()
        host.until(lambda state: not state.get("fenced"))
    finally:
        host.close()
