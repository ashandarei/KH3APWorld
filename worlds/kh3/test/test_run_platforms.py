"""Storefront isolation without using installed games or real save folders."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ..Client import KH3Context
from ..client_support.run_manager import KH3RunManagerError, KH3RunManagerService
from ..client_support.seed_guard import SeedGuardService
from .test_auto_activation import NEW, write_pak


def manager(root, platform, **kwargs):
    return KH3RunManagerService(
        run_root=root / "runs", bridge_root=root / "bridge",
        mods_dir=root / platform / "mods", save_dir=root / platform / "saves",
        process_running=lambda: False, platform=platform, **kwargs,
    )


def enroll(service, port=38281):
    pak = write_pak(service.mods_dir, NEW)
    return service.ensure_pak_managed(
        pak, expected_seed_id=NEW, server_address=f"localhost:{port}", connection_slot_name="Sora",
    ).run


def test_same_seed_has_independent_platform_records_snapshots_and_active_markers(tmp_path):
    steam, egs = manager(tmp_path, "steam"), manager(tmp_path, "egs")
    steam_run, egs_run = enroll(steam), enroll(egs)
    assert steam_run.run_id != egs_run.run_id
    assert steam_run.save_snapshot_dir != egs_run.save_snapshot_dir
    relative = Path("kh3sv2/data/KHIII_slot1.bin")
    for service, run, contents in ((steam, steam_run, b"steam"), (egs, egs_run, b"egs")):
        live = service.save_dir / relative
        live.parent.mkdir(parents=True)
        live.write_bytes(contents)
        service._snapshot_current_save(run, service.save_dir)
        service.activate(run.run_id, active_save_seed_id=NEW, active_save_run_id=run.run_id)
        payload = json.loads((run.run_dir / "run.json").read_text())
        assert payload["platform"] == service.platform
    assert (steam_run.save_snapshot_dir / relative).read_bytes() == b"steam"
    assert (egs_run.save_snapshot_dir / relative).read_bytes() == b"egs"
    assert steam._active_run_path != egs._active_run_path
    assert manager(tmp_path, "steam").active_connection_profile().run_id == steam_run.run_id
    assert manager(tmp_path, "egs").active_connection_profile().run_id == egs_run.run_id
    assert enroll(steam, port=38282).run_id == steam_run.run_id
    assert {run.platform for run in steam.list_runs()} == {"steam", "egs"}
    assert not steam.get_run(egs_run.run_id).active
    assert not steam.get_run(egs_run.run_id).installed
    with pytest.raises(KH3RunManagerError, match="installation"):
        steam.activate(egs_run.run_id)
    with pytest.raises(KH3RunManagerError, match="another platform"):
        steam.delete_inactive_run(egs_run.run_id, expected_active_run_id=steam_run.run_id)
    assert (egs_run.save_snapshot_dir / relative).read_bytes() == b"egs"


def test_runtime_platform_change_invalidates_inventory_and_connection_profile(tmp_path):
    steam, egs = manager(tmp_path, "steam"), manager(tmp_path, "egs")
    steam_run, egs_run = enroll(steam), enroll(egs)
    selected = ["steam"]
    service = KH3RunManagerService(
        run_root=tmp_path / "runs", bridge_root=tmp_path / "bridge",
        mods_dir=lambda: tmp_path / selected[0] / "mods",
        save_dir=lambda: tmp_path / selected[0] / "saves",
        process_running=lambda: False, platform=lambda: selected[0],
    )
    assert service.active_connection_profile().run_id == steam_run.run_id
    selected[0] = "egs"
    assert service.active_connection_profile().run_id == egs_run.run_id


@pytest.mark.parametrize("platform", ["steam", "egs"])
@pytest.mark.parametrize("unassigned", [False, True])
@pytest.mark.parametrize("action", ["single", "mixed", "automatic"])
def test_connected_manual_deletion_crosses_platforms_but_automatic_cleanup_does_not(
    tmp_path, platform, unassigned, action,
):
    service = manager(tmp_path, platform)
    active = enroll(service)
    other_platform = "" if unassigned else ("egs" if platform == "steam" else "steam")
    other = manager(tmp_path, other_platform)
    target = enroll(other)
    first = service.import_pak(write_pak(tmp_path / "downloads", "33333333333333333333"))
    last = service.import_pak(write_pak(tmp_path / "downloads", "44444444444444444444"))
    service._write_active_run_id(active.run_id)
    bridge = service.bridge_root / active.bridge_dir_name
    bridge.mkdir(parents=True)
    (bridge / "state.json").write_text("{}")
    target.save_snapshot_dir.mkdir(parents=True)
    (target.save_snapshot_dir / "KHIII_slot1.bin").write_bytes(b"stored AP save")
    service.process_running = lambda: True
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = bridge
    ctx.kh3_active_connection_profile_run_id = active.run_id
    ctx.kh3_run_action_running = action == "automatic"
    ctx.auto_delete_old_kh3_runs = True
    ctx._get_run_manager_service = Mock(return_value=service)
    ctx._is_ap_connected = Mock(return_value=True)
    ctx.disconnect = Mock(side_effect=AssertionError("unexpected disconnect"))
    ctx.stop_integrated_bridge = Mock(side_effect=AssertionError("unexpected watcher stop"))

    with patch.object(service, "_iter_gameplay_save_files", side_effect=AssertionError("live save inspection")):
        if action == "automatic":
            asyncio.run(ctx._auto_delete_old_kh3_runs_after_start(active.run_id))
        elif action == "single":
            asyncio.run(ctx._delete_kh3_run_action(target.run_id))
        else:
            asyncio.run(ctx._delete_kh3_runs_action((first.run_id, target.run_id, last.run_id)))

    assert target.run_dir.exists() is (action == "automatic")
    assert first.run_dir.exists() is (action == "single")
    assert last.run_dir.exists() is (action == "single")
    assert active.run_dir.exists() and active.installed_pak_path.exists()
    assert service._read_active_run_id() == active.run_id
    assert (bridge / "state.json").exists()
    assert target.installed_pak_path.exists()
    assert ctx.kh3_active_connection_profile_run_id == active.run_id
    assert ctx.kh3_run_action_running is (action == "automatic")


def test_legacy_record_is_reused_only_for_proven_platform(tmp_path):
    legacy = manager(tmp_path, "")
    old = enroll(legacy)
    # Only randomizer-owned metadata supplies this association; no save-file inference.
    def infer():
        return {old.run_id: 'egs'}
    egs = manager(tmp_path, "egs", legacy_platforms=infer)
    steam = manager(tmp_path, "steam", legacy_platforms=infer)
    assert enroll(egs).run_id == old.run_id
    assert enroll(steam).run_id != old.run_id
    payload = json.loads((old.run_dir / "run.json").read_text())
    assert payload["platform"] == "egs"


def test_legacy_platform_inference_rejects_shared_or_unknown_scope():
    def record(run):
        return {"seed_id": NEW, "guard_version": 2, "run_id": run}
    assert SeedGuardService.legacy_run_platforms({"save_seed_associations": {
        "C:/saves/Steam/123/SaveGames": record("shared"),
        "C:/saves/Epic Games Store/abc/SaveGames": record("shared"),
        "C:/saves/Steam/456/SaveGames": record("steam-only"),
        "C:/unknown": record("unknown"),
    }}) == {"steam-only": "steam"}


def test_platform_hover_uses_existing_columns_and_changes_with_row():
    from ..gui.save_manager import SaveManagerView
    view = SaveManagerView.__new__(SaveManagerView)
    view.runs = {name: SimpleNamespace(platform=name) for name in ("steam", "egs", "")}
    view.tree = Mock()
    view.platform_tooltip = Mock()
    view.hover_run_id = None
    for name, label in (("steam", "Steam"), ("egs", "Epic Games Store")):
        view.tree.identify_row.return_value = name
        view.hover_platform(SimpleNamespace(y=10))
        assert view.platform_tooltip_text() == label
    view.hide_platform_tooltip()
    assert view.hover_run_id is None


def test_bulk_delete_only_offers_current_platform():
    import asyncio
    from unittest.mock import AsyncMock

    from ..gui.save_manager import SaveManagerView
    view = SaveManagerView.__new__(SaveManagerView)
    runs = [SimpleNamespace(run_id="current", platform="steam", active=True),
            SimpleNamespace(run_id="old-steam", platform="steam", active=False),
            SimpleNamespace(run_id="keep-egs", platform="egs", active=False)]
    view.ctx = SimpleNamespace(kh3_run_action_running=False, list_kh3_runs=lambda **_: runs,
                               request_delete_inactive_kh3_runs=Mock())
    view.app = SimpleNamespace(form=AsyncMock(return_value={}))
    asyncio.run(view.delete_others())
    view.ctx.request_delete_inactive_kh3_runs.assert_called_once_with(("old-steam",), "current")


def test_connection_profile_edit_keeps_platform_identity(tmp_path):
    steam = manager(tmp_path, "steam")
    run = enroll(steam)
    updated = steam.update_connection_profile(
        run.run_id, slot_name="Sora", server="another.example", port="38281", room_url="",
    )
    assert updated.platform == "steam"
    assert "--steam--" in updated.run_id
    assert updated.run_id != run.run_id


def test_proven_legacy_active_marker_survives_multiple_server_profiles(tmp_path):
    legacy = manager(tmp_path, "")
    first = enroll(legacy)
    second = legacy.ensure_pak_managed(
        legacy.mods_dir / first.pak_name, expected_seed_id=NEW,
        server_address="other.example:38281", connection_slot_name="Sora",
    ).run
    legacy._write_active_run_id(second.run_id)
    egs = manager(tmp_path, "egs", legacy_platforms=lambda: {first.run_id: "egs", second.run_id: "egs"})
    write_pak(egs.mods_dir, NEW)
    assert egs.active_connection_profile().run_id == second.run_id
