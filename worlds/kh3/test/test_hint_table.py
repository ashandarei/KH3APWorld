"""Sorting and local filters must survive incoming hint updates."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..gui.hints import HintTable


@pytest.fixture
def table():
    table = HintTable.__new__(HintTable)
    table.rows = []
    table.visible_rows = []
    table.selected = None
    table.sort_column = None
    table.sort_reverse = False
    table.filters = {}
    table.canvas = Mock()
    table.canvas.winfo_width.return_value = sum(weight for _, weight in table.columns)
    table.filter_summary = Mock()
    table.filter_bar = Mock()
    table.redraw = Mock()
    return table


def row(*values):
    return tuple(values), ("yellow", "plum", "yellow", "green", "white")


@pytest.mark.parametrize("column", range(5))
def test_every_header_toggles_natural_sort_and_keeps_selection(table, column):
    rows = []
    for value in ("entry 10", "Entry 2", "entry 1"):
        values = ["same"] * 5
        values[column] = value
        rows.append(row(*values))
    table.set_rows(rows)
    table.selected = 0
    event = SimpleNamespace(x=sum(weight for _, weight in table.columns[:column]) + 10)
    table.sort_header(event)
    assert [r[0][column] for r in table.visible_rows] == ["entry 1", "Entry 2", "entry 10"]
    assert table.visible_rows[table.selected] == rows[0]
    table.sort_header(event)
    assert [r[0][column] for r in table.visible_rows] == ["entry 10", "Entry 2", "entry 1"]
    assert table.rows == rows


def test_filters_combine_and_persist_on_hint_updates(table):
    sora = row("Sora", "Fire", "Riku", "Chest 10", "Priority")
    aqua = row("Aqua", "Fire", "Riku", "Chest 2", "Found")
    other = row("Sora", "Blizzard", "Aqua", "Chest 3", "Found")
    table.set_rows([sora, aqua, other])
    table.select_filter_values(1, ["Fire"])
    table.set_filter(0, "only", "sora")
    assert table.visible_rows == [sora]
    table.set_filter(4, "hide", "Priority")
    assert not table.visible_rows
    table.set_rows([row("Sora", "Fire", "Riku", "Chest 10", "Found"), aqua, other])
    assert len(table.visible_rows) == 1
    assert table.visible_rows[0][0][4] == "Found"
    table.clear_filters(0)
    assert len(table.visible_rows) == 2
    table.clear_filters(1)
    assert len(table.visible_rows) == 3
    table.clear_filters()
    assert not table.filters
    table.filter_bar.grid_remove.assert_called()


def test_refresh_retains_sort_and_clears_hidden_selection(table):
    first = row("Sora", "Fire", "Riku", "Chest 10", "Priority")
    second = row("Aqua", "Fire", "Riku", "Chest 2", "Found")
    table.set_rows([first])
    table.sort_header(SimpleNamespace(x=10))
    table.set_rows([first, second])
    assert table.visible_rows == [second, first]
    table.selected = 0
    table.set_filter(0, "only", "Sora")
    assert table.selected is None
    assert table.sort_column == 0


def test_value_picker_lists_all_unique_values_and_all_or_none_selection(table):
    rows = [row("Sora", item, "Riku", "Chest 1", "Priority") for item in ("Fire", "Cure", "Fire", "Blizzard")]
    table.set_rows(rows)
    assert table.filter_values(1) == ["Blizzard", "Cure", "Fire"]
    table.select_filter_values(1, ["Cure", "Fire"])
    assert len(table.visible_rows) == 3
    assert table.filter_values(1) == ["Blizzard", "Cure", "Fire"]
    table.select_filter_values(1, [])
    assert table.visible_rows == []
    table.select_filter_values(1, table.filter_values(1))
    assert 1 not in table.filters
    assert table.visible_rows == rows
