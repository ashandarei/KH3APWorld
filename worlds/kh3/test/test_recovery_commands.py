"""Recovery runs through the real client parser and in-memory IPC boundary."""
import json
from types import SimpleNamespace

import pytest

from ..Client import KH3CommandProcessor, KH3Context
from ..client_support.recovery_commands import GAMEFLOW, RESULTS, WARP, RecoveryCommandService, map_path
from ..transport.pipe_transport import DELETE, SET, register, unregister


@pytest.fixture
def recovery(tmp_path):
    transport = register(tmp_path)
    transport.connected = True
    transport.epoch = 11
    transport.accept([(SET, "runtime_context.json", 1,
                       b'{"process_id":123,"recovery_protocol":1,"kh02_gameflow_protocol":1,"mapjump_auto_start_protocol":1}')])
    messages = []
    ctx = SimpleNamespace(bridge_dir=tmp_path)
    service = RecoveryCommandService(ctx, messages.append)
    ctx.recovery_command_service = service
    ctx._get_recovery_command_service = lambda: KH3Context._get_recovery_command_service(ctx)
    try:
        yield service, transport, messages
    finally:
        unregister(transport)


def reply(transport, channel=GAMEFLOW, *, consume=True, **fields):
    request = transport.read(channel)
    result = {"request_id": request["request_id"], "process_id": 123, "status": "completed",
              "flag_name": request.get("flag_name"), "game_flag_read": True, **fields}
    transport.accept([(SET, RESULTS[channel], 1, json.dumps(result).encode())])
    if consume:
        transport.accept([(DELETE, channel, 2, transport.values[channel])])
    return request


def test_command_parser_reads_and_sets_with_verified_readback(recovery):
    service, transport, messages = recovery
    command = KH3CommandProcessor(service.ctx)
    command("/gameflow gameflow_TT 2000")
    request = transport.read(GAMEFLOW)
    assert request["command"] == "get_game_flag"
    assert request["request_schema"] == "kh3ap.runtime_control.v1"
    assert request["process_id"] == 123
    reply(transport, flag_value=1000)
    service.poll()
    write = transport.read(GAMEFLOW)
    assert write["command"] == "set_game_flag" and write["flag_value"] == 2000
    assert write["request_id"] != request["request_id"]
    assert "original value 1000" in messages[-1]
    reply(transport, game_flag_set=True, flag_value=2000)
    service.poll()
    assert transport.read(GAMEFLOW)["command"] == "get_game_flag"
    reply(transport, flag_value=2000)
    service.poll()
    assert service.active is None
    assert messages[-1] == "gameflow_TT: 1000 -> 2000 (verified)."
    assert not list(service.ctx.bridge_dir.glob("runtime_*.json"))
    command("/gameflow TT")
    reply(transport, flag_value=2000)
    service.poll()
    assert messages[-1] == "gameflow_TT = 2000."


def test_result_does_not_advance_until_native_consumes_request(recovery):
    service, transport, _ = recovery
    service.gameflow("TT", "2")
    request = reply(transport, flag_value=1, consume=False)
    service.poll()
    assert transport.read(GAMEFLOW) == request
    transport.accept([(DELETE, GAMEFLOW, 2, transport.values[GAMEFLOW])])
    service.poll()
    assert transport.read(GAMEFLOW)["command"] == "set_game_flag"


@pytest.mark.parametrize("value", ["x", "1.0", "2147483648", "-2147483649"])
def test_invalid_value_never_dispatches(recovery, value):
    service, transport, _ = recovery
    assert not service.gameflow("TT", value)
    assert transport.read(GAMEFLOW) is None


@pytest.mark.parametrize("name", ["", "a/b", "gameflow_TT;"])
def test_invalid_name_never_dispatches(recovery, name):
    service, transport, _ = recovery
    assert not service.gameflow(name)
    assert transport.read(GAMEFLOW) is None


