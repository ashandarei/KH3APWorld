import pytest

from worlds.kh3.platform_support import process_status


def test_live_process_uses_verified_identity_and_returns_independent_status(monkeypatch):
    original = {"running": True, "pid": 42, "started_at_utc": "start"}
    monkeypatch.setattr(process_status, "_windows_process_identities", {"kh3.exe": (42, 1234, original)})
    queried = []

    def query(pid):
        queried.append(pid)
        return 1234, "start"

    result = process_status._cached_windows_process_status("kh3.exe", query)
    assert queried == [42]
    assert result == original
    result["running"] = False
    assert original["running"] is True


@pytest.mark.parametrize("observed", [None, (5678, "a different process with the same PID")])
def test_exit_or_pid_reuse_invalidates_cached_identity(monkeypatch, observed):
    monkeypatch.setattr(process_status, "_windows_process_identities",
        {"kh3.exe": (42, 1234, {"running": True, "pid": 42})})
    assert process_status._cached_windows_process_status("kh3.exe", lambda pid: observed) is None
    assert "kh3.exe" not in process_status._windows_process_identities


def test_missing_identity_requires_enumeration(monkeypatch):
    monkeypatch.setattr(process_status, "_windows_process_identities", {})
    assert process_status._cached_windows_process_status("kh3.exe", lambda pid: pytest.fail("unexpected query")) is None
