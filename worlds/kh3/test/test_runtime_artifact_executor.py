"""Artifact execution keeps effect order and invalidates partial file changes."""

from pathlib import Path
from unittest.mock import Mock
from unittest.mock import call

import pytest

from ..client_support import custom_music, fast_new_game
from ..client_support import runtime_artifact_executor as executor_module
from ..client_support.runtime_artifact_executor import RuntimeArtifactCallbacks, RuntimeArtifactExecutor
from ..client_support.runtime_artifact_service import RuntimeArtifactAction, RuntimeArtifactPlan, RuntimeArtifactService
from ..client_support.runtime_artifact_service import RuntimeArtifactActionKind as Kind
from ..platform_support import uobject_config


@pytest.mark.parametrize("custom", [False, True])
def test_egs_configuration_reaches_active_layout_and_preserves_custom_override(execution, tmp_path, custom):
    from ..installer_support.paths import EGS_GUOBJECT_ARRAY_SIGNATURE
    executor, callbacks, _, _, _, write, _ = execution
    root = tmp_path / "KH_3/KINGDOM HEARTS III"
    executable = root / "Binaries/Win64/KINGDOM HEARTS III.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(bytes(0 if token == "?" else int(token, 16)
                                for token in EGS_GUOBJECT_ARRAY_SIGNATURE.split()))
    callbacks.root.return_value = root
    config = root / ("custom/config.lua" if custom else "Binaries/Win64/Mods/KH3APProbeCpp/config.lua")
    executor.apply(RuntimeArtifactAction(Kind.WRITE_PROBE_CONFIG, path=config, bridge_dir=tmp_path / "bridge"))
    expected = [call(config, tmp_path / "bridge")]
    if not custom:
        expected.append(call(root / "Binaries/Win64/KH_3/Mods/KH3APProbeCpp/config.lua", tmp_path / "bridge"))
    assert write.call_args_list == expected


@pytest.fixture
def execution(monkeypatch):
    callbacks = RuntimeArtifactCallbacks(
        root=Mock(return_value=Path("egs")),
        mods=Mock(return_value=Path("egs/mods")),
        bridge=Mock(return_value=Path("bridge")),
        saves=Mock(return_value=Path("selected-saves")),
        invalidate_runtime=Mock(),
        invalidate_goa=Mock(),
        restore_vanilla=Mock(),
        disconnect_after_restore=Mock(),
        backup_vanilla=Mock(),
        ensure_goa=Mock(),
        configure_probe=Mock(),
    )
    planner = Mock()
    planner.plan_restore_report.return_value = RuntimeArtifactPlan(
        (RuntimeArtifactAction(Kind.DISCONNECT_AFTER_RESTORE),)
    )
    planner.plan_uninstall_report.return_value = RuntimeArtifactPlan()
    monkeypatch.setattr(executor_module, "provision_host", Mock())
    install = Mock(return_value={"status": "ok"})
    uninstall, probe = Mock(return_value={}), Mock()
    pool = Mock(return_value={"configured_capacity": 2097152, "path": "Engine.ini"})
    monkeypatch.setattr(executor_module, "install_kh3_runtime_files", install)
    monkeypatch.setattr(executor_module, "uninstall_kh3_ap_files", uninstall)
    monkeypatch.setattr(executor_module, "write_probe_config", probe)
    monkeypatch.setattr(uobject_config, "install_uobject_config", pool)
    return RuntimeArtifactExecutor(planner, callbacks), callbacks, planner, install, uninstall, probe, pool


@pytest.mark.parametrize("status", ["ok", "unchanged", "missing_kh3", "error"])
def test_only_successful_runtime_installs_configure_capacity_and_the_probe(execution, status):
    executor, callbacks, _, install, _, _, pool = execution
    install.return_value = {"status": status}
    executor.apply(RuntimeArtifactAction(Kind.INSTALL_RUNTIME_FILES))
    install.assert_called_once_with(Path("egs"), Path("bridge"))
    callbacks.invalidate_runtime.assert_called_once()
    if status in {"missing_kh3", "error"}:
        executor_module.provision_host.assert_not_called()
        pool.assert_not_called()
        callbacks.configure_probe.assert_not_called()
    else:
        executor_module.provision_host.assert_called_once_with(Path("egs"))
        pool.assert_called_once_with(Path("selected-saves"))
        callbacks.configure_probe.assert_called_once()