def test_mapjump_parser_resolves_catalog_and_only_reports_dispatch(recovery):
    service, transport, messages = recovery
    KH3CommandProcessor(service.ctx)("/mapjump GoA_01 NormalPlayerStart")
    request = transport.read(WARP)
    assert request["map_name"] == "/Game/Maps/GardenOfAssemblage/umap/GoA_01"
    assert request["target_tag"] == "NormalPlayerStart"
    assert request["fade_kind"] == "fadeout"
    reply(transport, WARP)
    service.poll()
    assert service.active is None
    assert "dispatched" in messages[-1]
    assert not list(service.ctx.bridge_dir.glob("runtime_*.json"))
    assert map_path("tt_01") == "/Game/Levels/tt/tt_01/tt_01"
    assert map_path("/Game/Custom/map") == "/Game/Custom/map"


@pytest.mark.parametrize("name,tag", [("not_a_known_map", "Start"), ("", ""), ("GoA_01", "a/b")])
def test_invalid_mapjump_never_dispatches(recovery, name, tag):
    service, transport, _ = recovery
    assert not service.mapjump(name, tag)
    assert transport.read(WARP) is None


@pytest.mark.parametrize("argument,path", [
    ("tt", "/Game/Levels/tt/tt_01/tt_01"),
    ("FZ", "/Game/Levels/fz/fz_01/fz_01"),
    ("frozen", "/Game/Levels/fz/fz_01/fz_01"),
    ("he", "/Game/Levels/he/he_02/he_02"),
    ("kh02", "/Game/Maps/APKH02CastleTown/APKH02CastleTown"),
    ("0.2", "/Game/Maps/APKH02CastleTown/APKH02CastleTown"),
    ("tt_01", "/Game/Levels/tt/tt_01/tt_01"),
    ("GoA_01", "/Game/Maps/GardenOfAssemblage/umap/GoA_01"),
    ("dw_71", "/Game/Levels/dw/dw_71/dw_71"),
    ("dw_02", "/Game/Maps/APKH02CastleTown/APKH02CastleTown"),
    ("kh02:dw_08", "/Game/Maps/APKH02Mirrors/APKH02Forest"),
    ("APKH02Depths", "/Game/Maps/APKH02Mirrors/APKH02Depths"),
    ("/Game/Levels/dw/dw_03/dw_03", "/Game/Maps/APKH02WorldWithin/APKH02WorldWithin"),
    ("/Game/Custom/map", "/Game/Custom/map"),
])
def test_map_name_only_uses_destination_scoped_automatic_start(recovery, argument, path):
    service, transport, messages = recovery
    KH3CommandProcessor(service.ctx)("/mapjump " + argument)
    request = transport.read(WARP)
    assert request["map_name"] == path
    assert request["target_tag"] == "KH3AP_AutoPlayerStart"
    reply(transport, WARP)
    service.poll()
    assert "automatic PlayerStart" in messages[-1] and "dispatched" in messages[-1]


def test_automatic_start_requires_capability_but_explicit_start_still_works(recovery):
    service, transport, messages = recovery
    transport.accept([(SET, "runtime_context.json", 6, b'{"process_id":123,"recovery_protocol":1}')])
    assert not service.mapjump("tt_01")
    assert transport.read(WARP) is None
    assert "restart KH3" in messages[-1]
    assert service.mapjump("tt_01", "tt_01_Lv_Start_01")
    assert transport.read(WARP)["target_tag"] == "tt_01_Lv_Start_01"


def test_kh02_aliases_match_the_authored_host_metadata():
    from ..client_support.map_jump_destinations import KH02_MAPS
    from tools.kh3.native_integration import native_project_root
    root = native_project_root()
    metadata = json.loads((root / "authoring/kh02/runtime_metadata.json").read_text())
    for area in metadata["areas"]:
        assert map_path(f"dw_{area['area']:02}") == area["package"]
    rooms = (root / "include/kh02/mirror_host_metadata.h").read_text()
    for area, host, package, alias in KH02_MAPS:
        assert map_path(host.lower()) == map_path(alias) == package
        if area not in (2, 3):
            assert f'L"{host}",L"{package}"' in rooms


