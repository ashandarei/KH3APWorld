# SPDX-License-Identifier: GPL-3.0-only
"""Player YAML round trips, generation-parser compatibility and native UI smoke."""

from copy import deepcopy
import os
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


@pytest.mark.parametrize("key,value", [
    ("heart_piece_count", -1), ("heart_piece_count", "oops"),
    ("death_link", {"true": -1}), ("death_link", {"true": 0}),
    ("death_link", {"true": "lots"}), ("death_link", "random-invalid"),
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
