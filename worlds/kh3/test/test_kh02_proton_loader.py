"""The owned source process must load UE4SS in the selected host's prefix."""
from types import SimpleNamespace

from ..installer_support import platform_config


def test_companion_override_uses_host_prefix_and_preserves_other_apps(tmp_path, monkeypatch):
    registry = tmp_path / "host-prefix/user.reg"
    registry.parent.mkdir()
    original = ('WINE REGISTRY Version 2\n\n'
                '[Software\\\\Wine\\\\AppDefaults\\\\KINGDOM HEARTS III.exe\\\\DllOverrides] 1\n'
                '"dwmapi"="native,builtin"\n"xinput1_3"="builtin"\n\n'
                '[Software\\\\Wine\\\\AppDefaults\\\\KINGDOM HEARTS 0.2 Birth by Sleep.exe\\\\DllOverrides] 1\n'
                '"dwmapi"="builtin"\n"other"="native"\n')
    registry.write_text(original)
    host = tmp_path / "selected-host"
    calls = []
    monkeypatch.setattr(platform_config, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(platform_config, "proton_user_registry_path_for_kh3_root",
                        lambda selected: calls.append(selected) or registry)

    changed = platform_config.ensure_proton_kh02_dwmapi_override(host, "first")
    assert changed["changed"] and not changed["errors"]
    assert type(registry)(changed["backup"]).read_text() == original
    updated = registry.read_text()
    assert '"xinput1_3"="builtin"' in updated and '"other"="native"' in updated
    assert updated.count('"dwmapi"="native,builtin"') == 2
    assert '"dwmapi"="builtin"' not in updated
    repeated = platform_config.ensure_proton_kh02_dwmapi_override(host, "repeat")
    assert not repeated["changed"] and repeated["backup"] is None
    assert registry.read_text() == updated
    assert calls == [host, host]


def test_windows_companion_setup_does_not_touch_a_wine_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_config, "os", SimpleNamespace(name="nt"))
    result = platform_config.ensure_proton_kh02_dwmapi_override(tmp_path, "unused")
    assert result["reason"] == "windows" and not result["changed"]
