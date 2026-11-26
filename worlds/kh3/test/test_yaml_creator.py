# SPDX-License-Identifier: GPL-3.0-only
"""Player YAML round trips, generation-parser compatibility and native UI smoke."""

from copy import deepcopy
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from Generate import roll_settings
from ..yaml_options import GAME, PlayerOptions, SettingsValidationError, catalog, clamp_range_value, entry_label
from ..yaml_presets import PresetStore


def test_range_clamping_preserves_special_and_unfinished_values():
    model = PlayerOptions()
    heart = model.types["heart_piece_count"]
    assert clamp_range_value(heart, "31") == 30
    assert clamp_range_value(heart, "2") == 3
    assert clamp_range_value(heart, "12") == 12
    for value in ("", "-", "oops", "1.5", "random", "random-low", {3: 10, 30: 20}):
        assert clamp_range_value(heart, value) == value
    assert clamp_range_value(model.types["battle_level_offset"], "10") == 10


def test_boss_picker_labels_cover_every_boss_without_duplicate_choices():
    from ..BossCatalog import RANDOMIZABLE_BOSS_IDS, resolve_boss_blacklist
    option = PlayerOptions().types["boss_blacklist"]
    keys = catalog(option)
    labels = [entry_label(option, key) for key in keys]
    assert len(labels) == len(set(labels))
    assert labels == sorted(labels, key=str.casefold)
    assert all(not entry_label(option, key).startswith("BOSS_") for key in option.valid_keys)
    assert resolve_boss_blacklist(keys) == RANDOMIZABLE_BOSS_IDS
    assert "Lava Titan" in labels
    assert labels.count("Yozora") == 1


def test_validation_reports_multiple_fields_and_conflicts_together():
    model = PlayerOptions()
    model.values.update(heart_piece_count="oops", death_link={"true": -1},
                        local_items=["Potion"], non_local_items=["Potion"])
    with pytest.raises(SettingsValidationError) as caught:
        model.export_text()
    assert set(caught.value.issues) == {"heart_piece_count", "death_link", "local_items", "non_local_items"}
    assert yaml.safe_load(model.draft_text())[GAME]["heart_piece_count"] == "oops"


def test_presets_persist_settings_with_generic_player_name_and_support_updates(tmp_path):
    store = PresetStore(tmp_path)
    model = PlayerOptions()
    model.name = ""
    model.values.update(heart_piece_count=7, death_link={"true": 20, "false": 80},
                        portal_unlocks="random", start_inventory={"Potion": 2})
    assert store.names() == []
    store.save("My run", model)
    reopened = PresetStore(tmp_path)
    assert reopened.names() == ["My run"]
    loaded = reopened.load("My run")
    assert loaded.values == model.values
    assert yaml.safe_load((tmp_path / "My run.yaml").read_text())["name"] == "Player"
    model.values["heart_piece_count"] = 9
    reopened.save("My run", model)
    assert store.load("My run").values["heart_piece_count"] == 9
    assert store.names() == ["My run"]


def test_deleting_a_preset_leaves_other_templates_intact(tmp_path):
    store = PresetStore(tmp_path)
    store.save("Keep", PlayerOptions())
    store.save("Remove", PlayerOptions())
    store.delete("Remove")
    assert store.names() == ["Keep"]
    assert store.load("Keep").name == "Player"
    with pytest.raises(ValueError):
        store.delete("../Keep")
    assert store.names() == ["Keep"]


@pytest.mark.parametrize("name", ["", "../escape", "a/b", "a\\b", "CON", "NUL.txt", "trail.", "x" * 101])
def test_preset_names_cannot_escape_storage_or_use_invalid_filenames(tmp_path, name):
    with pytest.raises(ValueError):
        PresetStore(tmp_path).save(name, PlayerOptions())
    assert not list(tmp_path.iterdir())


def test_failed_preset_save_preserves_previous_settings(tmp_path):
    store = PresetStore(tmp_path)
    model = PlayerOptions()
    store.save("Run", model)
    before = (tmp_path / "Run.yaml").read_bytes()
    model.values["heart_piece_count"] = 9
    with patch("worlds.kh3.yaml_presets.os.replace", side_effect=OSError("Disk error")):
        with pytest.raises(OSError):
            store.save("Run", model)
    assert (tmp_path / "Run.yaml").read_bytes() == before
    assert list(tmp_path.iterdir()) == [tmp_path / "Run.yaml"]


