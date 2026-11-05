"""Rule Builder composition for KH3's data-driven requirements and milestones."""

from contextlib import contextmanager
from dataclasses import dataclass, field

from rule_builder.options import OptionFilter
from rule_builder.rules import And, Has, HasAny, HasFromListUnique, NestedRule, Or, True_

from .CharacterUnlocks import CHARACTER_MOVEMENT_ITEM_NAMES, CHARACTER_UNLOCK_ITEM_NAMES
from .Logic import CountedRequirement, requirement_clause_item_names


@contextmanager
def cache_reachable_rule_construction(world):
    """Reuse milestone components for one player and one stable graph layout."""
    previous = getattr(world, "_kh3_reachable_rule_build_cache", None)
    world._kh3_reachable_rule_build_cache = {}
    try:
        yield
    finally:
        if previous is None:
            del world._kh3_reachable_rule_build_cache
        else:
            world._kh3_reachable_rule_build_cache = previous


@dataclass
class _ResolvedReachableSources:
    # Retain the original rules so their identity keys cannot be reused.
    source_rules: tuple
    children: tuple
    region_names: frozenset[str]
    thresholds: dict = field(default_factory=dict)


def requirement_rule(requirement_routes, starting_item_names=frozenset()):
    """Compile alternative routes, counting each owned or starting item once."""
    routes = []
    for route in requirement_routes:
        clauses = []
        for requirement in route:
            names = frozenset(requirement_clause_item_names(requirement))
            count = requirement.count if isinstance(requirement, CountedRequirement) else 1
            count -= len(names & starting_item_names)
            names -= starting_item_names
            if count <= 0:
                clauses.append(True_())
            elif names & CHARACTER_MOVEMENT_ITEM_NAMES:
                # Count movement capabilities.
                ally = HasAny(*CHARACTER_UNLOCK_ITEM_NAMES)
                clauses.append(CountRules(*(
                    Has(name) | ally if name in CHARACTER_MOVEMENT_ITEM_NAMES else Has(name)
                    for name in sorted(names)
                ), count=count, label="required abilities"))
            else:
                clauses.append(HasFromListUnique(*names, count=count))
        # Empty routes are satisfied; Rule Builder's bare And() resolves false.
        routes.append(And(True_(), *clauses))
    return Or(*routes)


@dataclass(init=False)
class CountRules(NestedRule, game="Kingdom Hearts III"):
    """Count satisfied children without deduplicating spots with identical logic."""

    count: int
    label: str

    def __init__(self, *children, count, label, options=(), filtered_resolution=False):
        super().__init__(*children, options=options, filtered_resolution=filtered_resolution)
        self.count = count
        self.label = label

    def _instantiate(self, world):
        return self.Resolved(
            tuple(child.resolve(world) for child in self.children),
            count=self.count,
            label=self.label,
            player=world.player,
            caching_enabled=getattr(world, "rule_caching_enabled", False),
        )

    def to_dict(self):
        data = super().to_dict()
        data.update(count=self.count, label=self.label)
        return data

    @classmethod
    def from_dict(cls, data, world_cls):
        return cls(
            *(world_cls.rule_from_dict(child) for child in data.get("children", ())),
            count=data["count"],
            label=data["label"],
            options=OptionFilter.multiple_from_dict(data.get("options", ())),
            filtered_resolution=data.get("filtered_resolution", False),
        )

    class Resolved(NestedRule.Resolved):
        count: int
        label: str

        def __post_init__(self):
            super().__post_init__()
            # Resolved children are interned by Rule Builder. Keep the original
            # children for serialization/dependencies, but evaluate identical
            # requirements once per call and retain their spot multiplicity.
            # No result survives a call: inventory and reachability can change.
            groups = {}
            for index, child in enumerate(self.children):
                key = (id(child), index if child.force_recalculate else None)
                if key in groups:
                    groups[key][1] += 1
                else:
                    groups[key] = [child, 1]
            object.__setattr__(self, "_weighted_children", tuple(
                (child, weight) for child, weight in groups.values()
            ))

        def _evaluate(self, state):
            needed = self.count
            if needed <= 0:
                return True
            remaining = len(self.children)
            for child, weight in self._weighted_children:
                if needed > remaining:
                    return False
                remaining -= weight
                if child(state):
                    needed -= weight
                    if needed <= 0:
                        return True
            return False

        def explain_json(self, state=None):
            if state is None:
                return [{"type": "text", "text": str(self)}]
            found = sum(child(state) for child in self.children)
            return [
                {"type": "text", "text": "At least "},
                {"type": "color", "color": "green" if found >= self.count else "salmon",
                 "text": f"{found}/{self.count}"},
                {"type": "text", "text": f" {self.label}"},
            ]

        def explain_str(self, state=None):
            return "".join(part["text"] for part in self.explain_json(state))

        def __str__(self):
            return f"At least {self.count} {self.label}"


@dataclass(init=False)
class CountReachableRules(CountRules, game="Kingdom Hearts III"):
    """Reuse XP/emblem totals across thresholds for one inventory/graph state."""

    def _instantiate(self, world):
        cache = getattr(world, "_kh3_reachable_rule_build_cache", None)
        key = tuple(map(id, self.children)) if cache is not None else None
        sources = cache.get(key) if cache is not None else None
        if sources is None:
            children = tuple(child.resolve(world) for child in self.children)
            sources = _ResolvedReachableSources(
                self.children, children,
                frozenset(name for child in children for name in child.region_dependencies()),
            )
            if cache is not None:
                cache[key] = sources
        threshold = (self.count, self.label)
        resolved = sources.thresholds.get(threshold)
        if resolved is None:
            resolved = self.Resolved(
                sources.children, count=self.count, label=self.label, player=world.player,
                caching_enabled=getattr(world, "rule_caching_enabled", False),
                source_region_names=sources.region_names,
            )
            sources.thresholds[threshold] = resolved
        return resolved

    class Resolved(CountRules.Resolved):
        source_region_names: frozenset[str] = field(default_factory=frozenset, repr=False, kw_only=True)

        def __post_init__(self):
            super().__post_init__()
            object.__setattr__(self, "_total_key", tuple(
                (id(child), weight) for child, weight in self._weighted_children
            ))

        def _evaluate(self, state):
            if self.force_recalculate:
                return super()._evaluate(state)
            if self.count <= 0:
                return True
            if self.count > len(self.children):
                return False
            totals = getattr(state, "_kh3_reachable_counts", None)
            if totals is None:
                totals = state._kh3_reachable_counts = {}
            player_totals = totals.setdefault(self.player, {})
            reachable = state.reachable_regions[self.player]
            cached = player_totals.get(self._total_key)
            # Rule Builder interns resolved rules globally. Region objects must
            # live only on the state, so the rule cannot keep a MultiWorld alive.
            sources = (cached[0] if cached is not None else frozenset(
                state.multiworld.get_region(name, self.player) for name in self.source_region_names
            ))
            # Only source-world changes affect these counts. Entering an
            # unrelated chest/encounter region must not invalidate the total.
            source_regions = sources.intersection(reachable)
            if cached is not None and cached[1] == source_regions:
                return cached[2] >= self.count
            found = sum(weight for child, weight in self._weighted_children if child(state))
            # An entrance may ask for a total while AP is still expanding its
            # regions. Never cache a count assembled across different BFS stages.
            stable = source_regions == sources.intersection(state.reachable_regions[self.player])
            player_totals[self._total_key] = (sources, source_regions if stable else None, found)
            return found >= self.count