@pytest.mark.parametrize("kind", [Kind.INSTALL_RUNTIME_FILES, Kind.UNINSTALL_RUNTIME_ARTIFACTS])
def test_file_operation_failure_invalidates_inspection_and_propagates(execution, kind):
    executor, callbacks, planner, install, uninstall, _, _ = execution
    target = install if kind is Kind.INSTALL_RUNTIME_FILES else uninstall
    target.side_effect = OSError("partial operation")
    with pytest.raises(OSError, match="partial operation"):
        executor.apply(RuntimeArtifactAction(kind))
    callbacks.invalidate_runtime.assert_called_once()
    if kind is Kind.UNINSTALL_RUNTIME_ARTIFACTS:
        callbacks.invalidate_goa.assert_called_once()
    planner.plan_uninstall_report.assert_not_called()
    callbacks.configure_probe.assert_not_called()


@pytest.mark.parametrize("error", [OSError("capacity"), ValueError("capacity"), RuntimeError("capacity")])
def test_capacity_errors_keep_existing_install_error_policy(execution, error, caplog):
    executor, callbacks, _, _, _, _, pool = execution
    pool.side_effect = error
    if isinstance(error, RuntimeError):
        with pytest.raises(RuntimeError):
            executor.apply(RuntimeArtifactAction(Kind.INSTALL_RUNTIME_FILES))
        callbacks.configure_probe.assert_not_called()
    else:
        executor.apply(RuntimeArtifactAction(Kind.INSTALL_RUNTIME_FILES))
        callbacks.configure_probe.assert_called_once()
        assert "capacity override was not installed" in caplog.text
    callbacks.invalidate_runtime.assert_called_once()


@pytest.mark.parametrize("failure", [True, False])
def test_probe_inspection_is_invalidated_after_a_successful_config_write(execution, failure):
    executor, callbacks, _, _, _, probe, _ = execution
    if failure:
        probe.side_effect = OSError("locked")
    executor.apply(RuntimeArtifactAction(Kind.WRITE_PROBE_CONFIG, path=Path("config.lua"), bridge_dir=Path("bridge")))
    probe.assert_called_once_with(Path("config.lua"), Path("bridge"))
    assert callbacks.invalidate_runtime.call_count == (0 if failure else 1)


def test_install_plan_uses_current_paths_after_prior_effects(execution):
    executor, callbacks, _, install, _, _, _ = execution
    events = []
    callbacks.backup_vanilla.side_effect = lambda: (
        events.append("backup"),
        setattr(callbacks.root, "return_value", Path("new-egs")),
    )
    callbacks.ensure_goa.side_effect = lambda: events.append("goa")
    install.side_effect = lambda *args: events.append("install") or {"status": "ok"}
    callbacks.invalidate_runtime.side_effect = lambda: events.append("invalidate")
    callbacks.configure_probe.side_effect = lambda: events.append("probe")
    executor.apply_plan(
        RuntimeArtifactPlan(
            tuple(
                RuntimeArtifactAction(kind)
                for kind in (Kind.BACKUP_VANILLA_SAVES, Kind.ENSURE_GOA_MOD, Kind.INSTALL_RUNTIME_FILES)
            )
        )
    )
    assert events == ["backup", "goa", "install", "invalidate", "probe"]
    install.assert_called_once_with(Path("new-egs"), Path("bridge"))


