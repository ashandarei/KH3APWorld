"""Track optional Classic Kingdom receipts without adding progression inventory."""

from collections import Counter
from dataclasses import dataclass

from rule_builder.rules import Rule
from worlds.AutoWorld import LogicMixin


class KH3ClassicKingdomState(LogicMixin):
    def init_mixin(self, multiworld):
        self._kh3_useful_classic_games = {}

    def copy_mixin(self, other):
        other._kh3_useful_classic_games = {
            player: counts.copy() for player, counts in self._kh3_useful_classic_games.items()
        }
        return other


def record_useful_game(state, player, name, remove=False):
    counts = state._kh3_useful_classic_games.setdefault(player, Counter())
    if remove:
        if not counts[name]:
            return False
        counts[name] -= 1
        if not counts[name]:
            del counts[name]
    else:
        counts[name] += 1
    totals = getattr(state, "_kh3_reachable_counts", None)
    if totals is not None:
        totals.pop(player, None)
    return True


@dataclass(init=False)
class HasClassicKingdomGames(Rule, game="Kingdom Hearts III"):
    """The optional aggregate still needs every game, including useful receipts."""

    item_names: tuple[str, ...]

    def __init__(self, *names, item_names=(), **kwargs):
        super().__init__(**kwargs)
        self.item_names = tuple(sorted(set(names or item_names)))

    def _instantiate(self, world):
        return self.Resolved(self.item_names, player=world.player,
                             caching_enabled=getattr(world, "rule_caching_enabled", False))

    class Resolved(Rule.Resolved):
        item_names: tuple[str, ...]
        skip_cache = True

        def _evaluate(self, state):
            optional = state._kh3_useful_classic_games.get(self.player, {})
            progression = state.prog_items[self.player]
            return all(progression[name] or optional.get(name, 0) for name in self.item_names)

        def item_dependencies(self):
            return {name: {id(self)} for name in self.item_names}

        def __str__(self):
            return "All Classic Kingdom game unlocks"

        def explain_json(self, state=None):
            return [{"type": "text", "text": str(self)}]
