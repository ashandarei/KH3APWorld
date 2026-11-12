"""Sorting and local filters must survive incoming hint updates."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from NetUtils import HintStatus

from ..gui.hints import EDITABLE_STATUSES, HintRow, HintsView, HintTable


@pytest.fixture
def table():
    table = HintTable.__new__(HintTable)
    table.rows = []
    table.visible_rows = []
    table.selected = None
    table.session = None
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
    return HintRow(tuple(values), ("yellow", "plum", "yellow", "green", "white"),
                   dict(receiving_player=values[0], finding_player=values[2], location=values, found=False))


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


@pytest.fixture
def connected(table):
    ctx = SimpleNamespace(server=SimpleNamespace(socket=SimpleNamespace(open=True, closed=False)), team=0, slot=1,
                          slot_concerns_self=lambda slot: slot in (1, 3), player_names={1: "Sora", 2: "Riku", 3: "Group"},
                          item_names=SimpleNamespace(lookup_in_slot=lambda item, slot: f"Item {item}"),
                          location_names=SimpleNamespace(lookup_in_slot=lambda location, slot: f"Chest {location}"),
                          update_hint=Mock())
    table.app = SimpleNamespace(ctx=ctx)
    view = HintsView.__new__(HintsView)
    view.app = table.app
    view.table = table
    return ctx, view


def hint(**changes):
    return dict(dict(receiving_player=1, finding_player=2, item=100, location=200, found=False,
                     status=HintStatus.HINT_UNSPECIFIED), **changes)


@pytest.mark.parametrize("status", EDITABLE_STATUSES)
def test_status_updates_use_server_ids_after_sorting_and_filtering(table, connected, status):
    ctx, view = connected
    view.refresh([hint(location=10, status=HintStatus.HINT_AVOID if status == 0 else 0),
                  hint(location=2), hint(receiving_player=2, location=3)])
    table.sort_header(SimpleNamespace(x=560))
    table.set_filter(0, "only", "Sora")
    target = table.visible_rows[1]
    assert target.hint["location"] == 10
    table.update_status(target.key, status, table.session)
    ctx.update_hint.assert_called_once_with(10, 2, status)
    assert target.values[4] != {0: "Unspecified", 10: "No Priority", 20: "Avoid", 30: "Priority"}[status]


@pytest.mark.parametrize("change", ["found", "foreign", "closed", "disconnect", "slot", "team", "server", "removed"])
def test_stale_or_read_only_hint_cannot_be_updated(table, connected, change):
    ctx, view = connected
    view.refresh([hint()])
    target, session = table.rows[0], table.session
    if change == "found":
        view.refresh([hint(found=True)])  # Found wins even if a stale priority is present.
        assert table.rows[0].values[4] == "Found"
    elif change == "foreign":
        ctx.slot_concerns_self = lambda slot: False
    elif change == "closed":
        ctx.server.socket.closed = True
    elif change == "disconnect":
        ctx.server = None
    elif change in ("slot", "team"):
        setattr(ctx, change, 4)
    elif change == "server":
        ctx.server = Mock(socket=SimpleNamespace(open=True, closed=False))
    else:
        view.refresh([])
    table.update_status(target.key, HintStatus.HINT_PRIORITY, session)
    ctx.update_hint.assert_not_called()


def test_group_hint_and_server_refresh_preserve_selection_and_filters(table, connected):
    ctx, view = connected
    view.refresh([hint(receiving_player=3), hint(location=201, status=HintStatus.HINT_AVOID)])
    table.selected = 0
    target = table.rows[0]
    assert table.can_edit(target)
    table.update_status(target.key, HintStatus.HINT_UNSPECIFIED, table.session)
    table.update_status(target.key, HintStatus.HINT_FOUND, table.session)
    ctx.update_hint.assert_not_called()
    table.update_status(target.key, HintStatus.HINT_PRIORITY, table.session)
    ctx.update_hint.assert_called_once_with(200, 2, HintStatus.HINT_PRIORITY)
    table.set_filter(4, "hide", "Found")
    table.sort_header(SimpleNamespace(x=890))
    view.refresh([hint(receiving_player=3, status=HintStatus.HINT_PRIORITY),
                  hint(location=201, status=HintStatus.HINT_AVOID)])
    assert table.visible_rows[table.selected].key == target.key
    assert table.visible_rows[table.selected].values[4] == "Priority"
    view.refresh([hint(receiving_player=3, found=True), hint(location=201, status=HintStatus.HINT_AVOID)])
    assert table.selected is None
    assert len(table.visible_rows) == 1
