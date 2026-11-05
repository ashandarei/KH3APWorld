from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from .PackageResources import read_package_text

SourceKey = tuple[str, str]
RequirementGroup = frozenset[str]


@dataclass(frozen=True)
class CountedRequirement:
    item_names: frozenset[str]
    count: int

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("KH3 counted logic requirements must require at least one item.")
        if self.count > len(self.item_names):
            raise ValueError(
                f"KH3 counted logic requirement asks for {self.count} of only {len(self.item_names)} items."
            )

    def __iter__(self):
        return iter(self.item_names)

    def __contains__(self, item_name: str) -> bool:
        return item_name in self.item_names


RequirementClause = RequirementGroup | CountedRequirement
RequirementRoute = tuple[RequirementClause, ...]
RequirementAlternatives = tuple[RequirementRoute, ...]


@dataclass(frozen=True)
class LocationDependency:
    source_keys: tuple[SourceKey, ...]
    require_all: bool

    def __post_init__(self) -> None:
        if not self.source_keys:
            raise ValueError("KH3 location dependencies must name at least one prerequisite location.")
        if len(set(self.source_keys)) != len(self.source_keys):
            raise ValueError("KH3 location dependencies cannot repeat a prerequisite location.")


def require_all_locations(*source_keys: SourceKey) -> LocationDependency:
    return LocationDependency(tuple(source_keys), require_all=True)


def require_any_location(*source_keys: SourceKey) -> LocationDependency:
    return LocationDependency(tuple(source_keys), require_all=False)


def inactive_location_dependency_records(active_locations, catalog, dependencies):
    """Keep disabled prerequisite checks as logic references, not AP rewards."""
    by_key = {(row["source_table"], row["source_id"]): row for row in catalog}
    active = {(row["source_table"], row["source_id"]) for row in active_locations}
    pending = list(active)
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        if key not in by_key:
            raise ValueError(f"Unknown KH3 location dependency source: {key!r}")
        visited.add(key)
        if key in dependencies:
            pending.extend(dependencies[key].source_keys)
    return [by_key[key] for key in sorted(visited - active)]


def validate_location_dependency_cycles(
    dependencies: dict[SourceKey, LocationDependency],
) -> None:
    visiting: list[SourceKey] = []
    visited: set[SourceKey] = set()

    def visit(source_key: SourceKey) -> None:
        if source_key in visited:
            return
        if source_key in visiting:
            cycle_start = visiting.index(source_key)
            cycle = visiting[cycle_start:] + [source_key]
            cycle_text = " -> ".join(f"{table}:{source_id}" for table, source_id in cycle)
            raise ValueError(f"Circular KH3 location dependency: {cycle_text}")

        visiting.append(source_key)
        dependency = dependencies.get(source_key)
        if dependency is not None:
            for prerequisite_key in dependency.source_keys:
                if prerequisite_key in dependencies:
                    visit(prerequisite_key)
        visiting.pop()
        visited.add(source_key)

    for source_key in dependencies:
        visit(source_key)


def unknown_location_dependency_source_keys(
    dependencies: dict[SourceKey, LocationDependency],
    valid_source_keys: Iterable[SourceKey],
) -> set[SourceKey]:
    valid = set(valid_source_keys)
    referenced = set(dependencies)
    referenced.update(
        source_key
        for dependency in dependencies.values()
        for source_key in dependency.source_keys
    )
    return referenced.difference(valid)

MAGIC_UNLOCK_ITEM = "Unlock: Magic"
LINKS_UNLOCK_ITEM = "Unlock: Links"

_EXPLICIT_LOGIC_REQUIREMENT_ITEM_ALIASES = {
    "Aero": "Magic: Aero",
    "Blizzard": "Magic: Blizzard",
    "Cure": "Magic: Cure",
    "Fire": "Magic: Fire",
    "Thunder": "Magic: Thunder",
    "Water": "Magic: Water",
    "Air Dash x1": "Ability: Air Slide",
    # CSV typo for one Air Dash.
    "Air Dash xYes": "Ability: Air Slide",
    "Air Slide": "Ability: Air Slide",
    "Attraction Flow": "Unlock: Attraction Flow",
    "Block": "Ability: Block",
    "Combo Master": "Ability: Combo Master",
    "Counter Kick": "Ability: Counter Kick",
    "Counter Slash": "Ability: Counter Slash",
    "Dodge Roll": "Ability: Dodge Roll",
    "Double Flight": "Ability: Doubleflight",
    "Doubleflight": "Ability: Doubleflight",
    "Glide": "Ability: Glide",
    "High Jump": "Ability: High Jump",
    "Links": LINKS_UNLOCK_ITEM,
    "Pole Spin": "Ability: Pole Spin",
    "Super Jump": "Ability: Superjump",
    "Superjump": "Ability: Superjump",
    "Wall Kick": "Ability: Wall Kick",
    "Wall Run": "Unlock: Wall Run",
}
LOGIC_REQUIREMENT_ITEM_NAMES = frozenset(_EXPLICIT_LOGIC_REQUIREMENT_ITEM_ALIASES.values()) | frozenset({
    "Magic: Aero",
    "Magic: Blizzard",
    "Magic: Cure",
    "Magic: Fire",
    "Magic: Thunder",
    "Magic: Water",
    MAGIC_UNLOCK_ITEM,
})