def test_old_probe_or_disconnected_game_rejects_command(recovery):
    service, transport, messages = recovery
    transport.accept([(SET, "runtime_context.json", 5, b"{}")])
    assert not service.gameflow("TT")
    assert "Update it" in messages[-1]
    transport.connected = False
    assert not service.gameflow("TT")
    assert transport.read(GAMEFLOW) is None


@pytest.mark.parametrize("change", ["disconnect", "reconnect", "epoch", "bridge", "timeout"])
def test_session_change_or_timeout_cancels_without_retry(recovery, change):
    service, transport, messages = recovery
    service.gameflow("TT", "2")
    if change == "disconnect":
        transport.connected = False
        transport.discard_recovery_commands()
    elif change == "reconnect":
        transport.connection_generation += 1
    elif change == "epoch":
        transport.epoch += 1
    elif change == "bridge":
        service.ctx.bridge_dir = None
    else:
        service.active["deadline"] = 0
    service.poll()
    assert service.active is None
    assert transport.read(GAMEFLOW) is None
    assert "not be retried" in messages[-1]


def test_busy_and_waiting_and_stale_response(recovery):
    service, transport, messages = recovery
    service.gameflow("TT", "2")
    assert not service.mapjump("GoA_01", "NormalPlayerStart")
    reply(transport, request_id="old", consume=False, flag_value=1)
    service.poll()
    assert service.active["phase"] == "read"
    reply(transport, process_id=999, consume=False, flag_value=1)
    service.poll()
    assert service.active["phase"] == "read"
    reply(transport, status="waiting", reason="missing_safe_sora_pawn", consume=False)
    service.poll()
    count = len(messages)
    service.poll()
    assert len(messages) == count and "waiting" in messages[-1]
    reply(transport, status="failed", reason="no_flag")
    service.poll()
    assert service.active is None and "no_flag" in messages[-1]


def test_no_op_write_and_readback_mismatch(recovery):
    service, transport, messages = recovery
    service.gameflow("TT", "2")
    reply(transport, flag_value=2)
    service.poll()
    assert service.active is None and "already set" in messages[-1]
    service.gameflow("TT", "3")
    reply(transport, flag_value=2)
    service.poll()
    reply(transport, game_flag_set=True)
    service.poll()
    reply(transport, flag_value=2)
    service.poll()
    assert service.active is None and "does not match" in messages[-1]


def test_cancel_does_not_delete_another_request(recovery):
    service, transport, _ = recovery
    service.gameflow("TT")
    transport.write(GAMEFLOW, {"request_id": "new-owner"})
    service.cancel()
    assert transport.read(GAMEFLOW) == {"request_id": "new-owner"}


def test_transport_discards_recovery_but_keeps_gameplay_on_disconnect(recovery):
    service, transport, _ = recovery
    service.gameflow("TT")
    transport.write(WARP, {"request_id": "warp"})
    transport.write("runtime_item_grant.json", {"request_id": "grant"})
    transport.discard_recovery_commands()
    assert GAMEFLOW not in transport.pending and WARP not in transport.pending
    assert transport.read(GAMEFLOW) is None and transport.read(WARP) is None
    assert transport.read("runtime_item_grant.json") == {"request_id": "grant"}


def test_kh02_checkpoint_uses_separate_bank_for_every_phase(recovery):
    service, transport, messages = recovery
    KH3CommandProcessor(service.ctx)("/gameflow kh02 110")
    for fields in ({"flag_value": 102}, {"game_flag_set": True}, {"flag_value": 110}):
        request = transport.read(GAMEFLOW)
        assert request["flag_name"] == "gameflow_DW"
        assert request["flag_bank"] == "kh02"
        reply(transport, flag_bank="kh02", **fields)
        service.poll()
    assert service.active is None
    assert messages[-1] == "kh02:gameflow_DW: 102 -> 110 (verified)."
    KH3CommandProcessor(service.ctx)("/gameflow kh02:DW_GEAR_GETCOUNT")
    assert transport.read(GAMEFLOW)["flag_name"] == "gameflow_DW_GEAR_GETCOUNT"
    reply(transport, flag_bank="kh02", flag_value=0)
    service.poll()
    assert messages[-1] == "kh02:gameflow_DW_GEAR_GETCOUNT = 0."


