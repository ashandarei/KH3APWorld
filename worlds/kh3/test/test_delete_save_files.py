import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from worlds.kh3.client_support.save_management import delete_confirmed_save_files
from worlds.kh3.gui.config import ConfigView


def test_delete_only_save_payloads(tmp_path):
    root = tmp_path / "SaveGames"
    nested = root / "kh3sv2" / "data"
    nested.mkdir(parents=True)
    payloads = [root / "RandomizerLevelUp.sav", nested / "KHIII_slot0.bin", nested / "KHIII_system.bin"]
    kept = [root / "ModLoaderSaves.sav", root / "settings.json", root / "other.dll", root / "save.bak"]
    backup = tmp_path / "SaveGames-backup" / "KHIII_slot0.bin"
    backup.parent.mkdir()
    for path in [*payloads, *kept, backup]:
        path.write_bytes(b"fixture")
    assert delete_confirmed_save_files(root) == {"removed_files": 3, "errors": []}
    assert all(not path.exists() for path in payloads)
    assert all(path.exists() for path in [*kept, backup])
    assert root.exists()


@pytest.mark.parametrize("answers,deleted", [([None], False), ([{}, {}], True)])
def test_one_confirmation_is_required(tmp_path, answers, deleted):
    view = ConfigView.__new__(ConfigView)
    view.ctx = SimpleNamespace(bridge_save_dir=tmp_path, kh3_run_action_running=False)
    view.app = SimpleNamespace(action_running=False, form=AsyncMock(side_effect=answers))
    with patch("worlds.kh3.platform_support.process_status.is_process_running", return_value=False), patch(
        "worlds.kh3.client_support.save_management.delete_confirmed_save_files",
        return_value={"removed_files": 2, "errors": []},
    ) as delete:
        asyncio.run(view.delete_save_files())
        assert delete.called is deleted
        if deleted:
            delete.assert_called_once_with(tmp_path.resolve())
            assert view.app.form.await_count == 2  # Confirmation, then result notice.
            assert view.app.form.await_args_list[0].kwargs["submit"] == "Delete Save Files"
    assert not view.app.action_running


@pytest.mark.parametrize("changed,running", [(True, False), (False, True)])
def test_rechecks_directory_and_game_after_confirmation(tmp_path, changed, running):
    view = ConfigView.__new__(ConfigView)
    view.ctx = SimpleNamespace(bridge_save_dir=tmp_path, kh3_run_action_running=False)
    async def form(title, *args, **kwargs):
        if title == "Delete Save Files" and changed:
            view.ctx.bridge_save_dir = tmp_path / "another"
        return {}
    view.app = SimpleNamespace(action_running=False, form=form)
    with patch("worlds.kh3.platform_support.process_status.is_process_running", side_effect=[False, running]), patch(
        "worlds.kh3.client_support.save_management.delete_confirmed_save_files"
    ) as delete:
        asyncio.run(view.delete_save_files())
        delete.assert_not_called()
    assert not view.app.action_running