def test_uninstall_report_runs_after_both_inspection_caches_are_invalidated(execution):
    executor, callbacks, planner, _, _, _, _ = execution
    events = []
    callbacks.invalidate_runtime.side_effect = lambda: events.append("runtime")
    callbacks.invalidate_goa.side_effect = lambda: events.append("goa")
    planner.normalize_uninstall_result.side_effect = lambda result: events.append("report") or result
    executor.apply(RuntimeArtifactAction(Kind.UNINSTALL_RUNTIME_ARTIFACTS))
    assert events == ["runtime", "goa", "report"]


def test_restore_report_can_disconnect_only_after_restore_returns(execution):
    executor, callbacks, _, _, _, _, _ = execution
    events = []
    callbacks.restore_vanilla.side_effect = lambda: events.append("restore") or {}
    callbacks.disconnect_after_restore.side_effect = lambda: events.append("disconnect")
    executor.apply(RuntimeArtifactAction(Kind.RESTORE_VANILLA_SAVES))
    assert events == ["restore", "disconnect"]


@pytest.fixture
def executor(tmp_path):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    callbacks = executor_module.RuntimeArtifactCallbacks(
        root=lambda: tmp_path,
        mods=lambda: tmp_path / "mods",
        bridge=lambda: bridge,
        saves=lambda: tmp_path / "saves",
        invalidate_runtime=Mock(),
        invalidate_goa=Mock(),
        restore_vanilla=Mock(),
        disconnect_after_restore=Mock(),
        backup_vanilla=Mock(),
        ensure_goa=Mock(),
        configure_probe=Mock(),
    )
    return executor_module.RuntimeArtifactExecutor(RuntimeArtifactService(), callbacks), callbacks, bridge


@pytest.mark.parametrize("music_failure", [False, True])
def test_probe_setup_keeps_music_fast_start_and_configuration_in_order(executor, monkeypatch, music_failure):
    service, callbacks, bridge = executor
    events = []

    def music(root):
        events.append(("music", root))
        if music_failure:
            raise OSError("music folder is locked")

    monkeypatch.setattr(custom_music, "sync_runtime", music)
    monkeypatch.setattr(fast_new_game, "sync_bridge", lambda path: events.append(("fast", path)))
    monkeypatch.setattr(executor_module, "kh3_win64_dir_from_root", lambda root: root)
    monkeypatch.setattr(
        executor_module, "write_probe_config", lambda path, target: events.append(("config", path, target))
    )
    config = callbacks.root() / "config.lua"
    service.configure_probe(config)
    assert events == [("music", callbacks.root()), ("fast", bridge), ("config", config, bridge)]
    callbacks.invalidate_runtime.assert_called_once()


@pytest.mark.parametrize("missing", ["bridge", "install", "config"])
def test_probe_setup_does_not_write_an_unavailable_install(executor, monkeypatch, missing):
    service, callbacks, _ = executor
    music, fast, write = Mock(), Mock(), Mock()
    monkeypatch.setattr(custom_music, "sync_runtime", music)
    monkeypatch.setattr(fast_new_game, "sync_bridge", fast)
    monkeypatch.setattr(executor_module, "write_probe_config", write)
    monkeypatch.setattr(
        executor_module, "kh3_win64_dir_from_root", lambda root: root / "missing" if missing == "install" else root
    )
    if missing == "bridge":
        from dataclasses import replace

        callbacks = replace(callbacks, bridge=lambda: None)
        service = executor_module.RuntimeArtifactExecutor(RuntimeArtifactService(), callbacks)
    service.configure_probe(None if missing == "config" else callbacks.root() / "config.lua")
    music.assert_called_once()
    fast.assert_called_once_with(callbacks.bridge())
    write.assert_not_called()
    callbacks.invalidate_runtime.assert_not_called()


def test_host_download_failure_propagates_without_configuring_probe(execution):
    executor, callbacks, _, _, _, _, pool = execution
    executor_module.provision_host.side_effect = ValueError("host verification")
    with pytest.raises(ValueError, match="host verification"):
        executor.apply(RuntimeArtifactAction(Kind.INSTALL_RUNTIME_FILES))
    callbacks.invalidate_runtime.assert_called_once()
    callbacks.configure_probe.assert_not_called()
    pool.assert_not_called()
