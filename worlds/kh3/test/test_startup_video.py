import zipfile
from unittest.mock import patch

import pytest

from worlds.kh3.client_support import fast_new_game, startup_video


def game(root):
    exe = root / "Binaries/Win64/KINGDOM HEARTS III.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    for language in ("en", "ja"):
        target = root / "Content/Movies" / language / "Copyright.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"original movie")
    return root


@pytest.mark.parametrize("nested", [False, True])
def test_permanent_replacement_and_idempotency(tmp_path, nested):
    outer = tmp_path / "KINGDOM HEARTS III"
    root = game(outer / "KINGDOM HEARTS III" if nested else outer)
    assert startup_video.install_short_copyright_video(outer, enabled=False) == 0
    assert startup_video.install_short_copyright_video(outer, enabled=True) == 2
    targets = list((root / "Content/Movies").rglob("*.mp4"))
    assert all(startup_video.hashlib.sha256(p.read_bytes()).hexdigest() == startup_video.VIDEO_SHA256 for p in targets)
    assert startup_video.install_short_copyright_video(outer, enabled=True) == 0
    assert startup_video.install_short_copyright_video(outer, enabled=False) == 0
    assert sorted(p.name for p in (root / "Content/Movies").rglob("*") if p.is_file()) == ["Copyright.mp4"] * 2
    targets[0].write_bytes(b"restored by game update")
    assert startup_video.install_short_copyright_video(outer, enabled=True) == 1


def test_resource_can_be_read_from_zip(tmp_path):
    payload = startup_video._runtime_resource("runtime", "startup", "Copyright.mp4").read_bytes()
    archive = tmp_path / "test.apworld"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("kh3/runtime/startup/Copyright.mp4", payload)
    with zipfile.ZipFile(archive) as source, patch.object(startup_video, "_runtime_resource",
            return_value=zipfile.Path(source, "kh3/runtime/startup/Copyright.mp4")):
        assert startup_video.install_short_copyright_video(game(tmp_path / "game"), enabled=True) == 2


def test_failed_replacement_preserves_original_and_preferences(tmp_path):
    root = game(tmp_path / "game")
    enabled = {**fast_new_game.DEFAULTS, "enabled": True}
    with patch.object(startup_video.os, "replace", side_effect=PermissionError("video is locked")), \
         patch.object(fast_new_game.Utils, "persistent_store") as store:
        with pytest.raises(PermissionError):
            fast_new_game.save_settings(enabled, tmp_path / "bridge", root)
        store.assert_not_called()
    assert not (tmp_path / "bridge" / fast_new_game.SETTINGS_FILE).exists()
    assert not list(root.rglob(".kh3-copyright-*"))
    assert (root / "Content/Movies/en/Copyright.mp4").read_bytes() == b"original movie"