def test_defaults_round_trip_through_archipelago():
    model = PlayerOptions()
    text = model.export_text()
    loaded = PlayerOptions.from_text(text)
    assert loaded.values == model.values
    rolled = roll_settings(yaml.safe_load(text))
    for key, option in model.types.items():
        assert getattr(rolled, key).value == option.from_any(deepcopy(option.default)).value


def test_blank_player_name_exports_as_player():
    model = PlayerOptions()
    model.name = "   "
    assert yaml.safe_load(model.export_text())["name"] == "Player"
    model.name = "Sora"
    assert yaml.safe_load(model.export_text())["name"] == "Sora"


def test_emblem_limit_exports_as_a_number_and_stylesanity_as_a_toggle():
    model = PlayerOptions()
    for count in range(10, 91, 10):
        model.values["lucky_emblem_limit"] = str(count)
        document = yaml.safe_load(model.export_text())
        assert document[GAME]["lucky_emblem_limit"] == count
        assert document[GAME]["stylesanity"] is True
        assert roll_settings(document).lucky_emblem_limit.value == count
    model.values["lucky_emblem_limit"] = 15
    with pytest.raises(ValueError):
        model.export_text()


def test_choices_ranges_inventory_and_special_characters_round_trip():
    model = PlayerOptions()
    model.name = "Sora: #1"
    item = catalog(model.types["start_inventory"])[0]
    model.values.update(start_with_magic=False, heart_piece_count=7,
                        portal_unlocks="random", start_inventory={item: 2})
    loaded = PlayerOptions.from_text(model.export_text())
    assert loaded.name == model.name
    assert loaded.values["portal_unlocks"] == "random"
    rolled = roll_settings(yaml.safe_load(model.export_text()))
    assert rolled.heart_piece_count.value == 7
    assert rolled.start_inventory.value == {item: 2}
    assert rolled.start_with_magic.value == 0


def test_export_keeps_random_and_weighted_choices_unrolled():
    model = PlayerOptions()
    model.values["start_with_magic"] = "random"
    model.values["heart_piece_count"] = "random-low"
    model.values["death_link"] = {"true": 20, "false": 80}
    loaded = PlayerOptions.from_text(model.export_text())
    assert loaded.values == model.values
    roll_settings(yaml.safe_load(model.export_text()))


def test_random_subsets_and_ranges_survive_presets_and_roll_in_archipelago(tmp_path):
    model = PlayerOptions()
    model.values.update(ultima_weapon_mode=["shards", "yozora"], lucky_emblem_limit=[30, 50, 60],
                        ultima_weapon_shard_count="random-range-5-9", battle_level_offset=[-2, -1, 0])
    store = PresetStore(tmp_path)
    store.save("Mystery", model)
    loaded = store.load("Mystery")
    assert loaded.values == model.values
    document = yaml.safe_load(loaded.export_text())
    for _ in range(10):
        rolled = roll_settings(document)
        assert rolled.ultima_weapon_mode.current_key in ("shards", "yozora")
        assert rolled.lucky_emblem_limit.value in (30, 50, 60)
        assert 5 <= rolled.ultima_weapon_shard_count.value <= 9
        assert -2 <= rolled.battle_level_offset.value <= 0


@pytest.mark.parametrize("key,value", [
    ("heart_piece_count", -1), ("heart_piece_count", "oops"),
    ("death_link", {"true": -1}), ("death_link", {"true": 0}),
    ("death_link", {"true": "lots"}), ("death_link", "random-invalid"),
    ("ultima_weapon_mode", []), ("ultima_weapon_mode", [["shards"]]),
    ("lucky_emblem_limit", [30, 45]), ("ultima_weapon_shard_count", "random-range-4-14"),
    ("ultima_weapon_shard_count", "random-range-9-5"),
    ("start_inventory", {"No such KH3 item": 1}),
    ("start_inventory", {"Potion": -1}),
    ("start_inventory", {"Potion": 1.5}),
    ("boss_blacklist", ["No such boss"]), ("boss_blacklist", "not a list"),
])
def test_invalid_settings_rejected(key, value):
    model = PlayerOptions()
    model.values[key] = value
    with pytest.raises(ValueError):
        model.export_text()


def test_conflicting_local_items_rejected():
    model = PlayerOptions()
    item = catalog(model.types["local_items"])[0]
    model.values.update(local_items=[item], non_local_items=[item])
    with pytest.raises(ValueError, match="both local"):
        model.export_text()


