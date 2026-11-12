"""Exercise the default frontend without a server, game, or live save directory."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Native window smoke test requires a Windows desktop")
def test_native_window_tabs_dialogs_and_shutdown():
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent('''
            import asyncio
            import logging
            import sys
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from unittest.mock import AsyncMock, Mock, patch
            import KH3Client
            from worlds.kh3.Client import KH3Context
            from worlds.kh3.ClientGui import run_kh3_gui

            async def run():
                with TemporaryDirectory() as directory, patch("Utils.persistent_load", return_value={}):
                    root = Path(directory)
                    ctx = KH3Context(None, None, str(root / "bridge"), integrated_bridge=False,
                                     kh3_dir=str(root / "game"), save_dir=str(root / "saves"))
                    run_kh3_gui(ctx)
                    app = ctx.ui
                    callback_errors = []
                    app.root.report_callback_exception = lambda *error: callback_errors.append(error)
                    try:
                        assert "worlds.kh3.gui.config" in sys.modules
                        assert not any("worlds.kh3.gui." + name in sys.modules
                                       for name in ("debug", "save_manager", "mods"))
                        for name, page in app.pages.items():
                            app.tabs.select(page)
                            app.root.update()
                            await app.refresh()
                        assert set(app.views) == {"KH3 Config", "KH3 Debug", "Save Manager", "Third Party Mods"}
                        created_views = dict(app.views)
                        for name in created_views:
                            app.tabs.select(app.pages[name])
                            app.select_tab()
                            assert app.views[name] is created_views[name]
                        assert not any(n == "kvui" or n.startswith("kivy") for n in sys.modules)
                        assert app.command_row.winfo_ismapped()
                        assert app.command_row.winfo_y() + app.command_row.winfo_height() <= app.root.winfo_height()
                        assert app.command.winfo_rootx() - app.root.winfo_rootx() == 2
                        app.connect_button.state(["active"])
                        app.launch_button.state(["active"])
                        await app.refresh()
                        assert app.connect_button.instate(["active"])
                        assert app.launch_button.instate(["active"])
                        config = app.views["KH3 Config"]
                        def button_texts(widget):
                            texts = []
                            if widget.winfo_class() == "TButton":
                                texts.append(widget.cget("text"))
                            for child in widget.winfo_children():
                                texts.extend(button_texts(child))
                            return texts
                        for key in ("KH0.2 Dir", "MoM Dir"):
                            row = config.detect_buttons[key].master
                            assert button_texts(row).count("Install Mod") == 1
                            assert "Uninstall Mod" not in button_texts(row)
                            assert config.mod_buttons[key].instate(["disabled"])
                        assert config.patch_button.cget("style") == "Alert.TButton"
                        assert config.patch_tooltip.text
                        with patch.object(config, "apply", new=AsyncMock(return_value=True)) as apply, patch.object(
                            ctx, "export_debug_bundle"
                        ) as export_debug:
                            for action, checked in (("Close", True), ("Escape", False), ("Export", False), ("Export", True)):
                                apply.reset_mock()
                                export_debug.reset_mock()
                                export_task = app.spawn(config.export_debug_bundle())
                                await asyncio.sleep(0)
                                app.root.update()
                                modal = next(iter(app.dialogs))
                                assert app.root.grab_current() == modal
                                children = modal.body.winfo_children()
                                assert [child.winfo_class() for child in children] == ["TCheckbutton", "TFrame"]
                                checkbox = children[0]
                                assert checkbox.cget("text") == "Include crash dumps from today"
                                assert not checkbox.instate(["selected"])
                                assert app.root.focus_get() == checkbox
                                buttons = {button.cget("text"): button for button in children[1].winfo_children()}
                                assert set(buttons) == {"Export", "Close"}
                                apply.assert_not_awaited()
                                export_debug.assert_not_called()
                                if checked:
                                    checkbox.invoke()
                                if action == "Escape":
                                    checkbox.event_generate("<Escape>")
                                else:
                                    buttons[action].invoke()
                                await export_task
                                assert not app.dialogs
                                assert app.root.grab_current() is None
                                if action == "Export":
                                    apply.assert_awaited_once()
                                    export_debug.assert_called_once_with(include_crash_dumps=checked)
                                else:
                                    apply.assert_not_awaited()
                                    export_debug.assert_not_called()
                        from worlds.kh3.client_support import custom_music
                        music = root / "My Music 音楽"
                        music.mkdir()
                        with patch("Utils.persistent_store") as store, patch("Utils.open_directory", return_value=str(music)):
                            await config.browse("Music Dir")
                            assert config.paths["Music Dir"].get() == str(music.resolve())
                            store.assert_called_with("kh3_client", custom_music.PREFERENCE, str(music.resolve()))
                            await config.detect("Music Dir")
                            assert config.paths["Music Dir"].get() == ""
                            store.assert_called_with("kh3_client", custom_music.PREFERENCE, "")
                        app.tabs.select(app.pages["KH3 Config"])
                        app.tabs.finish_transition()
                        app.root.update()
                        assert config.detect_buttons["Music Dir"].winfo_ismapped()
                        config.canvas.yview_moveto(1)
                        app.root.update()
                        assert "goa" not in config.labels
                        assert " | GoA " in app.root.title()
                        assert config.labels["bridge"].winfo_ismapped()
                        assert config.labels["bridge"].winfo_rooty() + config.labels["bridge"].winfo_height() <= config.canvas.winfo_rooty() + config.canvas.winfo_height()
                        config.canvas.yview_moveto(0)
                        from worlds.kh3.gui.widgets import Dropdown
                        from worlds.kh3.client_support import fast_new_game
                        preferences = dict(fast_new_game.DEFAULTS)
                        def save_preferences(value, bridge, kh3_dir=None):
                            preferences.update(value)
                            return dict(preferences)
                        with patch.object(fast_new_game, "read_settings", side_effect=lambda: dict(preferences)), patch.object(
                            fast_new_game, "save_settings", side_effect=save_preferences
                        ) as save_settings:
                            config.fast_new_game.set(True)
                            config.toggle_fast_new_game()
                            dialog = config.fast_new_game_dialog
                            app.root.update()
                            assert dialog.grab_current() == dialog
                            body = dialog.winfo_children()[0]
                            buttons = body.winfo_children()[-1].winfo_children()
                            buttons[0].invoke()  # Cancel must not enable or persist.
                            assert not config.fast_new_game.get()
                            save_settings.assert_not_called()
                            config.fast_new_game.set(True)
                            config.toggle_fast_new_game()
                            body = config.fast_new_game_dialog.winfo_children()[0]
                            choices = [child for child in body.winfo_children() if isinstance(child, Dropdown)]
                            assert len(choices) == 4
                            for choice, value in zip(choices, ("French", "Japanese", "AZERTY")):
                                choice.open()
                                app.root.update()
                                assert choice.popup.grab_current() == choice.popup
                                index = choice.values.index(value)
                                choice.options[index].invoke()
                                assert choice.popup is None
                                assert config.fast_new_game_dialog.grab_current() == config.fast_new_game_dialog
                            body.winfo_children()[-1].winfo_children()[1].invoke()
                            assert preferences == {**fast_new_game.DEFAULTS, "enabled": True, "text": 4, "voice": 11, "keyboard": 14}
                            assert config.fast_new_game.get()
                            config.fast_new_game.set(False)
                            config.toggle_fast_new_game()
                            assert preferences["enabled"] is False
                            assert preferences["text"] == 4
                        config.patch_tooltip.show()
                        assert config.patch_tooltip.window is not None
                        config.patch_tooltip.hide()
                        debug = app.views["KH3 Debug"]
                        assert debug.counts_tooltip.widget is debug.labels["item_counts"]
                        assert debug.slots.winfo_children()[0].tooltip.text
                        # The normal slide is still moving halfway through Kivy's
                        # duration, but clicking the selected tab again snaps to it.
                        app.tabs.buttons[str(app.pages["Hints"])].invoke()
                        await asyncio.sleep(.2)
                        assert app.tabs.animation is not None
                        app.tabs.buttons[str(app.pages["Hints"])].invoke()
                        app.root.update()
                        assert app.tabs.animation is None
                        assert app.pages["Hints"].winfo_x() == 0
                        # Rapid navigation cancels the previous transition and settles
                        # on just the requested page without leaving delayed callbacks.
                        for name in ("Hints", "Archipelago", "KH3 Debug", "Hints"):
                            app.tabs.select(app.pages[name])
                        await asyncio.sleep(.5)
                        assert app.tabs.animation is None
                        assert app.pages["Hints"].winfo_x() == 0
                        assert sum(page.winfo_ismapped() for page in app.pages.values()) == 1
                        assert app.tabs.winfo_x() == 0
                        assert app.tabs.winfo_width() == app.root.winfo_width()
                        ctx.item_names.update_game(ctx.game, {"Progressive High Jump": 1, "Progressive Glide": 2})
                        hint_view = app.hint_view
                        from worlds.kh3.gui.theme import MESSAGE_COLORS
                        from worlds.kh3.gui.hints import HintRow
                        table = hint_view.table
                        values = ("Sora", "Progressive High Jump " * 4, "Riku", "Cliffside Chest", "Priority")
                        colors = ("magenta", "plum", "yellow", "green", "plum")
                        hint_data = dict(receiving_player=1, finding_player=2, location=123, found=False)
                        table.set_rows([HintRow(values, colors, {**hint_data, "location": index}) for index in range(30)])
                        app.root.update()
                        cells = [item for item in table.canvas.find_withtag("row_0") if table.canvas.type(item) == "text"]
                        assert [table.canvas.itemcget(item, "fill") for item in cells] == [MESSAGE_COLORS[color] for color in colors]
                        assert table.canvas.bbox(cells[1])[3] - table.canvas.bbox(cells[1])[1] > table.text_font.metrics("linespace")
                        assert table.canvas.yview()[1] < 1
                        table.scroll(3)
                        assert table.canvas.yview()[0] > 0
                        sample = [HintRow(values, colors, hint_data),
                                  HintRow(("Aqua", "Fire", "Riku", "Chest 2", "Found"), colors,
                                          {**hint_data, "location": 124, "found": True})]
                        table.set_rows(sample)
                        app.root.update()
                        table.header.event_generate("<Button-1>", x=10, y=10)
                        assert table.visible_rows[0][0][0] == "Aqua"
                        table.header.event_generate("<Button-1>", x=10, y=10)
                        assert table.visible_rows[0][0][0] == "Sora"
                        table.canvas.yview_moveto(0)
                        table.canvas.event_generate("<Button-3>", x=10, y=10)
                        app.root.update()
                        assert table.context_menu.winfo_ismapped()
                        table.context_menu.buttons[1].invoke()  # Show only the clicked receiving player.
                        assert len(table.visible_rows) == 1
                        assert table.visible_rows[0][0][0] == "Sora"
                        table.clear_filters()
                        assert len(table.visible_rows) == 2
                        from worlds.kh3.gui.hint_filter import HintFilterDialog
                        filter_task = asyncio.create_task(table.filter_column(0))
                        await asyncio.sleep(0)
                        app.root.update()
                        picker = next(dialog for dialog in app.dialogs if isinstance(dialog, HintFilterDialog))
                        assert picker.listbox.get(0, "end") == ("Aqua", "Sora")
                        assert picker.listbox.curselection() == (0, 1)
                        picker.listbox.selection_clear(0, "end")
                        picker.listbox.selection_set(0)
                        next(button for button in picker.body.winfo_children()[-1].winfo_children()
                             if button.cget("text") == "Apply Filter").invoke()
                        await filter_task
                        assert [record[0][0] for record in table.visible_rows] == ["Aqua"]
                        table.clear_filters()
                        table.set_rows([])
                        hint_view.query.set("progressive")
                        app.root.update()
                        assert hint_view.query.get() == "progressive"
                        assert hint_view.matches.size() == 2
                        await app.refresh()
                        assert hint_view.query.get() == "progressive"
                        hint_view.matches.selection_set(0)
                        hint_view.choose()
                        assert hint_view.query.get() == "Progressive Glide"
                        assert not hint_view.popup.winfo_ismapped()
                        with patch.object(app, "commandprocessor", Mock()) as send:
                            hint_view.query.set("High Jump")
                            assert send.call_count == 0
                            hint_view.submit()
                            send.assert_called_once_with("!hint High Jump")
                        hint_view.query.set("no match exists")
                        assert not hint_view.popup.winfo_ismapped()
                        from worlds.kh3.gui.widgets import Tooltip
                        tip = Tooltip(hint_view.entry, "Open mod storage")
                        tip.show()
                        app.root.update()
                        short_width = tip.window.winfo_width()
                        assert tip.window.winfo_height() < 60
                        tip.hide()
                        tip.text = "Pending items: " + "Progressive High Jump " * 60
                        tip.show()
                        app.root.update()
                        assert short_width < tip.window.winfo_width() < 500
                        assert tip.window.winfo_height() > 100
                        tip.hide()
                        assert not callback_errors, callback_errors
                        await asyncio.to_thread(logging.getLogger("Client").warning, "worker log")
                        await asyncio.sleep(.05)
                        assert "worker log" in app.log.get("1.0", "end")
                        from worlds.kh3.gui.log import LogEntry
                        first = app.log.index("end-1c")
                        style = f"message_{app.log_view.message_number % 2}"
                        app.log_view.append(LogEntry((("first\\nsecond", "green"), (" tail", "")), (2, None)))
                        end = app.log.index("end-1c")
                        assert app.log.get(first, end) == "first\\nsecond tail\\n"
                        assert tuple(map(str, app.log.tag_nextrange(style, first))) == (first, end)
                        assert "green" in app.log.tag_names(first)
                        app.log_view.append("next message")
                        next_style = next(tag for tag in app.log.tag_names(end) if tag.startswith("message_"))
                        assert app.log.tag_cget(style, "background") == app.log.tag_cget(next_style, "background")
                        app.log_view.search.set("second tail")
                        assert app.log.tag_ranges("match")
                        app.tabs.select(app.pages["Archipelago"])
                        app.tabs.finish_transition()
                        app.root.update()
                        x, y, width, height = app.log.bbox(first)
                        app.log.event_generate("<Motion>", x=x + 1, y=y + height // 2)
                        assert tuple(map(str, app.log.tag_ranges("hover"))) == (first, end)
                        assert app.log_view.item_tooltip.text == "Useful"
                        assert app.log_view.item_tooltip.timer is not None
                        app.log_view.item_tooltip.cancel_timer()
                        app.log_view.item_tooltip.show()
                        assert app.log_view.item_tooltip.window is not None
                        with patch.object(app.log, "clipboard_clear") as clear, patch.object(app.log, "clipboard_append") as copy:
                            app.log.event_generate("<Button-3>", x=x + 1, y=y + height // 2)
                            clear.assert_called_once_with()
                            copy.assert_called_once_with("first")
                        x, y, width, height = app.log.bbox(end)
                        app.log.event_generate("<Motion>", x=x + 1, y=y + height // 2)
                        assert str(app.log.tag_ranges("hover")[0]) == end
                        assert app.log_view.item_tooltip.window is None
                        assert app.log_view.item_tooltip.timer is None
                        app.log.event_generate("<Leave>")
                        assert not app.log.tag_ranges("hover")
                        app.command.focus_force()
                        app.command.event_generate("<Control-f>")
                        app.root.update()
                        assert app.log_view.find_panel.winfo_ismapped()
                        assert app.root.focus_get() == app.log_view.find_entry
                        app.log_view.search.set("message")
                        assert app.log_view.matches
                        app.log_view.find_entry.event_generate("<Return>")
                        app.log_view.find_close.invoke()
                        app.root.update()
                        assert not app.log_view.find_panel.winfo_ismapped()
                        assert not app.log.tag_ranges("match")
                        assert app.root.focus_get() == app.command
                        app.log_view.show_find()
                        app.root.update()
                        app.log_view.find_entry.event_generate("<Escape>")
                        assert not app.log_view.find_visible
                        ctx.input_requests = 1
                        app.command.insert(0, "test slot")
                        app.submit_command()
                        assert await ctx.input_queue.get() == "test slot"
                        menu = app.views["Save Manager"].context_menu
                        menu.clear()
                        disabled_action = Mock()
                        menu.add_command(label="Activated", state="disabled", command=disabled_action)
                        menu.add_command(label="Edit", command=lambda: None)
                        menu.show(app.root.winfo_rootx() + 100, app.root.winfo_rooty() + 100)
                        app.root.update()
                        disabled, enabled = menu.buttons
                        background = disabled.cget("background")
                        disabled.event_generate("<Enter>")
                        disabled.invoke()
                        assert disabled.cget("background") == background
                        disabled_action.assert_not_called()
                        enabled.event_generate("<Enter>")
                        assert enabled.cget("background") != background
                        menu.unpost()
                        assert app.root.grab_current() is None
                        app.command.focus_force()
                        edit = app.spawn(app.prompt_run_connection_profile(slot_name="Sora", server="example.com",
                                        port="12345", room_url=""))
                        await asyncio.sleep(.25)
                        modal = next(iter(app.dialogs))
                        assert modal.winfo_toplevel() == app.root
                        assert abs(modal.winfo_x() + modal.winfo_width() / 2 - app.root.winfo_width() / 2) <= 1
                        assert abs(modal.winfo_y() + modal.winfo_height() / 2 - app.root.winfo_height() / 2) <= 1
                        assert app.root.grab_current() == modal
                        first_entry = app.root.focus_get()
                        assert first_entry.winfo_class() == "TEntry"
                        first_entry.event_generate("<Shift-Tab>")
                        assert app.root.focus_get().winfo_class() == "TButton"
                        app.root.focus_get().event_generate("<Tab>")
                        assert app.root.focus_get() == first_entry
                        app.root.focus_get().event_generate("<Escape>")
                        assert await edit is None
                        assert app.root.grab_current() is None
                        assert app.root.focus_get() == app.command
                        dialog = app.spawn(app.form("Test", "Close cancels this dialog"))
                        await asyncio.sleep(.03)
                        assert len(app.dialogs) == 1
                        ctx.input_requests = 1
                        app.stop()
                        assert await dialog is None
                        assert await ctx.input_queue.get() == ""
                        await asyncio.wait_for(ctx.ui_task, 3)
                        assert ctx.exit_event.is_set()
                        assert app.log_handler not in logging.getLogger("Client").handlers
                        assert not app.tasks
                    finally:
                        app.stop()
                        await ctx.ui_task
            asyncio.run(run())
        ''')],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Native window smoke test requires a Windows desktop")
def test_native_connection_errors_stay_in_client_and_dismiss_cleanly():
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent('''
            import asyncio
            import socket
            import sys
            import tkinter as tk
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from unittest.mock import AsyncMock, patch

            class RejectKivyImports:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "kvui" or fullname.startswith(("kivy", "kivymd")):
                        raise AssertionError(f"unexpected GUI dependency: {fullname}")
                    return None

            sys.meta_path.insert(0, RejectKivyImports())
            import KH3Client
            from worlds.kh3.client_support.network import server_loop
            from worlds.kh3.Client import KH3Context
            from worlds.kh3.ClientGui import run_kh3_gui

            async def run():
                with TemporaryDirectory() as directory, patch("Utils.persistent_load", return_value={}):
                    root = Path(directory)
                    ctx = KH3Context(None, None, str(root / "bridge"), integrated_bridge=False,
                                     kh3_dir=str(root / "game"), save_dir=str(root / "saves"))
                    ctx.connection_closed = AsyncMock()
                    assert ctx.gui_error("Error", "headless") is None
                    run_kh3_gui(ctx)
                    app = ctx.ui
                    callback_errors = []
                    app.root.report_callback_exception = lambda *error: callback_errors.append(error)
                    try:
                        # Hold a local port without listening so no server can race the refusal.
                        with socket.socket() as unavailable:
                            unavailable.bind(("127.0.0.1", 0))
                            address = f"127.0.0.1:{unavailable.getsockname()[1]}"
                            await asyncio.wait_for(server_loop(ctx, address), 5)
                            first = ctx._messagebox_connection_loss
                            assert first is app.messagebox
                            assert first.winfo_toplevel() == app.root
                            assert first.winfo_class() == "Frame"
                            assert app.root.grab_current() == first
                            labels = [child.cget("text") for child in first.body.winfo_children()
                                      if child.winfo_class() == "TLabel"]
                            assert labels[0] == "Connection Error"
                            assert f"Server: {address}" in labels[1]
                            assert "May not be running Archipelago" in labels[1]
                            await asyncio.sleep(.03)
                            assert any(f"Server: {address}" in str(line) for line in app.log_lines)
                            assert not any("Traceback (most recent call last)" in str(line) for line in app.log_lines)
                            # Retry must dismiss the old error before displaying the next one.
                            await asyncio.wait_for(server_loop(ctx, address), 5)
                            second = ctx._messagebox_connection_loss
                            assert first.dismissed and not first.winfo_exists()
                            assert second is not first and second.winfo_toplevel() == app.root
                            second.body.winfo_children()[-1].invoke()
                            second.dismiss()
                            assert second.dismissed and not second.winfo_exists()
                            assert app.root.grab_current() is None
                        # Other CommonClient errors use the same frontend, and shutdown clears it.
                        final = ctx.gui_error("Error", "Invalid password")
                        assert final.winfo_toplevel() == app.root
                        assert not any(isinstance(child, tk.Toplevel) for child in app.root.winfo_children())
                        assert not any(n == "kvui" or n.startswith(("kivy", "kivymd")) for n in sys.modules)
                        app.stop()
                        final.dismiss()
                        assert final.dismissed
                        assert app.show_error("Error", "after shutdown") is None
                        assert ctx.gui_error("Error", "after shutdown") is None
                        await asyncio.wait_for(ctx.ui_task, 3)
                        assert not callback_errors, callback_errors
                    finally:
                        app.stop()
                        await ctx.ui_task
                        ctx.keep_alive_task.cancel()
                        await asyncio.gather(ctx.keep_alive_task, return_exceptions=True)
            asyncio.run(run())
        ''')],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("accepted", [None, {}])
def test_bulk_delete_uses_confirmed_ids_and_active_run(accepted):
    from worlds.kh3.gui.save_manager import SaveManagerView

    runs = [SimpleNamespace(run_id="active", active=True), SimpleNamespace(run_id="old", active=False)]
    ctx = SimpleNamespace(kh3_run_action_running=False, list_kh3_runs=Mock(return_value=runs),
                          request_delete_inactive_kh3_runs=Mock())

    async def confirm(*args, **kwargs):
        # A newly discovered run was not part of the confirmation.
        runs.append(SimpleNamespace(run_id="new", active=False))
        return accepted

    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx, view.app = ctx, SimpleNamespace(form=confirm)
    asyncio.run(view.delete_others())
    if accepted is None:
        ctx.request_delete_inactive_kh3_runs.assert_not_called()
    else:
        ctx.request_delete_inactive_kh3_runs.assert_called_once_with(("old",), "active")


def test_connect_and_game_launch_use_context_and_busy_guard():
    from worlds.kh3.gui.manager import KH3Manager

    ctx = SimpleNamespace(kh3_run_action_running=False, server=None,
                          connect=AsyncMock(), disconnect=AsyncMock(), launch_kh3_game=Mock())
    app = KH3Manager.__new__(KH3Manager)
    app.ctx, app.action_running = ctx, False
    app.address = SimpleNamespace(get=lambda: "archipelago.gg:12345")
    asyncio.run(app.connect())
    ctx.connect.assert_awaited_once_with("archipelago.gg:12345")
    app.launch_game()
    ctx.launch_kh3_game.assert_called_once()
    ctx.server = object()
    asyncio.run(app.connect())
    ctx.disconnect.assert_awaited_once()
    app.action_running = True
    asyncio.run(app.connect())
    app.launch_game()
    ctx.disconnect.assert_awaited_once()
    ctx.launch_kh3_game.assert_called_once()


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("save_mismatch", [False, True])
def test_run_context_menu_targets_clicked_run_even_if_selection_changes(active, save_mismatch, monkeypatch):
    from worlds.kh3.gui.save_manager import SaveManagerView
    from worlds.kh3.platform_support import process_status
    monkeypatch.setattr(process_status, "is_process_running", lambda _: False)

    run = SimpleNamespace(run_id="clicked", active=active, installed=active, managed=True, pak_available=True,
                          room_url="https://example.com/room", slot_name="Sora", effective_slot_name="Sora",
                          server_address="archipelago.gg:12345")
    ctx = SimpleNamespace(kh3_run_action_running=False, save_seed_mismatch_active=save_mismatch,
                          request_activate_kh3_run=Mock(),
                          request_update_kh3_run_connection_profile=Mock(), request_delete_kh3_run=Mock(),
                          open_kh3_room_page=Mock(), _is_ap_connected=lambda: False)
    profile = dict(slot_name="Sora", server="archipelago.gg", port="12345", room_url=run.room_url)
    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = ctx
    view.game_running = False
    view.app = SimpleNamespace(action_running=False, spawn=asyncio.run,
                               prompt_run_connection_profile=AsyncMock(return_value=profile),
                               form=AsyncMock(return_value={}))
    view.runs = {run.run_id: run}
    view.tree = Mock()
    view.tree.identify_row.return_value = run.run_id
    view.tree.identify_region.return_value = "cell"
    view.tree.selection.return_value = ("different selection",)
    view.tree.selection_set.side_effect = lambda run_id: setattr(view.tree.selection, "return_value", (run_id,))
    view.context_menu = Mock()
    event = SimpleNamespace(x=20, y=40, x_root=120, y_root=140)
    assert view.show_context_menu(event) == "break"
    view.tree.selection_set.assert_called_once_with(run.run_id)
    entries = [call.kwargs for call in view.context_menu.add_command.call_args_list]
    already_active = active and not save_mismatch
    assert [entry["label"] for entry in entries] == ["Activated" if already_active else "Activate", "Edit", "Room", "Delete"]
    assert entries[0]["state"] == ("disabled" if already_active else "normal")
    if not already_active:
        entries[0]["command"]()
        ctx.request_activate_kh3_run.assert_called_once_with(run.run_id)
    for entry in entries[1:]:
        entry["command"]()
    ctx.request_update_kh3_run_connection_profile.assert_called_once_with(run.run_id, **profile, show_activity=active)
    ctx.open_kh3_room_page.assert_called_once_with(run.room_url)
    ctx.request_delete_kh3_run.assert_called_once_with(run.run_id)
    view.context_menu.show.assert_called_once_with(120, 140)
    view.context_menu.reset_mock()
    view.tree.identify_row.return_value = ""
    view.show_context_menu(event)
    view.context_menu.show.assert_not_called()


@pytest.mark.parametrize("active,installed,connected,running,allowed", [
    (True, True, False, False, True),
    (True, True, True, False, False),
    (True, True, False, True, False),
    (False, False, True, True, True),
    (False, True, False, True, False),
])
def test_run_context_menu_disables_delete_for_live_active_run(active, installed, connected, running, allowed):
    from worlds.kh3.gui.save_manager import SaveManagerView

    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = SimpleNamespace(kh3_run_action_running=False, _is_ap_connected=lambda: connected)
    view.app = SimpleNamespace(action_running=False)
    view.game_running = running
    view.runs = {"run": SimpleNamespace(active=active, installed=installed, managed=True,
                                       pak_available=True, room_url="")}
    view.tree = Mock()
    view.tree.identify_row.return_value = "run"
    view.tree.identify_region.return_value = "cell"
    view.tree.selection.return_value = ("run",)
    view.context_menu = Mock()
    view.show_context_menu(SimpleNamespace(x=1, y=1, x_root=1, y_root=1))
    entries = [call.kwargs for call in view.context_menu.add_command.call_args_list]
    assert entries[2]["state"] == "disabled"
    assert entries[3]["state"] == ("normal" if allowed else "disabled")


@pytest.mark.parametrize("accepted", [None, {}])
@pytest.mark.parametrize("count", [1, 3])
def test_selected_run_delete_confirms_once_and_keeps_original_targets(monkeypatch, accepted, count):
    from worlds.kh3.gui.save_manager import SaveManagerView
    from worlds.kh3.platform_support import process_status

    monkeypatch.setattr(process_status, "is_process_running", lambda _: False)
    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = SimpleNamespace(kh3_run_action_running=False, _is_ap_connected=lambda: False,
                               request_delete_kh3_run=Mock(), request_delete_kh3_runs=Mock())
    view.game_running = False
    ids = tuple(f"run-{index}" for index in range(count))
    view.runs = {run_id: SimpleNamespace(slot_name="Sora", active=False, installed=False) for run_id in ids}
    view.tree = Mock()
    view.tree.selection.return_value = ids

    async def confirm(*args, **kwargs):
        view.runs["new"] = SimpleNamespace(slot_name="New", active=False, installed=False)
        view.tree.selection.return_value = ("new",)
        return accepted

    view.app = SimpleNamespace(action_running=False, form=AsyncMock(side_effect=confirm))
    asyncio.run(view.delete())
    view.app.form.assert_awaited_once()
    if accepted is None:
        view.ctx.request_delete_kh3_run.assert_not_called()
        view.ctx.request_delete_kh3_runs.assert_not_called()
    elif count == 1:
        view.ctx.request_delete_kh3_run.assert_called_once_with(ids[0])
        view.ctx.request_delete_kh3_runs.assert_not_called()
    else:
        assert f"all {count} selected runs" in view.app.form.call_args.args[1]
        view.ctx.request_delete_kh3_runs.assert_called_once_with(ids)
        view.ctx.request_delete_kh3_run.assert_not_called()


@pytest.mark.parametrize("change", ["removed", "connected", "busy"])
def test_selected_run_delete_rechecks_confirmed_targets(monkeypatch, change):
    from worlds.kh3.gui.save_manager import SaveManagerView
    from worlds.kh3.platform_support import process_status

    monkeypatch.setattr(process_status, "is_process_running", lambda _: False)
    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = SimpleNamespace(kh3_run_action_running=False, _is_ap_connected=Mock(return_value=False),
                               request_delete_kh3_run=Mock(), request_delete_kh3_runs=Mock())
    view.game_running = False
    view.runs = {"old": SimpleNamespace(slot_name="Old", active=False, installed=False),
                 "active": SimpleNamespace(slot_name="Sora", active=True, installed=True)}
    view.tree = Mock()
    view.tree.selection.return_value = ("old", "active")

    async def confirm(*args, **kwargs):
        if change == "removed":
            view.runs.pop("active")
        elif change == "connected":
            view.ctx._is_ap_connected.return_value = True
        else:
            view.app.action_running = True
        return {}

    view.app = SimpleNamespace(action_running=False, form=confirm)
    asyncio.run(view.delete())
    view.ctx.request_delete_kh3_run.assert_not_called()
    view.ctx.request_delete_kh3_runs.assert_not_called()


@pytest.mark.parametrize("selection,connected,running,enabled", [
    ((), False, False, False),
    (("old", "another"), True, True, True),
    (("old", "active"), False, False, True),
    (("old", "active"), True, False, False),
    (("old", "active"), False, True, False),
    (("old", "missing"), False, False, False),
])
def test_delete_button_requires_every_selected_run_to_be_eligible(selection, connected, running, enabled):
    from worlds.kh3.gui.save_manager import SaveManagerView

    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = SimpleNamespace(kh3_run_action_running=False, _is_ap_connected=lambda: connected)
    view.app = SimpleNamespace(action_running=False)
    view.game_running = running
    view.runs = {"old": SimpleNamespace(active=False, installed=False),
                 "another": SimpleNamespace(active=False, installed=False),
                 "active": SimpleNamespace(active=True, installed=True)}
    view.tree = Mock()
    view.tree.selection.return_value = selection
    view.delete_button = Mock()
    view.update_delete_button()
    view.delete_button.state.assert_called_once_with(["!disabled" if enabled else "disabled"])


def test_context_delete_preserves_multiple_highlighted_runs(monkeypatch):
    from worlds.kh3.gui.save_manager import SaveManagerView
    from worlds.kh3.platform_support import process_status

    monkeypatch.setattr(process_status, "is_process_running", lambda _: False)
    view = SaveManagerView.__new__(SaveManagerView)
    view.ctx = SimpleNamespace(kh3_run_action_running=False, _is_ap_connected=lambda: False,
                               request_delete_kh3_runs=Mock())
    view.game_running = False
    view.runs = {run_id: SimpleNamespace(run_id=run_id, slot_name=run_id, active=False, installed=False,
                                        managed=True, pak_available=True, room_url="")
                 for run_id in ("first", "second", "third")}
    view.tree = Mock()
    view.tree.identify_row.return_value = "second"
    view.tree.identify_region.return_value = "cell"
    view.tree.selection.return_value = ("first", "second")
    view.context_menu = Mock()
    view.app = SimpleNamespace(action_running=False, spawn=asyncio.run, form=AsyncMock(return_value={}))
    view.show_context_menu(SimpleNamespace(x=1, y=1, x_root=1, y_root=1))
    view.tree.selection_set.assert_not_called()
    view.tree.selection.return_value = ("third",)
    entries = [call.kwargs for call in view.context_menu.add_command.call_args_list]
    entries[3]["command"]()
    view.ctx.request_delete_kh3_runs.assert_called_once_with(("first", "second"))


@pytest.mark.parametrize("status,found,expected", [
    (None, False, "Unspecified"), (None, True, "Found"),
    (0, False, "Unspecified"), (10, False, "No Priority"),
    (20, False, "Avoid"), (30, False, "Priority"), (40, True, "Found"),
    (999, False, "Unknown"),
])
def test_native_hint_columns_preserve_players_and_status_without_entrance(status, found, expected):
    from worlds.kh3.gui.hints import HintsView

    ctx = SimpleNamespace(player_names={1: "Sora", 2: "Riku"}, slot_concerns_self=lambda slot: slot == 1,
                          item_names=SimpleNamespace(lookup_in_slot=Mock(return_value="High Jump")),
                          location_names=SimpleNamespace(lookup_in_slot=Mock(return_value="Cliffside Chest")))
    view = HintsView.__new__(HintsView)
    view.app = SimpleNamespace(ctx=ctx)
    view.table = Mock()
    hint = dict(receiving_player=1, finding_player=2, item=123, location=456, found=found, item_flags=2)
    if status is not None:
        hint["status"] = status
    view.refresh([hint, {**hint, "entrance": "Olympus"}])
    rows = view.table.set_rows.call_args.args[0]
    assert [row.values for row in rows] == [
        ("Sora", "High Jump", "Riku", "Cliffside Chest", expected),
        ("Sora", "High Jump", "Riku", "Cliffside Chest", expected),
    ]
    assert rows[0][1][:4] == ("magenta", "slateblue", "yellow", "green")
    assert rows[0][1][4] == {"Found": "green", "Unspecified": "white", "No Priority": "cyan",
                           "Avoid": "salmon", "Priority": "plum", "Unknown": "red"}[expected]
    ctx.item_names.lookup_in_slot.assert_called_with(123, 1)
    ctx.location_names.lookup_in_slot.assert_called_with(456, 2)


@pytest.mark.skipif(sys.platform != "win32", reason="Native window smoke test requires a Windows desktop")
def test_inline_hint_status_menu_and_lifecycle():
    from .test_client_gui import run_isolated_python

    result = run_isolated_python('''
        import tkinter as tk
        from types import SimpleNamespace
        from unittest.mock import Mock
        from NetUtils import HintStatus
        from worlds.kh3.gui.hints import HintsView
        from worlds.kh3.gui.theme import Theme

        root = tk.Tk()
        root.geometry("980x420")
        theme = Theme(root)
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        ctx = SimpleNamespace(server=SimpleNamespace(socket=SimpleNamespace(open=True, closed=False)),
                              team=0, slot=1, slot_concerns_self=lambda slot: slot == 1,
                              player_names={1: "Sora", 2: "Riku"}, update_hint=Mock(),
                              item_names=SimpleNamespace(lookup_in_slot=lambda item, slot: "Progressive High Jump"),
                              location_names=SimpleNamespace(lookup_in_slot=lambda location, slot: "Cliffside Chest " * 4))
        app = SimpleNamespace(root=root, theme=theme, ctx=ctx)
        view = HintsView(app, root)
        table = view.table
        hints = [dict(receiving_player=1, finding_player=2, item=10, location=100, found=False, status=0),
                 dict(receiving_player=2, finding_player=1, item=11, location=101, found=False, status=30),
                 dict(receiving_player=1, finding_player=2, item=12, location=102, found=True, status=40)]
        try:
            view.refresh(hints)
            root.update()
            # Only an owned, unfound hint gets a dropdown affordance.
            arrows = [item for item in table.canvas.find_all() if table.canvas.type(item) == "text"
                      and table.canvas.itemcget(item, "text") == "▾"]
            assert len(arrows) == 1
            table.canvas.event_generate("<Button-1>", x=table.canvas.winfo_width() - 20, y=12)
            root.update()
            menu = table.context_menu
            assert menu.winfo_ismapped() and root.grab_current() == menu
            control_bottom = table.canvas.coords("status_control_0")[3]
            row_bottom = table.canvas.coords("background_0")[3]
            assert row_bottom > control_bottom + table.text_font.metrics("linespace")
            assert menu.winfo_rooty() == round(table.canvas.winfo_rooty() + control_bottom + 1 - table.canvas.canvasy(0))
            assert [button.cget("text").strip("✓ ") for button in menu.buttons] == [
                "Unspecified", "No Priority", "Priority", "Avoid"]
            menu.buttons[2].invoke()
            root.update()
            ctx.update_hint.assert_called_once_with(100, 2, HintStatus.HINT_PRIORITY)
            assert not menu.winfo_ismapped() and root.grab_current() is None
            assert table.rows[0].values[4] == "Unspecified"  # Wait for server confirmation.
            hints[0]["status"] = HintStatus.HINT_PRIORITY
            view.refresh(hints)
            root.update()
            assert table.selected == 0 and table.rows[0].values[4] == "Priority"
            table.canvas.focus_force()
            table.canvas.event_generate("<Return>")
            root.update()
            assert menu.winfo_ismapped()
            menu.buttons[2].event_generate("<Escape>")
            root.update()
            assert not menu.winfo_ismapped()
            ctx.update_hint.assert_called_once()
            # Keyboard row navigation skips no data, but read-only rows cannot open the menu.
            table.canvas.event_generate("<Down>")
            root.update()
            assert table.selected == 1
            table.canvas.event_generate("<Return>")
            root.update()
            assert not menu.winfo_ismapped()
            table.move_selection(-1)
            root.update()
            table.open_status_menu()
            root.update()
            root.geometry("1100x420")
            root.update()
            assert not menu.winfo_ismapped() and root.grab_current() is None
            table.open_status_menu()
            root.update()
            hints[0]["found"] = True
            view.refresh(hints)
            root.update()
            assert not menu.winfo_ismapped() and table.rows[0].values[4] == "Found"
            hints[0]["found"] = False
            view.refresh(hints)
            root.update()
            table.open_status_menu()
            root.update()
            assert menu.winfo_ismapped()
            table.pack_forget()
            root.update()
            assert not menu.winfo_ismapped() and root.grab_current() is None
            assert not errors, errors
        finally:
            root.destroy()
            theme.close()
    ''')
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.mark.parametrize("args", [(), ("--experimental-native-ui",)])
def test_native_cli_default_and_compatibility_alias(monkeypatch, args):
    from worlds.kh3 import Client

    main = AsyncMock()
    monkeypatch.setattr(Client, "main", main)
    Client.launch(*args)
    main.assert_awaited_once()
    assert "legacy_kivy_ui" not in main.await_args.kwargs




def test_gui_dispatch_uses_native_manager(monkeypatch):
    from worlds.kh3.ClientGui import run_kh3_gui

    native = Mock(return_value=SimpleNamespace(async_run=AsyncMock()))
    monkeypatch.setitem(sys.modules, "worlds.kh3.gui.manager", SimpleNamespace(KH3Manager=native))
    ctx = SimpleNamespace()

    async def run():
        run_kh3_gui(ctx)
        await ctx.ui_task
    asyncio.run(run())
    native.assert_called_once_with(ctx)
    native.return_value.async_run.assert_awaited_once_with()


@pytest.mark.parametrize("accepted", [None, {}])
def test_seed_guard_recovery_explains_automatic_retry_without_manual_activation(accepted):
    from worlds.kh3.gui.manager import KH3Manager

    app = KH3Manager.__new__(KH3Manager)
    app.closed = False
    app.form = AsyncMock(return_value=accepted)
    app.tabs = Mock()
    app.pages = {"Save Manager": object()}
    app.select_tab = Mock()
    app.ctx = SimpleNamespace(request_activate_kh3_run=Mock())
    view = SimpleNamespace(refresh=AsyncMock(), tree=Mock(), runs={
        "target": SimpleNamespace(run_id="target", seed_id="22222222222222222222", effective_slot_name="Sora"),
        "other": SimpleNamespace(run_id="other", seed_id="11111111111111111111", effective_slot_name="Sora"),
    })
    app.views = {"Save Manager": view}
    asyncio.run(app._show_seed_guard_recovery("Mismatch: test", "22222222222222222222", "Sora"))
    app.ctx.request_activate_kh3_run.assert_not_called()
    assert not app.seed_recovery_pending
    app.tabs.select.assert_not_called()
    view.refresh.assert_not_awaited()
    assert "automatically" in app.form.call_args.args[1]


def test_seed_guard_recovery_deduplicates_pending_dialogs():
    from worlds.kh3.gui.manager import KH3Manager

    app = KH3Manager.__new__(KH3Manager)
    app.closed = False
    app.spawn = Mock(side_effect=lambda coroutine: coroutine.close())
    app.show_seed_guard_recovery(status="Mismatch", seed_id="seed", slot_name="Sora")
    app.show_seed_guard_recovery(status="Mismatch", seed_id="seed", slot_name="Sora")
    app.spawn.assert_called_once()


@pytest.mark.parametrize("reason", ["save_seed_mismatch", "installed_pak_mismatch"])
def test_seed_guard_disconnect_offers_save_recovery_only_for_save_mismatch(reason):
    from worlds.kh3.Client import KH3Context
    from worlds.kh3.client_support.seed_policy_service import DisconnectPolicyResult

    ctx = KH3Context.__new__(KH3Context)
    ctx.seed_name = "AP_22222222222222222222"
    ctx.auth = "Sora"
    ctx.ui = SimpleNamespace(show_seed_guard_recovery=Mock())
    ctx.stop_integrated_bridge = Mock()
    ctx.write_bridge_files = Mock()
    policy = Mock()
    policy.disconnect_for_seed_guard.return_value = DisconnectPolicyResult("22222222222222222222", True)
    ctx._get_seed_policy_service = Mock(return_value=policy)
    ctx.disconnect_for_seed_guard(reason, "Mismatch: test")
    if reason == "save_seed_mismatch":
        ctx.ui.show_seed_guard_recovery.assert_called_once_with(
            status="Mismatch: test", seed_id="22222222222222222222", slot_name="Sora",
        )
    else:
        ctx.ui.show_seed_guard_recovery.assert_not_called()


def test_native_chat_is_plain_text_and_does_not_mutate_server_message():
    import queue

    from NetUtils import RawJSONtoTextParser
    from worlds.kh3.gui.manager import KH3Manager

    app = KH3Manager.__new__(KH3Manager)
    app.parser = RawJSONtoTextParser(SimpleNamespace(player_names={1: "Sora"}, slot_concerns_self=lambda _: True))
    app.messages = queue.SimpleQueue()
    data = [{"type": "player_id", "text": "1"}]
    app.print_json(data)
    entry = app.messages.get()
    assert entry.text == "Sora"
    assert entry.spans == (("Sora", "magenta"),)
    assert data == [{"type": "player_id", "text": "1"}]


@pytest.mark.parametrize("node_type", ["item_id", "item_name"])
@pytest.mark.parametrize("flags", range(8))
def test_native_chat_keeps_item_classification_separate_from_text_color(node_type, flags):
    import copy
    import queue

    from NetUtils import RawJSONtoTextParser
    from worlds.kh3.gui.manager import KH3Manager

    app = KH3Manager.__new__(KH3Manager)
    ctx = SimpleNamespace(item_names=SimpleNamespace(lookup_in_slot=Mock(return_value="Bonus MP +5")))
    app.parser = RawJSONtoTextParser(ctx)
    app.messages = queue.SimpleQueue()
    data = [{"type": "color", "text": "Same color", "color": "slateblue"},
            {"type": node_type, "text": "123" if node_type == "item_id" else "Bonus MP +5",
             "player": 1, "flags": flags}]
    original = copy.deepcopy(data)
    app.print_json(data)
    entry = app.messages.get()
    assert entry.item_flags == (None, flags)
    assert entry.text == "Same colorBonus MP +5"
    assert data == original


def test_native_background_actions_exclude_other_views_and_release_on_error():
    from worlds.kh3.gui.view import View

    async def run():
        app = SimpleNamespace(ctx=SimpleNamespace(kh3_run_action_running=False), action_running=False)
        first, second = View(app, None), View(app, None)
        competing = Mock()

        def fail():
            assert app.action_running
            raise ValueError("failed operation")

        with pytest.raises(ValueError, match="failed operation"):
            await first.work(fail)
        assert not app.action_running
        app.action_running = True
        await second.work(competing)
        competing.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "path", "worker"])
def test_patch_exposes_installer_log_and_reports_action_errors(failure, caplog):
    import logging

    from worlds.kh3.gui.config import ConfigView

    async def run():
        view = ConfigView.__new__(ConfigView)
        view.app = SimpleNamespace(
            action_running=False, closed=False, tabs=Mock(),
            pages={"Archipelago": object()}, form=AsyncMock(),
        )
        view.patch_button = Mock()
        view.apply = AsyncMock(return_value=True)
        view.busy = False
        view.next_status = 100
        error = PermissionError("Access denied to game directory")

        def install():
            assert view.app.action_running
            view.app.tabs.select.assert_called_once_with(view.app.pages["Archipelago"])
            if failure == "worker":
                raise error
            # A guard may return normally after logging its reason. It must be
            # visible without suggesting that installation succeeded.
            logging.getLogger("Client").warning("Close KH3 before patching the game.")

        view.ctx = SimpleNamespace(
            kh3_dir=Path("test-game"), kh3_run_action_running=False,
            install_kh3_runtime_files_in_subprocess=Mock(side_effect=install),
        )
        if failure == "path":
            view.apply.side_effect = error

        await view.patch()

        if failure:
            view.app.form.assert_awaited_once_with("Could Not Patch KH3", str(error))
        else:
            view.app.form.assert_not_awaited()
            assert "Close KH3 before patching" in caplog.text
        if failure == "path":
            view.ctx.install_kh3_runtime_files_in_subprocess.assert_not_called()
        else:
            view.ctx.install_kh3_runtime_files_in_subprocess.assert_called_once_with()
        assert not view.busy
        assert not view.app.action_running
        assert view.next_status == 0
        if failure == "path":
            view.patch_button.configure.assert_not_called()
            view.patch_button.state.assert_not_called()
        else:
            view.patch_button.configure.assert_called_with(text="Patch Game")
            view.patch_button.state.assert_called_with(["!disabled"])

    asyncio.run(run())


def test_patch_does_not_reenable_button_owned_by_another_action():
    from worlds.kh3.gui.config import ConfigView

    view = ConfigView.__new__(ConfigView)
    view.apply = AsyncMock(return_value=False)
    view.patch_button = Mock()
    view.app = SimpleNamespace(closed=False, form=AsyncMock())
    asyncio.run(view.patch())
    view.patch_button.configure.assert_not_called()
    view.patch_button.state.assert_not_called()
    view.app.form.assert_not_awaited()


@pytest.mark.parametrize("source", ["steam", "egs"])
def test_native_detect_uses_selected_store_and_updates_both_paths(monkeypatch, tmp_path, source):
    from worlds.kh3.client_support import mom_installation
    from worlds.kh3.gui.config import ConfigView
    from worlds.kh3.platform_support import game_discovery

    class Value:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value

    game = tmp_path / source
    saves = game / "derived-test-saves"
    detect = Mock(return_value=game)
    monkeypatch.setattr(game_discovery, "detect_kh3_install_dir", detect)
    monkeypatch.setattr(mom_installation, "discover_installs", Mock(side_effect=AssertionError("MoM must not auto-detect")))
    ctx = SimpleNamespace(kh3_run_action_running=False, set_kh3_dir=Mock(),
                          clear_kh3_save_dir_override=Mock(), configure_probe_for_current_bridge=Mock(),
                          display_kh3_save_dir=Mock(return_value=saves))
    view = ConfigView.__new__(ConfigView)
    view.ctx, view.busy = ctx, False
    view.app = SimpleNamespace(action_running=False, prompt_patch_game_confirmation=AsyncMock(return_value=False))
    view.paths = {name: Value() for name in ("KH3 Dir", "Save Dir", "MoM Dir")}
    asyncio.run(view.detect("KH3 Dir", source))
    detect.assert_called_once_with(source=source)
    ctx.set_kh3_dir.assert_called_once_with(str(game), persist=True)
    ctx.clear_kh3_save_dir_override.assert_called_once_with(persist=True)
    assert view.paths["KH3 Dir"].get() == str(game)
    assert view.paths["Save Dir"].get() == str(saves)
    assert view.paths["MoM Dir"].get() == ""
