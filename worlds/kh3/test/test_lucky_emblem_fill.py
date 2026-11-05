"""Lucky Emblem Junk is a placement restriction, including foreign items."""
import pytest

from BaseClasses import Item, ItemClassification
from Fill import distribute_items_restrictive
from test.general import gen_steps, setup_multiworld
from worlds.AutoWorld import call_all

from .. import KH3World


def make_world(mode, frequency):
    return setup_multiworld([KH3World], seed=731, steps=gen_steps, options=[{
        "lucky_emblems_pool": mode, "lucky_emblem_limit": 90,
        "lucky_emblems_per_check": frequency,
    }])


@pytest.mark.parametrize("frequency", [0, 1, 3])
@pytest.mark.parametrize("mode", ["junk", "randomize"])
def test_lucky_emblem_item_rules(mode, frequency):
    multiworld = make_world(mode, frequency)
    world = multiworld.worlds[1]
    locations = [multiworld.get_location(row["name"], 1) for row in world.active_locations
                 if row["source_table"] == "LuckyMark"]
    assert locations
    for classification in (ItemClassification.filler, ItemClassification.trap,
                           ItemClassification.useful, ItemClassification.progression,
                           ItemClassification.progression_skip_balancing,
                           ItemClassification.progression | ItemClassification.trap,
                           ItemClassification.useful | ItemClassification.trap):
        for player in (1, 2):
            item = Item("Test reward", classification, 1, player)
            allowed = mode == "randomize" or classification in (
                ItemClassification.filler, ItemClassification.trap)
            assert all(location.item_rule(item) == allowed for location in locations)


@pytest.mark.parametrize("frequency", [0, 1, 3])
def test_full_fill_keeps_lucky_emblem_junk_and_remains_beatable(frequency):
    multiworld = make_world("junk", frequency)
    distribute_items_restrictive(multiworld)
    call_all(multiworld, "post_fill")
    call_all(multiworld, "finalize_multiworld")
    for row in multiworld.worlds[1].active_locations:
        if row["source_table"] == "LuckyMark":
            item = multiworld.get_location(row["name"], 1).item
            assert item is not None
            assert item.classification in (ItemClassification.filler, ItemClassification.trap)
    assert not multiworld.get_unfilled_locations()
    assert multiworld.can_beat_game()
    assert multiworld.fulfills_accessibility()
