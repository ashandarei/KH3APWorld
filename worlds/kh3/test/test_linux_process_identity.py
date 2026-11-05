from ..platform_support.process_status import _get_process_status_linux_proc


def test_launcher_argument_is_not_a_running_game(tmp_path):
    wrapper = tmp_path / "100"
    wrapper.mkdir()
    image = b"/games/KINGDOM HEARTS III/Binaries/Win64/KINGDOM HEARTS III.exe"
    (wrapper / "cmdline").write_bytes(b"python3\0proton\0waitforexitandrun\0" + image + b"\0")
    assert _get_process_status_linux_proc("KINGDOM HEARTS III.exe", tmp_path) == {"running": False}
    game = tmp_path / "200"
    game.mkdir()
    (game / "cmdline").write_bytes(image + b"\0-private-arguments\0")
    status = _get_process_status_linux_proc("KINGDOM HEARTS III.exe", tmp_path)
    assert status["running"] and status["pid"] == 200
    (game / "cmdline").unlink()
    assert _get_process_status_linux_proc("KINGDOM HEARTS III.exe", tmp_path) == {"running": False}