@pytest.mark.parametrize("name,value", [("kh02", 9999), ("kh02:dw", 300)])
def test_kh02_same_checkpoint_still_repairs_previous_story(recovery, name, value):
    service, transport, messages = recovery
    KH3CommandProcessor(service.ctx)(f"/gameflow {name} {value}")
    reply(transport, flag_bank="kh02", flag_value=value)
    service.poll()
    request = transport.read(GAMEFLOW)
    assert request["command"] == "set_game_flag" and request["flag_value"] == value
    assert request["flag_bank"] == "kh02"
    reply(transport, flag_bank="kh02", game_flag_set=True)
    service.poll()
    reply(transport, flag_bank="kh02", flag_value=value)
    service.poll()
    assert service.active is None and f"{value} -> {value} (verified)." in messages[-1]


def test_kh02_individual_flag_keeps_no_op_write(recovery):
    service, transport, messages = recovery
    service.gameflow("kh02:REBELLIS_CLEAR", "1")
    reply(transport, flag_bank="kh02", flag_value=1)
    service.poll()
    assert service.active is None and "already set" in messages[-1]


@pytest.mark.parametrize("argument", ["kh02 reset", "kh02 0", "kh02:dw 0"])
def test_kh02_reset_waits_for_authoritative_reset_ack(recovery, argument):
    service, transport, messages = recovery
    KH3CommandProcessor(service.ctx)("/gameflow " + argument)
    request = transport.read(GAMEFLOW)
    assert request["command"] == "reset_gameflow" and request["flag_bank"] == "kh02"
    reply(transport, status="waiting", reason="readback", consume=False)
    service.poll()
    assert service.active is not None
    reply(transport, flag_bank="kh02", kh02_reset=True, progress=None, revision=12,
          opened_chests_after=0, other_chest_bits_unchanged=True)
    service.poll()
    assert service.active is None and "reset (verified)" in messages[-1]
    assert "AP checks and received items are preserved" in messages[-1]


@pytest.mark.parametrize("name", ["DW", "kh02:DW_GEAR_GETCOUNT"])
def test_zero_remains_an_ordinary_write_for_other_flags(recovery, name):
    service, transport, _ = recovery
    assert service.gameflow(name, "0")
    reply(transport, flag_bank="kh02", flag_value=10)
    service.poll()
    request = transport.read(GAMEFLOW)
    assert request["command"] == "set_game_flag" and request["flag_value"] == 0


@pytest.mark.parametrize("fields", [{}, {"flag_bank": "kh02"},
                                   {"flag_bank": "kh02", "kh02_reset": True, "revision": 1},
                                   {"flag_bank": "kh02", "kh02_reset": True, "progress": {}, "revision": 1}])
def test_kh02_reset_rejects_incomplete_or_wrong_bank_proof(recovery, fields):
    service, transport, messages = recovery
    service.gameflow("kh02", "reset")
    reply(transport, **fields)
    service.poll()
    assert service.active is None and "did not confirm" in messages[-1]


@pytest.mark.parametrize("name,value", [("DW", "reset"), ("kh02:DW_GEAR_GETCOUNT", "reset"),
                                       ("kh02:", ""), ("kh02:DW/other", "")])
def test_invalid_reset_or_kh02_name_does_not_dispatch(recovery, name, value):
    service, transport, _ = recovery
    assert not service.gameflow(name, value)
    assert transport.read(GAMEFLOW) is None


def test_kh02_capability_required_without_disabling_ordinary_recovery(recovery):
    service, transport, messages = recovery
    transport.accept([(SET, "runtime_context.json", 5, b'{"process_id":123,"recovery_protocol":1}')])
    assert not service.gameflow("kh02")
    assert "updated diagnostic probe" in messages[-1]
    assert transport.read(GAMEFLOW) is None
    assert service.gameflow("DW")
    assert "flag_bank" not in transport.read(GAMEFLOW)
