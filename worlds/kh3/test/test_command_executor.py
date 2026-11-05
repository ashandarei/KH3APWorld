"""Command effects preserve asynchronous acknowledgement rules."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support import command_executor as execution
from ..client_support.client_command_service import ClientCommandAction as Action
from ..client_support.client_command_service import ClientCommandActionKind as Kind
from ..client_support.client_command_service import ClientCommandPlan as Plan


@pytest.fixture
def commands(tmp_path):
    callbacks = execution.CommandExecutionCallbacks(
        bridge=Mock(return_value=tmp_path / "bridge"),
        root=Mock(return_value=tmp_path / "egs"),
        mods=Mock(return_value=tmp_path / "mods"),
        saves=Mock(return_value=tmp_path / "saves"),
        probe_directory=Mock(return_value=tmp_path / "probe"),
        write_bridge=Mock(),
        delete_sidecar=Mock(),
        install_runtime=Mock(),
        invalidate_goa=Mock(),
        export_debug=Mock(),
        send=AsyncMock(),
        acknowledge=Mock(),
    )
    return execution.ClientCommandExecutor(callbacks), callbacks


@pytest.mark.parametrize("error", [OSError("download"), asyncio.CancelledError()])
def test_download_failure_always_invalidates_goa_inspection(commands, monkeypatch, error):
    executor, callbacks = commands
    monkeypatch.setattr(execution, "download_goa_mod", Mock(side_effect=error))
    with pytest.raises(type(error)):
        executor.apply(Action(Kind.DOWNLOAD_GOA_MOD))
    callbacks.invalidate_goa.assert_called_once()


@pytest.mark.parametrize(
    "kind,provider,description",
    [
        (Kind.OPEN_KH3_DIRECTORY, "root", "KH3 directory"),
        (Kind.OPEN_KH3_MODS_DIRECTORY, "mods", "KH3 mods directory"),
        (Kind.OPEN_KH3_SAVE_DIRECTORY, "saves", "KH3 save directory"),
        (Kind.OPEN_PROBE_CONFIG_DIRECTORY, "probe_directory", "KH3 probe config directory"),
    ],
)
def test_directory_commands_use_their_current_path_and_existing_open_policy(
    commands, monkeypatch, kind, provider, description
):
    executor, callbacks = commands
    opener = Mock()
    monkeypatch.setattr(execution, "open_path", opener)
    executor.apply(Action(kind))
    opener.assert_called_once_with(
        getattr(callbacks, provider).return_value, description=description, create_directory=True
    )


def test_launch_reads_the_root_after_prior_plan_effects(commands, monkeypatch):
    executor, callbacks = commands
    callbacks.write_bridge.side_effect = lambda: setattr(callbacks.root, "return_value", Path("changed-egs-root"))
    launch = Mock()
    monkeypatch.setattr(execution, "launch_kh3_game", launch)
    executor.apply_plan(Plan(actions=(Action(Kind.WRITE_BRIDGE_FILES), Action(Kind.LAUNCH_KH3))))
    launch.assert_called_once_with(Path("changed-egs-root"))


def test_bridge_acknowledgement_and_deletion_wait_for_the_send(commands):
    executor, callbacks = commands

    async def run_case():
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(messages):
            assert messages == [{"cmd": "Say", "text": "!hint"}]
            entered.set()
            await release.wait()

        callbacks.send.side_effect = send
        task = asyncio.create_task(
            executor.apply_bridge_plan(
                Plan(
                    actions=(
                        Action(Kind.SEND_SAY, text="!hint"),
                        Action(Kind.ACKNOWLEDGE, sequence=5),
                        Action(Kind.DELETE_COMMAND_FILE, text="ap_client_command.json"),
                    )
                )
            )
        )
        await entered.wait()
        callbacks.acknowledge.assert_not_called()
        callbacks.delete_sidecar.assert_not_called()
        release.set()
        await task
        callbacks.acknowledge.assert_called_once_with(5)
        callbacks.delete_sidecar.assert_called_once_with("ap_client_command.json")

    asyncio.run(run_case())


@pytest.mark.parametrize("error", [OSError("send"), asyncio.CancelledError()])
def test_failed_or_cancelled_bridge_send_retains_the_unacknowledged_command(commands, error):
    executor, callbacks = commands
    callbacks.send.side_effect = error
    with pytest.raises(type(error)):
        asyncio.run(
            executor.apply_bridge_plan(
                Plan(
                    actions=(
                        Action(Kind.SEND_SAY, text="!hint"),
                        Action(Kind.ACKNOWLEDGE, sequence=5),
                        Action(Kind.DELETE_COMMAND_FILE, text="ap_client_command.json"),
                    )
                )
            )
        )
    callbacks.acknowledge.assert_not_called()
    callbacks.delete_sidecar.assert_not_called()