_ALIASABLE_ITEM_PREFIXES = frozenset({"Ability", "Magic", "Unlock"})
_CONTROLLER_UNLOCK_ITEM_NAMES = frozenset({
    "Unlock: Airstep",
    "Unlock: Attraction Flow",
    "Unlock: Magic",
    LINKS_UNLOCK_ITEM,
    "Unlock: Style Change",
    "Unlock: Wall Run",
})


def _load_aliasable_item_names() -> frozenset[str]:
    extracted_data = json.loads(
        read_package_text("data/extracted_locations.json", module_file=__file__)
    )
    extracted_item_names = {
        str(item.get("name", "")).strip()
        for item in extracted_data.get("items", [])
    }
    return frozenset(
        item_name
        for item_name in extracted_item_names | _CONTROLLER_UNLOCK_ITEM_NAMES
        if item_name.partition(": ")[0] in _ALIASABLE_ITEM_PREFIXES
        and item_name.partition(": ")[2]
    )


def _build_prefixless_item_aliases(item_names: Iterable[str]) -> dict[str, str]:
    candidates_by_alias: dict[str, set[str]] = {}
    alias_text_by_casefold: dict[str, str] = {}
    for item_name in item_names:
        _prefix, separator, alias = item_name.partition(": ")
        if not separator:
            continue
        casefolded_alias = alias.casefold()
        candidates_by_alias.setdefault(casefolded_alias, set()).add(item_name)
        alias_text_by_casefold.setdefault(casefolded_alias, alias)

    # A bare name is only safe when it identifies one item. Explicit aliases
    # below can still resolve intentional special cases such as CSV typos.
    return {
        alias_text_by_casefold[casefolded_alias]: next(iter(item_names))
        for casefolded_alias, item_names in candidates_by_alias.items()
        if len(item_names) == 1
    }


