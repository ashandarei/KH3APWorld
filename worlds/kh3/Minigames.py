"""One-time minigame ability and item prizes, appended after published AP IDs."""
import json

from .PackageResources import read_package_text

MINIGAME_ITEM_REWARDS = json.loads(read_package_text(
    "data/minigame_item_rewards.json", module_file=__file__))
MINIGAME_ITEM_STATE_FILE = "runtime_minigame_rewards.json"
# Network items use the three base medals, rather than exposing the native
# lottery's many stat/ability variants as indistinguishable AP item names.
MINIGAME_MEDAL_ITEMS = {
    "Junior Medal": "ACC_ITEM28",
    "Star Medal": "ACC_ITEM30",
    "Master Medal": "ACC_ITEM29",
}
MINIGAME_APPENDED_ITEMS = {
    **MINIGAME_MEDAL_ITEMS,
    "Wellspring Shard": "MAT_ITEM45",
    "Wellspring Stone": "MAT_ITEM46",
}


# Option -> native item-prize prefix and A-rank ability bonus. Treasure and
# flan rewards intentionally have no rank cap or cumulative rank expansion.
MINIGAME_RANK_OPTIONS = {
    "festival_dance_max_rank": ("RA_MINIGAME", "002"),
    "verum_rex_max_rank": ("TS_MINIGAME", "001"),
    "frozen_slider_max_rank": ("FZ_MINIGAME", "003"),
    "flash_tracer_course_a_max_rank": ("BX_MINIGAME_1", "005"),
    "flash_tracer_course_b_max_rank": ("BX_MINIGAME_2", "006"),
}


MINIGAME_POOL_OPTION_NAMES = tuple(
    option.removesuffix("_max_rank") + "_pool" for option in MINIGAME_RANK_OPTIONS
)

def minigame_pool_option(location: dict) -> str | None:
    if location.get("source_table") == "VBonus":
        return MINIGAME_POOL_OPTIONS.get(location.get("source_id"))
    if location.get("source_table") == "MinigameReward":
        source_id = str(location.get("source_id") or "")
        for option, (prefix, _) in MINIGAME_RANK_OPTIONS.items():
            if source_id.startswith(prefix + "_"):
                return option.removesuffix("_max_rank") + "_pool"
    return None


def minigame_reward_rank(location: dict, option: str) -> int | None:
    """D=1 through A=4; the native first-A item and ability are both A rank."""
    prefix, bonus = MINIGAME_RANK_OPTIONS[option]
    if (location.get("source_table"), location.get("source_id")) == ("VBonus", f"VBonus_Minigame{bonus}"):
        return 4
    if location.get("source_table") == "MinigameReward":
        return {
            f"{prefix}_Rank_D": 1,
            f"{prefix}_Rank_C": 2,
            f"{prefix}_Rank_B": 3,
            f"{prefix}_Rank_A": 4,
            f"{prefix}_Rank_A_1st": 4,
        }.get(location.get("source_id"))
    return None


def canonical_prize_token(token: str) -> str:
    if not token.startswith("ACC_ITEM"):
        return token
    number = int(token.removeprefix("ACC_ITEM"))
    if number in {28, 83, *range(85, 93)}:
        return "ACC_ITEM28"
    if number in {29, 81, 82, 84, 94, 95, 97, 99, 100, 101, 102, 103}:
        return "ACC_ITEM29"
    if number in {30, 93, 96, 98, *range(104, 111)}:
        return "ACC_ITEM30"
    raise ValueError(f"Unknown minigame medal: {token}")


