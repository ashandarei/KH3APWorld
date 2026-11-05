"""The host client and sandboxed game must share one bridge directory."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ..client_support.context.run_library import ClientRunLibraryMixin
from ..client_support.context.seed_policy import ClientSeedPolicyMixin


class Context(ClientRunLibraryMixin, ClientSeedPolicyMixin):
    pass


@pytest.mark.parametrize("platform,override,expected", [
    ("linux", None, "game/Archipelago/bridge"),
    ("win32", None, "user/kh3/bridge"),
    ("linux", "custom", "custom"),
])
def test_run_library_and_offline_startup_share_bridge(tmp_path, monkeypatch, platform, override, expected):
    monkeypatch.setattr("worlds.kh3.client_support.context.run_library.sys.platform", platform)
    context = Context()
    context.kh3_dir = tmp_path / "game"
    context.bridge_root_override = tmp_path / override if override else None
    context._client_api = SimpleNamespace(
        Path=Path,
        Utils=SimpleNamespace(user_path=lambda *parts: str(tmp_path / "user" / Path(*parts))),
        OfflineBridgeInitializationInput=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    context.bridge_dir = None
    context.kh3_mods_dir = context.kh3_dir / "Content/Paks/~mods"
    context.expected_seed_id = lambda: "fresh-seed"
    context._active_kh3_bridge_dir_name = lambda: ""
    context.auth = "Player"
    context.server_address = None
    context.team = 0
    root = (tmp_path / expected).resolve()
    assert context.kh3_bridge_root() == root
    assert context._offline_bridge_initialization_input().bridge_root == root
