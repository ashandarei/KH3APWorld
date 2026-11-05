from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..kh02 import linux_source_access as access


class PortalPath(PurePosixPath):
    @classmethod
    def home(cls):
        return cls("/home/player")

    def is_file(self):
        return True

    def is_dir(self):
        return False


@pytest.fixture
def portal(monkeypatch):
    monkeypatch.setattr(access, "os", SimpleNamespace(name="posix", getuid=lambda: 1000))
    monkeypatch.setattr(access, "Path", PortalPath)
    monkeypatch.setattr(access.shutil, "which", lambda _: "/usr/bin/flatpak")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="/run/user/1000/doc/abcd/KH02\n", stderr=""))
    monkeypatch.setattr(access.subprocess, "run", run)
    return run


HOST = PortalPath("/home/player/.var/app/com.valvesoftware.Steam/steamapps/common/KH3")
EXE = PortalPath("/home/player/Games/KH02/Binaries/Win64/game.exe")


def test_external_source_gets_scoped_portal_access(portal):
    assert access.source_executable_for_host(HOST, EXE) == PortalPath(
        "/run/user/1000/doc/abcd/KH02/Binaries/Win64/game.exe")
    args = portal.call_args.args[0]
    assert args == ["/usr/bin/flatpak", "document-export", "--app=com.valvesoftware.Steam",
                    "--allow-read", "--allow-write", "/home/player/Games/KH02"]


def test_native_steam_and_internal_source_need_no_export(portal):
    assert access.source_executable_for_host(PortalPath("/home/player/.steam/KH3"), EXE) == EXE
    internal = HOST.parent / "KH02/Binaries/Win64/game.exe"
    assert access.source_executable_for_host(HOST, internal) == internal
    portal.assert_not_called()


def test_collection_engine_dependencies_remain_visible(portal, monkeypatch):
    monkeypatch.setattr(PortalPath, "is_dir", lambda self: str(self) == "/home/player/Games/Engine")
    portal.return_value.stdout = "/run/user/1000/doc/abcd/Games"
    assert access.source_executable_for_host(HOST, EXE) == PortalPath(
        "/run/user/1000/doc/abcd/Games/KH02/Binaries/Win64/game.exe")
    assert portal.call_args.args[0][-1] == "/home/player/Games"


@pytest.mark.parametrize("path", ["/tmp/KH02", "/run/user/999/doc/abcd/KH02",
                                  "/run/user/1000/doc/abcd/Other", "/run/user/1000/doc/../KH02"])
def test_unexpected_portal_paths_fail_closed(portal, path):
    portal.return_value.stdout = path
    with pytest.raises(OSError, match="unexpected"):
        access.source_executable_for_host(HOST, EXE)


def test_portal_failure_does_not_write_an_unusable_config(portal):
    portal.return_value.returncode = 1
    portal.return_value.stderr = "No portal"
    with pytest.raises(OSError, match="No portal"):
        access.source_executable_for_host(HOST, EXE)