@pytest.mark.parametrize("document", [
    {"game": "Another game"},
    {"game": GAME, GAME: {"unknown_option": True}},
    {"game": GAME, "triggers": []},
    {"game": GAME, GAME: []},
    {"game": GAME, "name": ["Sora", "Riku"]},
])
def test_import_rejects_unsupported_content_instead_of_dropping_it(document):
    with pytest.raises(ValueError):
        PlayerOptions.from_text(yaml.safe_dump(document))


def test_launcher_registration_and_scope_restoration():
    from .. import launch_yaml_creator
    from worlds.LauncherComponents import components
    assert any(component.display_name == "KH3 YAML Creator" and component.func == launch_yaml_creator
               for component in components)
    before = os.environ.get("AP_WORLD_LOAD_PATHS")
    with patch("worlds.LauncherComponents.launch_subprocess") as spawn:
        launch_yaml_creator()
        assert spawn.call_args.kwargs["name"] == "KH3YamlCreator"
    assert os.environ.get("AP_WORLD_LOAD_PATHS") == before


@pytest.mark.parametrize("include_current", [True, False])
def test_generation_stages_only_selected_files(tmp_path, monkeypatch, include_current):
    from .. import yaml_generation

    first = tmp_path / "meta.yaml"
    second = tmp_path / "subdir" / first.name
    second.parent.mkdir()
    first.write_text("name: Friend", encoding="utf-8")
    second.write_text("name: Other", encoding="utf-8")
    content = PlayerOptions().export_text() if include_current else None
    monkeypatch.setenv("AP_WORLD_LOAD_PATHS", '["kh3"]')
    component = tmp_path / "Generate"
    component.touch()

    def generate(argv, *, cwd, env, **kwargs):
        assert argv[0] == str(component)
        assert cwd == yaml_generation.Utils.local_path()
        assert "AP_WORLD_LOAD_PATHS" not in env
        players = Path(argv[argv.index("--player_files_path") + 1])
        expected = {"name: Friend", "name: Other"}
        if include_current:
            expected.add(content)
        assert {p.read_text(encoding="utf-8") for p in players.iterdir()} == expected
        for flag in ("--weights_file_path", "--meta_file_path"):
            assert not Path(argv[argv.index(flag) + 1]).exists()
        (Path(argv[argv.index("--outputpath") + 1]) / "AP_1.zip").touch()
        return subprocess.CompletedProcess(argv, 0)

    with patch.object(yaml_generation, "get_exe", return_value=[str(component)]) as resolve, \
            patch.object(yaml_generation.Utils, "output_path", return_value=str(tmp_path)), \
            patch.object(subprocess, "run", side_effect=generate):
        assert yaml_generation.generate_archive(content, [first, second]).is_file()
    resolve.assert_called_once_with("Generate")
    assert os.environ["AP_WORLD_LOAD_PATHS"] == '["kh3"]'


def test_local_server_uses_a_free_port_when_another_room_is_on_loopback():
    from ..yaml_generation import _available_port

    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        occupied = existing.getsockname()[1]
        assert _available_port("", occupied) != occupied


def test_advanced_settings_only_override_the_current_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from .. import yaml_generation as generation

    source = tmp_path / "options.yaml"
    source.write_text("server_options:\n  hint_cost: 20\n  location_check_points: 3\nother_world: {enabled: true}\n")
    before = source.read_bytes()
    server = SimpleNamespace(hint_cost=20, release_mode="auto", collect_mode="auto", remaining_mode="goal")
    installation = SimpleNamespace(filename=str(source), generator=SimpleNamespace(spoiler=3), server_options=server)
    monkeypatch.setattr(generation.Utils, "get_settings", lambda: installation)
    monkeypatch.setattr(generation.Utils, "output_path", lambda: str(tmp_path))
    monkeypatch.setattr(generation, "_command", lambda _: ["Generate"])
    selected = generation.GenerationSettings(0, 0, "disabled", "enabled", "goal")

    def generate(argv, *, cwd, **kwargs):
        assert argv[argv.index("--spoiler") + 1] == "0"
        config = yaml.safe_load((Path(cwd) / "host.yaml").read_text(encoding="utf-8"))
        assert config["server_options"] == {"location_check_points": 3, **selected.server_options()}
        assert config["other_world"] == {"enabled": True}
        (Path(argv[argv.index("--outputpath") + 1]) / "AP_1.zip").touch()
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", generate)
    assert generation.generate_archive(PlayerOptions().export_text(), [], selected).is_file()
    assert source.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Requires a desktop")
