from __future__ import annotations

from dataclasses import asdict, dataclass

from BaseClasses import ItemClassification

MOOGLE_SHOP_FUNDING_COVERAGE_PERCENT = 60
MOOGLE_SHOP_MAX_ITEM_PRICE = 10_000
MOOGLE_SHOP_TRAP_PRICE_NUMERATOR = 1
MOOGLE_SHOP_TRAP_PRICE_DENOMINATOR = 2
MOOGLE_SHOP_USEFUL_PRICE_MULTIPLIER = 5
MOOGLE_SHOP_PROGRESSION_PRICE_MULTIPLIER = 10


def _ceil_div(numerator: int, denominator: int) -> int:
    if numerator <= 0:
        return 0
    return (numerator + denominator - 1) // denominator


@dataclass(frozen=True)
class MoogleShopPriceTiers:
    filler: int
    useful: int
    progression: int
    trap: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class MoogleShopFundingPlan:
    shop_check_count: int
    requested_base_price: int
    effective_base_price: int
    price_tiers: MoogleShopPriceTiers
    funding_coverage_percent: int
    maximum_shop_cost: int
    funding_target: int
    existing_pouch_munny: int
    available_filler_slots: int
    large_pouch_count: int
    large_pouch_munny: int
    planned_pouch_munny: int
    price_was_reduced: bool

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["price_tiers"] = self.price_tiers.as_dict()
        return result


def build_moogle_shop_price_tiers(base_price: int) -> MoogleShopPriceTiers:
    base_price = min(MOOGLE_SHOP_MAX_ITEM_PRICE, max(0, int(base_price)))
    trap_price = (
        base_price * MOOGLE_SHOP_TRAP_PRICE_NUMERATOR
        // MOOGLE_SHOP_TRAP_PRICE_DENOMINATOR
    )
    if base_price > 0:
        trap_price = max(1, trap_price)
    return MoogleShopPriceTiers(
        filler=base_price,
        useful=min(
            MOOGLE_SHOP_MAX_ITEM_PRICE,
            base_price * MOOGLE_SHOP_USEFUL_PRICE_MULTIPLIER,
        ),
        progression=min(
            MOOGLE_SHOP_MAX_ITEM_PRICE,
            base_price * MOOGLE_SHOP_PROGRESSION_PRICE_MULTIPLIER,
        ),
        trap=trap_price,
    )


def moogle_shop_price_for_classification(
    classification: int | ItemClassification,
    price_tiers: MoogleShopPriceTiers,
) -> int:
    flags = int(classification)
    if flags & int(ItemClassification.progression):
        return price_tiers.progression
    if flags & int(ItemClassification.useful):
        return price_tiers.useful
    if flags & int(ItemClassification.trap):
        return price_tiers.trap
    return price_tiers.filler


def _funding_target(
    shop_check_count: int,
    price_tiers: MoogleShopPriceTiers,
    funding_coverage_percent: int,
) -> tuple[int, int]:
    maximum_shop_cost = shop_check_count * price_tiers.progression
    target = _ceil_div(
        maximum_shop_cost * funding_coverage_percent,
        100,
    )
    return maximum_shop_cost, target


def build_moogle_shop_funding_plan(
    *,
    shop_check_count: int,
    requested_base_price: int,
    existing_pouch_munny: int,
    available_filler_slots: int,
    large_pouch_munny: int,
    funding_coverage_percent: int = MOOGLE_SHOP_FUNDING_COVERAGE_PERCENT,
) -> MoogleShopFundingPlan:
    """Plan pouch replacements against a conservative all-progression shop.

    Shop rewards are assigned by the multiworld fill after KH3 constructs its
    item pool, so their final classifications are not available here. Planning
    against the progression tier guarantees the configured coverage for every
    possible post-fill mix. If the pool lacks enough replaceable filler, the
    effective base price is reduced until the available pouch capacity covers
    the same percentage.
    """

    shop_check_count = max(0, int(shop_check_count))
    requested_base_price = min(
        MOOGLE_SHOP_MAX_ITEM_PRICE,
        max(0, int(requested_base_price)),
    )
    existing_pouch_munny = max(0, int(existing_pouch_munny))
    available_filler_slots = max(0, int(available_filler_slots))
    large_pouch_munny = max(1, int(large_pouch_munny))
    funding_coverage_percent = min(100, max(0, int(funding_coverage_percent)))

    funding_capacity = existing_pouch_munny + available_filler_slots * large_pouch_munny
    effective_base_price = requested_base_price
    price_tiers = build_moogle_shop_price_tiers(effective_base_price)
    maximum_shop_cost, funding_target = _funding_target(
        shop_check_count,
        price_tiers,
        funding_coverage_percent,
    )

    if funding_target > funding_capacity:
        low = 0
        high = requested_base_price
        while low < high:
            candidate = (low + high + 1) // 2
            candidate_tiers = build_moogle_shop_price_tiers(candidate)
            _, candidate_target = _funding_target(
                shop_check_count,
                candidate_tiers,
                funding_coverage_percent,
            )
            if candidate_target <= funding_capacity:
                low = candidate
            else:
                high = candidate - 1
        effective_base_price = low
        price_tiers = build_moogle_shop_price_tiers(effective_base_price)
        maximum_shop_cost, funding_target = _funding_target(
            shop_check_count,
            price_tiers,
            funding_coverage_percent,
        )

    funding_deficit = max(0, funding_target - existing_pouch_munny)
    large_pouch_count = min(
        available_filler_slots,
        _ceil_div(funding_deficit, large_pouch_munny),
    )
    planned_pouch_munny = existing_pouch_munny + large_pouch_count * large_pouch_munny

    return MoogleShopFundingPlan(
        shop_check_count=shop_check_count,
        requested_base_price=requested_base_price,
        effective_base_price=effective_base_price,
        price_tiers=price_tiers,
        funding_coverage_percent=funding_coverage_percent,
        maximum_shop_cost=maximum_shop_cost,
        funding_target=funding_target,
        existing_pouch_munny=existing_pouch_munny,
        available_filler_slots=available_filler_slots,
        large_pouch_count=large_pouch_count,
        large_pouch_munny=large_pouch_munny,
        planned_pouch_munny=planned_pouch_munny,
        price_was_reduced=effective_base_price < requested_base_price,
    )