_ALIASABLE_ITEM_NAMES = _load_aliasable_item_names()
_PREFIXLESS_ITEM_ALIASES = _build_prefixless_item_aliases(
    _ALIASABLE_ITEM_NAMES
)
LOGIC_REQUIREMENT_ITEM_ALIASES = {
    **_PREFIXLESS_ITEM_ALIASES,
    **_EXPLICIT_LOGIC_REQUIREMENT_ITEM_ALIASES,
}
_CANONICAL_ITEM_NAMES_BY_CASEFOLD = {
    item_name.casefold(): item_name
    for item_name in _ALIASABLE_ITEM_NAMES
}
_ALIASES_BY_CASEFOLD = {
    token.casefold(): item_name
    for token, item_name in LOGIC_REQUIREMENT_ITEM_ALIASES.items()
}
_PREFIXLESS_ITEM_ALIASES_BY_CASEFOLD = {
    token.casefold(): item_name
    for token, item_name in _PREFIXLESS_ITEM_ALIASES.items()
}
_PREFIXED_ITEM_PATTERN = re.compile(
    r"^(?P<prefix>ability|magic|unlock)\s*:\s*(?P<name>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogicRule:
    name: str
    sources: tuple[SourceKey, ...]
    requires: str


def source_keys(source_table: str, source_ids: Iterable[str]) -> tuple[SourceKey, ...]:
    return tuple((source_table, source_id) for source_id in source_ids)


def treasures(source_table: str, source_ids: Iterable[str]) -> tuple[SourceKey, ...]:
    return source_keys(source_table, source_ids)


def vbonuses(source_ids: Iterable[str]) -> tuple[SourceKey, ...]:
    return source_keys("VBonus", source_ids)


def events(source_ids: Iterable[str]) -> tuple[SourceKey, ...]:
    return source_keys("Event", source_ids)


def logic_rule(name: str, sources: Iterable[SourceKey], requires: str) -> LogicRule:
    return LogicRule(name=name, sources=tuple(sources), requires=requires)


def normalize_requirement_token(token: str) -> str:
    token = token.strip()
    if not token:
        raise ValueError("Empty KH3 logic requirement token")

    explicit_alias = LOGIC_REQUIREMENT_ITEM_ALIASES.get(token)
    if explicit_alias is not None:
        return explicit_alias

    casefolded_token = token.casefold()
    alias = _ALIASES_BY_CASEFOLD.get(casefolded_token)
    if alias is not None:
        return alias

    canonical_name = _CANONICAL_ITEM_NAMES_BY_CASEFOLD.get(casefolded_token)
    if canonical_name is not None:
        return canonical_name

    prefixed_match = _PREFIXED_ITEM_PATTERN.fullmatch(token)
    if prefixed_match is not None:
        candidate = (
            f"{prefixed_match.group('prefix').capitalize()}: "
            f"{prefixed_match.group('name').strip()}"
        )
        canonical_name = _CANONICAL_ITEM_NAMES_BY_CASEFOLD.get(candidate.casefold())
        if canonical_name is not None:
            return canonical_name

    return _PREFIXLESS_ITEM_ALIASES_BY_CASEFOLD.get(casefolded_token, token)


_ANY_OF_PATTERN = re.compile(r"^anyof(?P<count>\d+)\((?P<choices>.*)\)$", re.IGNORECASE)


def requirement_clause_item_names(requirement: RequirementClause) -> frozenset[str]:
    if isinstance(requirement, CountedRequirement):
        return requirement.item_names
    return requirement


def _logic_requirement_group_for_token(token: str, route_tokens: tuple[str, ...]) -> RequirementClause:
    any_of_match = _ANY_OF_PATTERN.fullmatch(token.strip())
    if any_of_match is not None:
        item_names = frozenset(
            normalize_requirement_token(choice)
            for choice in re.split(r"\s*[/,]\s*", any_of_match.group("choices"))
            if choice.strip()
        )
        return CountedRequirement(item_names, int(any_of_match.group("count")))

    if "/" in token:
        return frozenset(
            normalize_requirement_token(choice)
            for choice in token.split("/")
            if choice.strip()
        )

    item_name = normalize_requirement_token(token)
    if token.strip().casefold() == "high jump" and not any(
        route_token.strip().casefold() in {"double flight", "doubleflight"}
        for route_token in route_tokens
    ):
        return frozenset({item_name, normalize_requirement_token("Double Flight")})
    return frozenset({item_name})


def parse_requirement_summary(summary: str) -> RequirementAlternatives:
    routes: list[RequirementRoute] = []
    for raw_route in summary.split(" OR "):
        route_tokens = tuple(token.strip() for token in raw_route.split(" + ") if token.strip())
        if not route_tokens:
            continue
        routes.append(tuple(
            _logic_requirement_group_for_token(token, route_tokens)
            for token in route_tokens
        ))
    return add_links_unlock_to_link_routes(add_magic_unlock_to_magic_routes(tuple(routes)))


def route_requires_magic(route: RequirementRoute) -> bool:
    return any(
        item_name.startswith("Magic: ")
        for requirement in route
        for item_name in requirement_clause_item_names(requirement)
    )


def route_requires_magic_unlock(route: RequirementRoute) -> bool:
    return any(
        MAGIC_UNLOCK_ITEM in requirement_clause_item_names(requirement)
        for requirement in route
    )


def add_magic_unlock_to_magic_routes(routes: RequirementAlternatives) -> RequirementAlternatives:
    return tuple(
        route + (frozenset({MAGIC_UNLOCK_ITEM}),)
        if route_requires_magic(route) and not route_requires_magic_unlock(route)
        else route
        for route in routes
    )


def add_links_unlock_to_link_routes(routes: RequirementAlternatives) -> RequirementAlternatives:
    return tuple(
        route + (frozenset({LINKS_UNLOCK_ITEM}),)
        if any(
            item_name.endswith("Heartbinder")
            for requirement in route
            for item_name in requirement_clause_item_names(requirement)
        ) and not any(LINKS_UNLOCK_ITEM in requirement_clause_item_names(requirement) for requirement in route)
        else route
        for route in routes
    )


def compile_logic_rules(rules: Iterable[LogicRule]) -> dict[SourceKey, RequirementAlternatives]:
    requirements: dict[SourceKey, RequirementAlternatives] = {}
    owners: dict[SourceKey, str] = {}

    for rule in rules:
        routes = parse_requirement_summary(rule.requires)
        if not routes:
            raise ValueError(f"KH3 logic rule {rule.name!r} has no requirements.")
        for source_key in rule.sources:
            existing_owner = owners.get(source_key)
            if existing_owner is not None:
                raise ValueError(
                    "Duplicate KH3 logic rule source "
                    f"{source_key[0]}:{source_key[1]} in {rule.name!r}; "
                    f"already owned by {existing_owner!r}."
                )
            owners[source_key] = rule.name
            requirements[source_key] = routes

    return requirements


def requirement_item_names(requirement_maps: Iterable[dict[SourceKey, RequirementAlternatives]]) -> frozenset[str]:
    return frozenset(
        item_name
        for requirement_map in requirement_maps
        for routes in requirement_map.values()
        for route in routes
        for requirement in route
        for item_name in requirement_clause_item_names(requirement)
    )


def unknown_requirement_item_names(
    requirement_maps: Iterable[dict[SourceKey, RequirementAlternatives]],
    valid_item_names: Iterable[str],
) -> set[str]:
    valid = set(valid_item_names)
    return {
        item_name
        for item_name in requirement_item_names(requirement_maps)
        if item_name not in valid
    }


def unknown_requirement_source_keys(
    requirement_maps: Iterable[dict[SourceKey, RequirementAlternatives]],
    valid_source_keys: Iterable[SourceKey],
) -> set[SourceKey]:
    valid = set(valid_source_keys)
    return {
        source_key
        for requirement_map in requirement_maps
        for source_key in requirement_map
        if source_key not in valid
    }