def append_minigame_item_locations(data: dict) -> None:
    existing_items = {item["id"] for item in data["items"]}
    for name, token in MINIGAME_APPENDED_ITEMS.items():
        if token not in existing_items:
            data["items"].append({"id": token, "name": name, "important": False})
    names = {item["id"]: item["name"] for item in data["items"]}
    existing = {(row["source_table"], row["source_id"]) for row in data["locations"]}
    for reward in MINIGAME_ITEM_REWARDS:
        if ("MinigameReward", reward["source_id"]) in existing:
            continue
        token = canonical_prize_token(reward["tokens"][0])
        data["locations"].append({
            "name": reward["name"], "location_description": reward["name"],
            "type": "minigame_reward", "world": reward["world"],
            "source_table": "MinigameReward", "source_id": reward["source_id"],
            "vanilla_item": token, "vanilla_item_name": names[token],
            "minigame_reward_crc": reward["crc"],
            "minigame_reward_tokens": reward["tokens"],
        })


# source suffix, world, display name, native ability, required story completion
MINIGAME_REWARDS = (
    ("001", "Toy Box", "Verum Rex - A Rank", "FOCUS_ASPIR", "EVENT_KEYBLADE_003"),
    ("002", "Kingdom of Corona", "Festival Dance - A Rank", "UNISON_THUNDER", "EVENT_KEYBLADE_004"),
    ("003", "Arendelle", "Frozen Slider - A Rank", "FUSION_ROCKET", "EVENT_KEYBLADE_007"),
    ("004", "Arendelle", "Frozen Slider - All Treasures", "MASTER_DRAW", "EVENT_KEYBLADE_007"),
    ("005", "San Fransokyo", "Flash Tracer Course A - A Rank", "ATTRACTION_UP", "EVENT_KEYBLADE_009"),
    ("006", "San Fransokyo", "Flash Tracer Course B - A Rank", "MAGIC_TIME", "EVENT_KEYBLADE_009"),
    ("007", "Olympus", "Cherry Flan - High Score", "FORM_TIME", "EVENT_KEYBLADE_001"),
    ("008", "Toy Box", "Strawberry Flan - High Score", "ATTRACTION_TIME", "EVENT_KEYBLADE_003"),
    ("009", "Kingdom of Corona", "Orange Flan - High Score", "PRIZE_DRAW", "EVENT_KEYBLADE_004"),
    ("010", "Monstropolis", "Banana Flan - High Score", "MAGIC_TIME", "EVENT_KEYBLADE_005"),
    ("011", "Arendelle", "Grape Flan - High Score", "UNISON_BLIZZARD", "EVENT_KEYBLADE_007"),
    ("012", "The Caribbean", "Watermelon Flan - High Score", "FOCUS_ASPIR", "EVENT_KEYBLADE_008"),
    ("013", "San Fransokyo", "Honeydew Flan - High Score", "ATTRACTION_TIME", "EVENT_KEYBLADE_009"),
)
MINIGAME_POOL_OPTIONS = {
    **{f"VBonus_Minigame{bonus}": option.removesuffix("_max_rank") + "_pool"
       for option, (_, bonus) in MINIGAME_RANK_OPTIONS.items()},
    "VBonus_Minigame004": "frozen_slider_pool",
    **{f"VBonus_Minigame{suffix}": "flantastic_flans_pool"
       for suffix, *_ in MINIGAME_REWARDS if int(suffix) >= 7},
}


def append_minigame_locations(data: dict) -> None:
    names = {item["id"]: item["name"] for item in data["items"]}
    existing = {(row["source_table"], row["source_id"]) for row in data["locations"]}
    for suffix, world, description, ability, _ in MINIGAME_REWARDS:
        if ("VBonus", f"VBonus_Minigame{suffix}") in existing:
            continue
        fields = {
            "Sora_Bonus1": "ETresVictoryBonusKind::NONE",
            "Sora_Ability1": f"ETresAbilityKind::{ability}",
            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
            "Sora_Ability2": "ETresAbilityKind::NONE",
        }
        data["locations"].append({
            "name": f"{world} - {description}",
            "type": "vbonus", "world": world,
            "source_table": "VBonus", "source_id": f"VBonus_Minigame{suffix}",
            "vanilla_items": fields,
            "vanilla_item_names": {field: names.get(token, token) for field, token in fields.items()},
        })