def test_posted_windows_file_drops_survive_the_tk_mainloop(tmp_path):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", textwrap.dedent(r'''
        import ctypes as ct
        from ctypes import wintypes as wt
        from pathlib import Path
        import runpy
        import struct
        import sys
        import tkinter as tk

        bind = runpy.run_path("worlds/kh3/gui/file_drop.py")["bind_file_drop"]
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        user = ct.WinDLL("user32", use_last_error=True)
        kernel.GlobalAlloc.argtypes = (wt.UINT, ct.c_size_t)
        kernel.GlobalAlloc.restype = wt.HGLOBAL
        kernel.GlobalLock.argtypes = (wt.HGLOBAL,)
        kernel.GlobalLock.restype = ct.c_void_p
        kernel.GlobalUnlock.argtypes = (wt.HGLOBAL,)
        user.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        root = tk.Tk()
        root.withdraw()
        paths = [str(Path(sys.argv[1]) / name) for name in ("Sora and Riku.yaml", "雪.yml")]
        raw = struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(paths) + "\0\0").encode("utf-16-le")
        received = []

        def post_drop(hwnd):
            handle = kernel.GlobalAlloc(0x42, len(raw))
            assert handle
            ct.memmove(kernel.GlobalLock(handle), raw, len(raw))
            kernel.GlobalUnlock(handle)
            # Explorer posts WM_DROPFILES. Synchronous SendMessage from Python
            # bypasses the native Tk dispatch that triggered the original abort.
            assert user.PostMessageW(hwnd, 0x233, handle, 0)

        def open_dialog():
            window = tk.Toplevel(root)
            def receive(files):
                received.append(files)
                if len(received) % 2:
                    window.after_idle(post_drop, window.winfo_id())
                else:
                    window.destroy()
                    root.after_idle(open_dialog if len(received) < 4 else root.quit)
            assert bind(window, receive)
            window.after(10, post_drop, window.winfo_id())

        root.after_idle(open_dialog)
        root.after(5000, root.quit)
        root.mainloop()
        root.destroy()
        assert received == [paths] * 4, received
    '''), str(tmp_path)], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Requires a desktop")
def test_native_creator_categories_search_and_editor_callbacks():
    root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["AP_WORLD_LOAD_PATHS"] = '["' + (root / "worlds" / "kh3").as_posix() + '"]'
    result = subprocess.run([sys.executable, "-c", textwrap.dedent('''
        import tkinter as tk
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from worlds.kh3.gui.yaml_creator import AutoScrollbar, YamlCreator
        from worlds.kh3.yaml_options import PlayerOptions
        preset_directory = TemporaryDirectory()
        app = YamlCreator(preset_directory=preset_directory.name)
        assert app.name.get() == ''
        app.name.set('Player')
        errors = []
        app.root.report_callback_exception = lambda *args: errors.append(args)
        app.root.withdraw()
        original_rows = {key: row['frame'] for key, row in app.rows.items()}
        with patch.object(app, '_card', side_effect=AssertionError('navigation rebuilt a row')):
            for group in app.model.groups:
                app.show_group(group)
                app.root.update()
            for query in ('m', 'ma', 'mag', 'magic', 'heart piece'):
                app.search.set(query)
                app.render()
                app.root.update()
        assert {key: row['frame'] for key, row in app.rows.items()} == original_rows
        assert 'heart_piece_count' in app.editors
        app.editors['heart_piece_count'].set('9')
        assert app.model.values['heart_piece_count'] == 9
        app.reset_option('heart_piece_count')
        app._randomize('death_link', True)
        assert app.model.values['death_link'] == 'random'
        app.changed_only.set(True)
        app.render()
        app.pick_entries('start_inventory')
        app.root.update()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        picker = next(child for child in app.root.winfo_children()
                      if isinstance(child, tk.Toplevel) and child is not app.tooltip_surface.window)
        widgets = list(descendants(picker))
        lists = [widget for widget in widgets if isinstance(widget, tk.Listbox)]
        first_item = lists[0].get(0)
        lists[0].selection_set(0)
        next(widget for widget in widgets if isinstance(widget, tk.Button)
             and widget.cget('text') == 'Add / set count').invoke()
        next(widget for widget in widgets if isinstance(widget, tk.Button)
             and widget.cget('text') == 'Apply').invoke()
        assert app.model.values['start_inventory'] == {first_item: 1}

        app.changed_only.set(False)
        app.root.deiconify()
        for key, first, second, expected in (
                ('ultima_weapon_mode', 'shards', 'yozora', ['shards', 'yozora']),
                ('lucky_emblem_limit', '50', '30', [30, 50])):
            app.search.set(key)
            app.render()
            app.root.update()
            row = app.rows[key]
            buttons = {str(button.cget('value')): button for button in row['selector'].buttons}
            def selected():
                return {name for name, button in buttons.items() if button.instate(['selected'])}
            def mouse(name, number, modifier=''):
                button = buttons[name]
                for action in ('Press', 'Release'):
                    button.event_generate(f'<{modifier}Button{action}-{number}>', x=2, y=2)
                app.root.update()

            assert selected() == {first}
            mouse(second, 1, 'Shift-')
            assert selected() == {first, second} and row['random'].get()
            assert app.model.values[key] == expected
            app._replace_model(PlayerOptions.from_text(app._export_text()))
            assert selected() == {first, second}
            mouse(first, 3)
            assert selected() == {second} and not row['random'].get()
            mouse(second, 3)
            assert selected() == {second}
            mouse(first, 3)
            assert selected() == {first, second}
            mouse(first, 1)
            assert selected() == {first} and not row['random'].get()
            row['random_button'].invoke()
            assert selected() == set(buttons)
            mouse(first, 1, 'Shift-')
            assert selected() == set(buttons) - {first}
            app.reset_option(key)
            assert selected() == {first}
        for key, lower, upper, expected in (('ultima_weapon_shard_count', 5, 9, 'random-range-5-9'),
                                            ('battle_level_offset', -2, 1, [-2, -1, 0, 1])):
            app.search.set(key)
            app.render()
            row = app.rows[key]
            with patch('tkinter.Toplevel', side_effect=AssertionError('expected inline fields')):
                row['random_button'].invoke()
                app.root.update()
            fields = [w for w in descendants(row['editor']) if w.winfo_class() == 'TSpinbox']
            minimum, maximum = fields
            assert all(w.winfo_viewable() for w in fields)
            option = app.model.types[key]
            minimum.set(option.range_start - 99)
            minimum.event_generate('<FocusOut>')
            maximum.set(option.range_end + 99)
            app._export_text()
            assert [int(w.get()) for w in fields] == [option.range_start, option.range_end]
            assert app.model.values[key] == 'random'
            minimum.event_generate('<<Decrement>>')
            maximum.event_generate('<<Increment>>')
            assert [int(w.get()) for w in fields] == [option.range_start, option.range_end]
            minimum.set(lower)
            minimum.event_generate('<FocusOut>')
            maximum.set(upper)
            app._export_text()
            assert app.model.values[key] == expected
            assert float(maximum.cget('from')) == lower and float(minimum.cget('to')) == upper
            saved = app._export_text()
            minimum.set(upper + 1)
            minimum.event_generate('<FocusOut>')
            assert [int(w.get()) for w in fields] == [upper, upper]
            app._replace_model(PlayerOptions.from_text(saved))
            assert [int(w.get()) for w in fields] == [lower, upper]
            maximum.set(lower - 1)
            maximum.event_generate('<FocusOut>')
            assert [int(w.get()) for w in fields] == [lower, lower]
            maximum.set('')
            app._export_text()
            assert int(maximum.get()) == lower
            app.reset_option(key)
        app.root.withdraw()

        app._set('boss_blacklist', ['BOSS_0106'])
        app.pick_entries('boss_blacklist')
        picker = next(child for child in app.root.winfo_children()
                      if isinstance(child, tk.Toplevel) and child is not app.tooltip_surface.window)
        widgets = list(descendants(picker))
        lists = [widget for widget in widgets if isinstance(widget, tk.Listbox)]
        assert not any('BOSS_' in label for label in lists[0].get(0, 'end'))
        assert lists[1].get(0, 'end') == ('Yozora',)
        lists[0].selection_set(lists[0].get(0, 'end').index('Lava Titan'))
        next(widget for widget in widgets if isinstance(widget, tk.Button)
             and widget.cget('text') == 'Add').invoke()
        next(widget for widget in widgets if isinstance(widget, tk.Button)
             and widget.cget('text') == 'Apply').invoke()
        assert set(app.model.values['boss_blacklist']) == {'BOSS_002', 'BOSS_0106'}
        assert 'BOSS_' not in app.rows['boss_blacklist']['picker'].tooltip.text
        for song_option in ('melody_of_memory_song_whitelist', 'melody_of_memory_song_blacklist'):
            app.pick_entries(song_option)
            picker = next(child for child in app.root.winfo_children()
                          if isinstance(child, tk.Toplevel) and child is not app.tooltip_surface.window)
            widgets = list(descendants(picker))
            lists = [widget for widget in widgets if isinstance(widget, tk.Listbox)]
            dropdown = next(widget for widget in widgets if widget.winfo_class() == 'TCombobox')
            search = next(widget for widget in widgets if widget.winfo_class() == 'TEntry')
            all_entries = lists[0].get(0, 'end')
            for category in ('Source', 'Theme', 'World'):
                dropdown.set(category)
                entries = lists[0].get(0, 'end')
                assert entries and all(entry.startswith(category + ':') for entry in entries)
            lists[0].selection_set(0)
            selected_world = lists[0].get(0)
            next(widget for widget in widgets if isinstance(widget, tk.Button)
                 and widget.cget('text') == 'Add').invoke()
            dropdown.set('Songs')
            assert lists[0].size() > 0
            assert not any(entry.startswith(('Source:', 'Theme:', 'World:')) for entry in lists[0].get(0, 'end'))
            dropdown.set('Source')
            search.insert(0, 'Kingdom Hearts')
            assert lists[0].size() > 0
            assert all('kingdom hearts' in entry.casefold() for entry in lists[0].get(0, 'end'))
            assert lists[1].get(0, 'end') == (selected_world,)
            search.delete(0, 'end')
            dropdown.set('All')
            assert lists[0].get(0, 'end') == all_entries
            next(widget for widget in widgets if isinstance(widget, tk.Button)
                 and widget.cget('text') == 'Apply').invoke()
            assert app.model.values[song_option] == [selected_world]
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'player.yaml'
            with patch('tkinter.filedialog.asksaveasfilename', return_value=str(target)):
                app.export_file()
            assert target.is_file()
            app.model.name = 'Changed'
            with patch('tkinter.messagebox.askyesno', side_effect=AssertionError('discard prompt')), \\
                    patch('tkinter.filedialog.askopenfilename', return_value=str(target)):
                app.import_file()
            assert app.model.name == 'Player'
            assert app.model.values['start_inventory'] == {first_item: 1}
            from worlds.kh3.gui.yaml_generation import GenerationDialog
            dialog = GenerationDialog(app, app.model.export_text(), app.model.name)
            assert dialog.listing.get(0, 'end') == ('Player (Kingdom Hearts III)',)
            assert dialog.listing.cget('activestyle') == 'none'
            other = Path(directory) / 'friend.yml'
            other.write_text('name: Riku\\ngame: ChecksFinder', encoding='utf-8')
            first = Path(directory) / 'zzz.yml'
            first.write_text('name: aqua\\ngame: Timespinner', encoding='utf-8')
            dialog.listing.selection_set(0)
            dialog.add_files([other, first, other])
            assert dialog.listing.get(0, 'end') == (
                'aqua (Timespinner)', 'Player (Kingdom Hearts III)', 'Riku (ChecksFinder)')
            dialog.remove_selected()
            assert dialog.listing.get(0, 'end') == ('aqua (Timespinner)', 'Riku (ChecksFinder)')
            dialog.listing.selection_set(0, 'end')
            dialog.remove_selected()
            assert dialog.listing.size() == 0
            assert all(b.cget('state') == 'disabled' for b in (dialog.generate, dialog.host))
            dialog.add_files([other, other])
            assert dialog.listing.get(0, 'end') == ('Riku (ChecksFinder)',)
            from worlds.kh3.yaml_generation import GenerationSettings
            advanced = dialog.advanced
            assert not advanced.panel.winfo_manager()
            assert advanced.values() == GenerationSettings.from_installation()
            advanced.toggle.invoke()
            assert advanced.panel.winfo_manager()
            ticker = advanced.hint_cost
            assert ticker.winfo_class() == 'TSpinbox'
            app.root.update_idletasks()
            ticker.delete(0, 'end')
            ticker.insert(0, '24')
            ticker.event_generate('<<Increment>>')
            assert advanced.values().hint_cost == 25
            ticker.event_generate('<<Decrement>>')
            assert advanced.values().hint_cost == 24
            ticker.set('')
            with patch.object(dialog, 'run_task') as run:
                dialog.host.invoke()
                run.assert_not_called()
            assert not dialog.busy and 'whole number' in dialog.status.get()
            ticker.set(str(advanced.hint_min - 1))
            ticker.event_generate('<FocusOut>')
            assert ticker.get() == str(advanced.hint_min)
            ticker.set(0)
            expected = GenerationSettings(0, 0, "disabled", "goal", "enabled")
            for key, (combo, choices) in advanced.choices.items():
                combo.current(choices.index(getattr(expected, key)))
            advanced.toggle.invoke()
            assert advanced.values() == expected
            with patch.object(dialog, 'run_task') as run:
                dialog.generate.invoke()
                assert run.call_args.args[1] == (None, (other,), expected)
            dialog.show_result('Could not finish generation', 'Generation failed', retry=True)
            dialog.result_action.invoke()
            assert dialog.form.winfo_manager() and dialog.listing.get(0, 'end') == ('Riku (ChecksFinder)',)
            with patch.object(dialog, 'run_task') as run:
                dialog.host.invoke()
                run.call_args.args[2](Path(directory) / 'test.zip')
                assert dialog.status.get() == 'Starting local server…'
                assert run.call_args.args[1] == (Path(directory) / 'test.zip', expected)
                with patch('tkinter.messagebox.showinfo', side_effect=AssertionError('unexpected popup')):
                    run.call_args.args[2]('127.0.0.1:38281')
            assert dialog.result.winfo_manager() and not dialog.form.winfo_manager()
            assert dialog.result_title.cget('text') == 'Local server started'
            assert '127.0.0.1:38281' in dialog.details.get('1.0', 'end')
            dialog.result_action.invoke()
            assert not dialog.window.winfo_exists()
        app.preview()
        app.root.update()
        assert not errors, errors
        app.review.close()
        app.editors['heart_piece_count'].set('2')
        spin = next(iter(app.rows['heart_piece_count']['editor'].winfo_children()))
        spin.event_generate('<FocusOut>')
        app.root.update()
        assert app.editors['heart_piece_count'].get() == '3'
        app.editors['heart_piece_count'].set('31')
        app._commit_range('heart_piece_count')
        assert app.model.values['heart_piece_count'] == 30
        app.editors['heart_piece_count'].set('2')
        valid_draft = app._export_text()
        assert app.model.values['heart_piece_count'] == 3
        app._set('heart_piece_count', 'oops')
        app._set('death_link', {'true': -1})
        with patch('tkinter.messagebox.showerror', side_effect=AssertionError('native validation dialog')):
            assert app._export_text() is None
            app.root.update()
            review = app.review
            assert set(review.issues) == {'heart_piece_count', 'death_link'}
            assert len(review.issue_tags) == 2
            assert all(review.source.tag_ranges(tag) for tag in review.issue_tags)
            assert 'Heart Pieces Required' in review.details.get('1.0', 'end')
            review.source.delete('1.0', 'end')
            review.source.insert('1.0', valid_draft)
            assert review.check(apply=True)
            assert app.review is None
            assert app.model.values['heart_piece_count'] == 3
        with patch('tkinter.simpledialog.askstring', return_value='My preset'):
            app.save_preset()
        assert tuple(app.preset_picker.cget('values')) == ('My preset',)
        saved_count = app.model.values['heart_piece_count']
        app.name.set('Other player')
        app._set('heart_piece_count', 9)
        app.load_preset()
        assert app.model.values['heart_piece_count'] == saved_count
        assert app.name.get() == 'Other player'
        app._set('heart_piece_count', 9)
        app.save_preset()
        assert app.presets.load('My preset').values['heart_piece_count'] == 9
        current_values = app.model.values.copy()
        app.delete_preset()
        assert app.model.values == current_values
        assert not app.presets.names()
        assert app.active_preset is None
        assert str(app.delete_preset_button.cget('state')) == 'disabled'
        assert not app.preset_picker.cget('values')
        overflow = tk.Toplevel(app.root)
        overflow.geometry('240x120')
        listing = tk.Listbox(overflow)
        scrollbar = AutoScrollbar(overflow, listing)
        listing.pack(fill='both', expand=True)
        listing.insert('end', 'One item')
        app.root.update()
        assert not scrollbar.winfo_manager()
        for index in range(100):
            listing.insert('end', str(index))
        app.root.update()
        assert scrollbar.winfo_manager() == 'pack'
        listing.delete(0, 'end')
        app.root.update()
        assert not scrollbar.winfo_manager()
        overflow.destroy()
        with patch('tkinter.messagebox.askyesno', side_effect=AssertionError('discard prompt')):
            app.reset()
            frames = {key: row['frame'] for key, row in app.rows.items()}
            candidate = PlayerOptions()
            candidate.name = 'Imported'
            candidate.values.update(heart_piece_count=9, death_link=True,
                                    lucky_emblem_limit=90, start_inventory={'Potion': 2})
            with patch.object(app, '_card', side_effect=AssertionError('ordinary load/reset rebuilt a row')):
                app._replace_model(candidate)
                app.root.update()
                assert app.editors['heart_piece_count'].get() == '9'
                assert app.editors['death_link'].get() is True
                assert app.rows['death_link']['switch'].cget('text').strip() == 'On'
                assert app.editors['lucky_emblem_limit'].get() == '90'
                assert app.rows['start_inventory']['picker'].tooltip.text == 'Potion'
                assert not app.dirty_rows
                app.reset()
                app.root.update()
                assert app.editors['death_link'].get() is False
                assert app.rows['death_link']['switch'].cget('text').strip() == 'Off'
                assert app.rows['start_inventory']['picker'].cget('text') == '0 selected…'
            assert {key: row['frame'] for key, row in app.rows.items()} == frames
            candidate.values.update(death_link={'true': 20, 'false': 80}, heart_piece_count='random-low')
            with patch.object(app, '_card', wraps=app._card) as rebuild:
                app._replace_model(candidate)
                assert {call.args[0] for call in rebuild.call_args_list} == {'death_link', 'heart_piece_count'}
            assert app.rows['death_link']['kind'] == 'weighted'
            assert app.rows['heart_piece_count']['kind'] == 'random'
            app.reset()
            app.editors['heart_piece_count'].set('9')
            assert app.model.values['heart_piece_count'] == 9
            app.root.update()
            assert not errors, errors
            app.root.deiconify()
            app.show_group(min(app.model.groups, key=lambda group: len(app.model.groups[group])))
            app.root.update()
            assert app.cards.winfo_reqheight() <= app.canvas.winfo_height()
            origin = app.canvas.canvasy(0)
            wheel = tk.Event()
            wheel.widget = app.canvas
            for direction in (120, 120, -120, -120):
                wheel.delta = direction
                app._wheel(wheel)
                app.root.update()
                assert app.canvas.canvasy(0) == origin
            assert not app.settings_scroll.winfo_manager()
            app.search.set('a')
            app.render()
            app.root.update()
            app.canvas.yview_moveto(.4)
            app.root.update()
            row = app.rows['heart_piece_count']
            original_height = row['frame'].winfo_reqheight()
            permanent = tuple(row[key] for key in ('frame', 'label', 'reset', 'help', 'random_button'))
            with patch.object(app, 'render', side_effect=AssertionError('random toggle rendered the panel')):
                row['random_button'].invoke()
                app.root.update()
                assert app.model.values['heart_piece_count'] == 'random'
                assert row['frame'].winfo_reqheight() == original_height
                row['random_button'].invoke()
                app.root.update()
                assert app.rows['heart_piece_count']['kind'] == 'value'
                assert row['frame'].winfo_reqheight() == original_height
            assert app.canvas.yview()[0] > .2
            assert permanent == tuple(row[key] for key in ('frame', 'label', 'reset', 'help', 'random_button'))
            assert all(str(row['reset'].cget('image')) == str(app.row_icons['reset']) for row in app.rows.values())
            assert all(str(row['help'].cget('image')) == str(app.row_icons['help']) for row in app.rows.values())
            app.show_group(next(iter(app.model.groups)))
            app.root.update()
            surface = app.tooltip_surface
            with patch('tkinter.Toplevel', side_effect=AssertionError('tooltip created another window')), \\
                    patch('tkinter.Text', side_effect=AssertionError('tooltip rebuilt its contents widget')), \\
                    patch.object(surface.font, 'measure', side_effect=AssertionError('tooltip size was not prepared')):
                for key in ('heart_piece_count', 'death_link'):
                    tip = app.rows[key]['help'].tooltip
                    tip.show()
                    app.root.update()
                    assert surface.owner is tip
                    assert surface.details.get('1.0', 'end-1c') == tip.text
                    tip.hide()
            original_sizes = {name: value.cget('size') for name, value in app.fonts.items()}
            with patch.object(app, '_card', side_effect=AssertionError('zoom rebuilt rows')):
                app.change_zoom(1)
                app.root.update()
                assert app.font_size == 13
                assert all(value.cget('size') == original_sizes[name] + 1 for name, value in app.fonts.items())
                app.change_zoom(-1)
                app.root.update()
                assert app.font_size == 12
            assert not errors, errors
            app.name.set('Changed')
            app.close()
        preset_directory.cleanup()
    ''')], cwd=root, env=environment, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
