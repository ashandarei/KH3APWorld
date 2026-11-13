# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Aesais and contributors.
# See LICENSE and COPYRIGHT.txt for scope and separate component licenses.

import hashlib
import json
import os
import zipfile
from collections import Counter
from threading import Lock
from typing import Any, ClassVar, Dict, Type

from BaseClasses import DEFAULT_COLLECTION_RULE, CollectionState, Item, ItemClassification, Tutorial
from rule_builder.rules import Has
from worlds.AutoWorld import WebWorld, World
from worlds.Files import APPlayerContainer
from worlds.LauncherComponents import (
    Component,
    SuffixIdentifier,
    components,
    icon_paths,
)
from worlds.LauncherComponents import (
    Type as ComponentType,
)
from worlds.LauncherComponents import (
    launch as launch_component,
)

from .Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION, KH3_WORLD_VERSION, build_bridge_slot_data
from .CharacterUnlocks import CHARACTER_UNLOCK_ITEM_ALIASES
from .ClassicKingdomLogic import record_useful_game
from .Data import (
    TRAP_ITEM_NAMES,
    CHEST_LOGIC_REQUIREMENTS,
    CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
    CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES,
    CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID,
    CONTROLLER_UNLOCK_ITEM_NAMES,
    CONTROLLER_UNLOCK_ITEM_NAMES_BY_OPTION,
    DEFAULT_BASE_ABILITY_ITEM_NAMES,
    EVENT_LOGIC_REQUIREMENTS,
    FILLER_ITEM_NAMES,
    FIXED_SORA_CHRINIT_ABILITY_ITEM_NAMES,
    HEART_PIECE_ITEM_NAME,
    INGREDIENT_LOGIC_REQUIREMENTS,
    KEYBLADE_DISPLAY_NAMES,
    KEYBLADE_ENHANCEMENT_GROUPS,
    KEYBLADE_STYLE_ITEM_NAMES,
    LARGE_MUNNY_POUCH_ITEM_NAME,
    LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
    LOCATION_LOGIC_DEPENDENCIES,
    LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD,
    MUNNY_POUCH_ITEM_NAMES,
    MUNNY_POUCH_MUNNY_AMOUNT_BY_NAME,
    PARTY_MEMBER_UNLOCK_ITEM_NAMES,
    PORTAL_KEYBLADE_ITEM_NAMES,
    PORTAL_KEYBLADE_REGION_LOCKS,
    PORTAL_PARTY_ITEM_NAMES,
    PORTAL_PARTY_REGION_LOCKS,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES,
    RANDOMIZED_ITEM_POOL_EXCLUSIONS,
    RANDOMIZED_KEYBLADE_ITEM_NAMES,
    SECRET_REPORT_ITEM_NAMES,
    STARTING_PORTAL_KEYBLADE_ITEM_NAMES,
    STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES,
    STYLE_CHANGE_UNLOCK_ITEM,
    SPELLWEAVER_UNLOCK_ITEM,
    ULTIMA_WEAPON_ITEM_NAME,
    ULTIMA_WEAPON_SHARD_ITEM_NAME,
    VICTORY_BONUS_LOGIC_REQUIREMENTS,
    build_item_counts,
    filter_locations_for_options,
    goal_required_item_counts,
    goal_required_item_names,
    is_always_on_location,
    load_extracted_data,
    local_seeded_reward_requires_bridge_delivery,
    normalize_locations,
    resolve_item_runtime_token,
    uses_replace_location_pool,
    validate_locations,
)
from .Data import (
    PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES as PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES,
)
from .Data import (
    PROOF_ITEMS as PROOF_ITEMS,
)
from .ExtraVictoryBonuses import (
    extra_vbonus_locations,
    set_extra_vbonus_rules,
)
from .Items import (
    ABILITY_GROUP_EXCLUSIONS, ABILITY_ITEM_NAMES, CONSUMABLE_ITEM_NAMES, MAGIC_ITEM_NAMES,
    HEARTBINDER_ITEM_NAMES, KH3Item, classify_item, item_name_to_id,
)
from .Locations import (
    COMPLETION_LOCATION_NAME,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
    ROCK_TITAN_VBONUS_BRIDGE_KEY,
    ROCK_TITAN_VBONUS_SOURCE_KEY,
    location_name_groups,
    location_name_to_id,
    location_id_to_alias,
)
from .Locations import (
    KH3Location as KH3Location,
)
from .Logic import LOGIC_REQUIREMENT_ITEM_ALIASES, inactive_location_dependency_records
from .Options import (
    KeybladeLevels,
    KH3Options,
    UltimaWeaponMode,
    build_randomizer_generation_config,
    effective_keyblade_levels,
    option_groups,
)
from .PackageResources import read_package_text
from .Regions import create_regions
from .Rules import set_rules
from .ShopEconomy import (
    MoogleShopFundingPlan,
    build_moogle_shop_funding_plan,
    build_moogle_shop_price_tiers,
    moogle_shop_price_for_classification,
)
from .SlotData import decode_slot_data, encode_slot_data
from .Space import ENDYMION
from .universal_tracker import (
    KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY,
    build_universal_tracker_slot_data,
    restore_universal_tracker_options,
)

CONTROLLER_UNLOCK_EXTRA_ITEM_POOL_ITEMS = CONTROLLER_UNLOCK_ITEM_NAMES
PROGRESSION_ITEM_FLAG = int(ItemClassification.progression)
KEYBLADE_STYLE_EXTRA_ITEM_POOL_ITEMS = KEYBLADE_STYLE_ITEM_NAMES
PARTY_MEMBER_UNLOCK_EXTRA_ITEM_POOL_ITEMS = PARTY_MEMBER_UNLOCK_ITEM_NAMES
# Standalone AP items replacing the retired Guardian/Warrior/Mystic level-up
# route tables.  These are global pool entries, not rewards attached to a
# particular level or character-growth path.
RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS = (
    "Ability: Aero Boost",
    "Ability: Air Combo Boost",
    "Ability: Air Combo Plus",
    "Ability: Attraction Enhancer",
    "Ability: Attraction Extender",
    "Ability: Auto-Finish",
    "Ability: Blizzard Boost",
    "Ability: Block Replenisher",
    "Ability: Combo Boost",
    "Ability: Combo Plus",
    "Ability: Damage Control",
    "Ability: Fire Boost",
    "Ability: Fusion Spin",
    "Ability: Grand Magic Extender",
    "Ability: Leaf Bracer",
    "Ability: Link Extender",
    "Ability: Magic Combo Thrift",
    "Ability: Magic Galvanizer",
    "Ability: MP Haste",
    "Ability: Reprisal Boost",
    "Ability: Second Chance",
    "Ability: Thunder Boost",
    "Ability: Treasure Magnet",
    "Ability: Unison Fire",
    "Ability: Water Boost",
    "Ability: Withstand Combo",
)
# These registered bonuses have no vanilla location source. Add one of each
# as standalone AP rewards in every generated pool.
ADDITIONAL_STAT_BONUS_POOL_ITEMS = (
    "Bonus: HP +15",
    "Bonus: HP +30",
    "Bonus: MP +10",
    "Bonus: MP +20",
)
SECONDARY_VBONUS_ITEM_POOL_REWARDS = frozenset({
    "Ability: Aerial Dive",
    "Ability: Aerial Sweep",
    "Ability: Falling Slash",
    "Ability: Final Blow",
    "Ability: Flash Step",
    "Ability: Last Charge",
    "Ability: MP Safety",
    "Ability: Payback Strike",
    "Ability: Quick Slash",
    "Ability: Superjump",
    "Ability: Superslide",
    "Bonus: Accessory Slot +1",
    "Bonus: HP +10",
    "Bonus: MP +5",
    HEART_PIECE_ITEM_NAME,
    "Magic: Water",
})
YOZORA_REWARD_SOURCE_KEY = ("Event", "EVENT_YOZORA_001")
KH3_WORLD_LOAD_PATHS_ENV = "AP_WORLD_LOAD_PATHS"


def _kh3_world_load_paths_value() -> str:
    return json.dumps([os.path.abspath(os.path.dirname(__file__))])


def launch_client(*args: str) -> None:
    import subprocess

    import Utils

    # A spawned launcher child otherwise imports every installed world before
    # it reaches the KH3 client target. The child inherits this scoped value.
    previous_world_load_paths = os.environ.get(KH3_WORLD_LOAD_PATHS_ENV)
    os.environ[KH3_WORLD_LOAD_PATHS_ENV] = _kh3_world_load_paths_value()
    try:
        if Utils.is_kivy_running() and os.environ.get("APPIMAGE"):
            appimage = os.environ.get("ARGV0") or os.environ["APPIMAGE"]
            subprocess.Popen([appimage, "KH3 Client", *args])
            return

        from .Client import launch

        launch_component(launch, name="KH3Client", args=args)
    finally:
        if previous_world_load_paths is None:
            os.environ.pop(KH3_WORLD_LOAD_PATHS_ENV, None)
        else:
            os.environ[KH3_WORLD_LOAD_PATHS_ENV] = previous_world_load_paths


def generate_player_paks(*args: str) -> None:
    from pathlib import Path

    import Utils

    from .PakGenerator import KH3PakGenerationError, generate_kh3_pak_from_container, generate_kh3_paks_from_ap_zip

    ap_zip = args[0] if args else Utils.open_filename(
        "Select KH3 AP seed or Archipelago AP zip",
        (("KH3 AP seed or Archipelago generated zip", ("*.apkh3", "*.zip")),),
    )
    if not ap_zip:
        return

    source_path = Path(ap_zip).resolve()
    output_dir = source_path.parent / "kh3-player-paks"
    try:
        if source_path.suffix.casefold() == KH3Container.patch_file_ending:
            results = [generate_kh3_pak_from_container(source_path, output_dir)]
        else:
            results = generate_kh3_paks_from_ap_zip(source_path, output_dir)
    except KH3PakGenerationError as exc:
        Utils.messagebox("KH3 Pak Generation Failed", str(exc), error=True)
        return

    lines = [f"{result.slot_name}: {result.pak_path.name}" for result in results]
    Utils.messagebox(
        "KH3 Pak Generation Complete",
        "Generated KH3 pak file(s):\n\n" + "\n".join(lines) + f"\n\nOutput folder:\n{output_dir}",
    )
    Utils.open_file(output_dir)


def launch_yaml_creator(*args: str) -> None:
    """Use a separate Tk process so the creator cannot block a connected client."""
    import subprocess
    import Utils
    from worlds.LauncherComponents import launch_subprocess
    from .gui.yaml_creator import launch

    previous = os.environ.get(KH3_WORLD_LOAD_PATHS_ENV)
    os.environ[KH3_WORLD_LOAD_PATHS_ENV] = _kh3_world_load_paths_value()
    try:
        if Utils.is_kivy_running() and os.environ.get("APPIMAGE"):
            subprocess.Popen([os.environ.get("ARGV0") or os.environ["APPIMAGE"], "KH3 YAML Creator", *args])
        else:
            launch_subprocess(launch, name="KH3YamlCreator", args=args)
    finally:
        if previous is None:
            os.environ.pop(KH3_WORLD_LOAD_PATHS_ENV, None)
        else:
            os.environ[KH3_WORLD_LOAD_PATHS_ENV] = previous


KH3_CONTAINER_PAYLOAD = "kh3_seed.json"


class KH3Container(APPlayerContainer):
    """
    Player-scoped KH3 seed data for AP rooms.

    This container lets a KH3 player build their pak locally from their own
    room download instead of requiring the full generated AP zip.
    """

    game: str = "Kingdom Hearts III"
    patch_file_ending: str = ".apkh3"

    def __init__(self, *args: Any, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.data = data or {}
        super().__init__(*args, **kwargs)

    def write_contents(self, opened_zipfile: zipfile.ZipFile) -> None:
        super().write_contents(opened_zipfile)
        opened_zipfile.writestr(KH3_CONTAINER_PAYLOAD, json.dumps(self.data, indent=2, sort_keys=True))


KH3_HOST_ONLY = json.loads(read_package_text("archipelago.json", module_file=__file__)).get("package_variant") == "host"
if not KH3_HOST_ONLY:
    icon_paths["kh3"] = f"ap:{__name__}/gui/assets/kh3-icon.png"
    components.append(Component("KH3 Client", func=launch_client, component_type=ComponentType.CLIENT, icon="kh3"))
    components.append(Component("KH3 YAML Creator", func=launch_yaml_creator, component_type=ComponentType.TOOL,
                                icon="kh3", description="Create, import and export KH3 player options with a native editor."))
    components.append(Component("KH3 Pak Generator", func=generate_player_paks, component_type=ComponentType.TOOL,
                                file_identifier=SuffixIdentifier(KH3Container.patch_file_ending),
                                description="Generate KH3 player .pak files from a KH3 seed file or generated AP zip."))


class KingdomHearts3Web(WebWorld):
    tutorials = [Tutorial(
        "Hosting Guide",
        "Generate and host Kingdom Hearts III rooms with the lightweight host APWorld.",
        "English", "hosting_en.md", "hosting/en", ["Codex", "Aesais"],
    )] if KH3_HOST_ONLY else [
        Tutorial(
            "Quick Setup Guide",
            "The normal install, patch, connection, and Save Manager workflow for Kingdom Hearts III.",
            "English",
            "setup_en.md",
            "setup/en",
            ["Codex", "Aesais"],
        ),
    ]
    option_groups = option_groups


class KH3World(World):
    """
    Kingdom Hearts III support using a Garden of Assemblage hub, generated KH3
    pak artifacts, and a KH3 runtime bridge for check reporting and item
    delivery.
    """

    game = "Kingdom Hearts III"
    web = KingdomHearts3Web()
    required_client_version = (0, 6, 6)
    ut_can_gen_without_yaml = True

    options_dataclass: ClassVar[Type[KH3Options]] = KH3Options
    options: KH3Options

    item_name_to_id = item_name_to_id
    item_name_aliases = {
        **LOGIC_REQUIREMENT_ITEM_ALIASES,
        **CHARACTER_UNLOCK_ITEM_ALIASES,
        **{
            item_name.removeprefix("Party Member: "): item_name
            for item_name in PARTY_MEMBER_UNLOCK_ITEM_NAMES
        },
    }
    item_name_groups = {
        "Magic": MAGIC_ITEM_NAMES,
        "Heartbinders": HEARTBINDER_ITEM_NAMES,
        "Reports": frozenset(SECRET_REPORT_ITEM_NAMES),
        "Consumables": CONSUMABLE_ITEM_NAMES,
        "Abilities": ABILITY_ITEM_NAMES - ABILITY_GROUP_EXCLUSIONS,
        "Controller Unlocks": CONTROLLER_UNLOCK_ITEM_NAMES,
        "Keyblade Styles": KEYBLADE_STYLE_ITEM_NAMES,
        "Party Member Unlocks": PARTY_MEMBER_UNLOCK_ITEM_NAMES,
        "Keyblade Upgrades": PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES,
        "Traps": TRAP_ITEM_NAMES,
    }
    location_name_to_id = location_name_to_id
    location_name_groups = location_name_groups
    location_id_to_alias = location_id_to_alias

    extracted_data: dict
    active_locations: list[dict]
    logic_reference_locations: list[dict]
    starting_inventory_locations: list[dict]
    kh3_starting_inventory_items: list[str]
    extra_vbonus_locations: list[dict]
    _moogle_shop_funding_plan: MoogleShopFundingPlan

    @staticmethod
    def interpret_slot_data(slot_data: dict[str, Any]) -> dict[str, Any]:
        """Ask Universal Tracker to regenerate KH3 from the connected slot data."""
        return slot_data


    def _restore_universal_tracker_options(self) -> None:
        passthrough = getattr(self.multiworld, "re_gen_passthrough", None)
        if not isinstance(passthrough, dict) or self.game not in passthrough:
            return

        slot_data = passthrough[self.game]
        if not isinstance(slot_data, dict):
            raise ValueError("KH3 Universal Tracker passthrough data must be an object.")
        self._tracker_runtime_slot_data = decode_slot_data(slot_data)
        restore_universal_tracker_options(self.options, self._tracker_runtime_slot_data)

    def create_regions(self) -> None:
        create_regions(self.multiworld, self.player, self.active_locations, self.logic_reference_locations)
        # Shared access regions depend on these predicates. Build both during
        # the region stage, before AP records the finished graph's size.
        self._create_access_rules_and_regions()

    def generate_early(self) -> None:
        self._restore_universal_tracker_options()
        self.extracted_data = load_extracted_data()
        normalized_locations = normalize_locations(self.extracted_data)
        self.starting_inventory_locations = [
            location
            for location in normalized_locations
            if is_always_on_location(location)
        ]
        self.active_locations = filter_locations_for_options(normalized_locations, self.options)
        from .kh02.locations import kh02_locations
        kh02_checks = kh02_locations(
            chests=bool(self.options.kh02_enabled.value),
            bosses=bool(self.options.kh02_enabled.value),
            challenges=bool(self.options.kh02_enabled.value and self.options.kh02_challenges.value),
        )
        self.active_locations.extend(kh02_checks)
        from .Space import space_locations
        space_checks = space_locations(
            bool(self.options.include_space.value),
            include_grindy_space_missions=bool(self.options.include_grindy_space_missions.value),
            include_space_superbosses=bool(self.options.include_space_superbosses.value),
        )
        self.active_locations.extend(space_checks)
        from .MelodyOfMemory import music_chest_bonus_locations, select_music_chests
        tracker_slot_data = getattr(self, "_tracker_runtime_slot_data", None)
        mom_locations = select_music_chests(
            self.random, self.active_locations,
            int(self.options.music_chest_count.value),
            int(self.options.music_chest_vanilla_conversion.value),
            int(self.options.melody_of_memory_difficulty.value), tracker_slot_data,
            whitelist=self.options.melody_of_memory_song_whitelist.value,
            blacklist=self.options.melody_of_memory_song_blacklist.value,
        )
        self.active_locations.extend(mom_locations)
        from .KeybladeChests import assign_keyblade_chests
        assign_keyblade_chests(self.random, self.active_locations, self.options, tracker_slot_data)
        self.keyblade_chest_required_items = frozenset(
            row["keyblade_chest_keyblade"] for row in self.active_locations
            if row.get("keyblade_chest_keyblade") and not row["keyblade_chest_native"]
        )
        bonus_locations = music_chest_bonus_locations(
            self.active_locations, tracker_slot_data,
        )
        self.active_locations.extend(bonus_locations)
        if int(self.options.ultima_weapon_mode.value) == UltimaWeaponMode.option_yozora:
            yozora_location = next(
                location
                for location in normalized_locations
                if (location["source_table"], location["source_id"]) == YOZORA_REWARD_SOURCE_KEY
            )
            if yozora_location not in self.active_locations:
                self.active_locations.append(yozora_location)
        validate_locations(self.active_locations)
        self.logic_reference_locations = inactive_location_dependency_records(
            self.active_locations, normalized_locations + mom_locations + bonus_locations + space_checks + kh02_checks, LOCATION_LOGIC_DEPENDENCIES)
        self.extra_vbonus_locations = extra_vbonus_locations(self.active_locations, tracker_slot_data)
        self.active_locations.extend(self.extra_vbonus_locations)
        from .CharacterUnlocks import character_unlock_locations
        if self.options.ally_characters:
            self.active_locations.extend(character_unlock_locations(
                kh02_enabled=bool(self.options.kh02_enabled.value)))
        validate_locations(self.active_locations)

    def create_items(self) -> None:
        from .Ingredients import INGREDIENT_FIXED_REWARDS

        source_locations = []
        for location in self.active_locations + self.starting_inventory_locations:
            reward = (location["fixed_character_reward"] if "fixed_character_reward" in location else
                      INGREDIENT_FIXED_REWARDS.get(location["source_id"])
                      if location["source_table"] == "IngredientSpot" else None)
            if reward is not None:
                self.multiworld.get_location(location["name"], self.player).place_locked_item(
                    self.create_item(reward))
            else:
                source_locations.append(location)
        item_counts = build_item_counts([
            location
            for location in source_locations
            if location.get("type") != "level_up"
        ])
        filler_item_count = len(source_locations) - sum(item_counts.values())
        filler_item_count += sum(
            item_counts.pop(item_name, 0)
            for item_name in RANDOMIZED_ITEM_POOL_EXCLUSIONS
        )
        filler_item_count += sum(
            item_counts.pop(item_name, 0)
            for item_name in {*PROOF_ITEMS, HEART_PIECE_ITEM_NAME}
        )
        filler_item_count += sum(
            item_counts.pop(item_name, 0)
            for item_name in FIXED_SORA_CHRINIT_ABILITY_ITEM_NAMES
        )
        if int(self.options.ultima_weapon_mode.value) == UltimaWeaponMode.option_yozora:
            filler_item_count -= 1
            if filler_item_count < 0:
                raise RuntimeError("Unable to reserve Yozora's locked Ultima Weapon reward.")
        # Area maps are baseline starting items, so their rewards become filler.
        filler_item_count += sum(
            item_counts.pop(item_name)
            for item_name in tuple(item_counts)
            if item_name.startswith("Map: ")
        )
        item_pool = []
        for item_name, count in sorted(item_counts.items()):
            for _ in range(count):
                item_pool.append(self.create_item(item_name))
        item_pool.extend(self.create_filler() for _ in range(filler_item_count))
        self._ensure_randomized_keyblades_in_pool(item_pool)
        self._ensure_secret_reports_in_pool(item_pool)
        self._add_randomized_default_base_ability_items_to_pool(item_pool)
        self._add_retired_growth_path_ability_items_to_pool(item_pool)
        self._ensure_required_logic_items_in_pool(item_pool)
        self._add_progressive_keyblade_level_items(item_pool)
        if self.options.portal_unlocks.value == 1:
            self._add_party_member_unlock_item_pool_items(item_pool)
        self._move_starting_portal_keyblade_from_pool(item_pool)
        self._move_starting_inventory_items_from_pool(item_pool)
        self._add_controller_unlock_item_pool_items(item_pool)
        self._add_keyblade_style_item_pool_items(item_pool)
        self._add_party_member_unlock_item_pool_items(item_pool)
        self._add_secondary_vbonus_item_pool_items(item_pool)
        self._ensure_required_goal_items_in_pool(item_pool)
        self._add_trap_items(item_pool)
        self._add_moogle_shop_funding_items(item_pool)
        self._add_ultima_weapon_mode_items(item_pool)
        self._replace_removable_item_pool_items(
            item_pool,
            ADDITIONAL_STAT_BONUS_POOL_ITEMS,
            "KH3 additional stat bonus pool items",
            set(MUNNY_POUCH_ITEM_NAMES) if self._moogle_shop_funding_plan.shop_check_count > 0 else set(),
        )
        if self.options.include_space.value:
            from .Space import SPACE_POOL_ITEMS
            self._replace_removable_item_pool_items(
                item_pool, SPACE_POOL_ITEMS, "Gummi space rewards",
                set(MUNNY_POUCH_ITEM_NAMES) if self._moogle_shop_funding_plan.shop_check_count > 0 else set(),
            )
        self._replace_removable_item_pool_items(
            item_pool, ["Gate Opener"] * self.options.gate_opener_count.value,
            "Gate Openers",
            set(MUNNY_POUCH_ITEM_NAMES) if self._moogle_shop_funding_plan.shop_check_count > 0 else set(),
        )
        self.multiworld.itempool.extend(item_pool)
        self._place_fixed_rewards()

    def _place_fixed_rewards(self) -> None:
        victory_location = self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
        victory_location.place_locked_item(self.create_event_item("Victory"))
        if int(self.options.ultima_weapon_mode.value) == UltimaWeaponMode.option_yozora:
            yozora_location = next(
                self.multiworld.get_location(location["name"], self.player)
                for location in self.active_locations
                if (location["source_table"], location["source_id"]) == YOZORA_REWARD_SOURCE_KEY
            )
            yozora_location.place_locked_item(self.create_item(ULTIMA_WEAPON_ITEM_NAME))
        if self._rock_titan_sequence_break_bonus_enabled():
            self.multiworld.get_location(
                ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, self.player,
            ).place_locked_item(self.create_item(ULTIMA_WEAPON_ITEM_NAME))

    def _add_ultima_weapon_mode_items(self, item_pool: list[Item]) -> None:
        mode = int(self.options.ultima_weapon_mode.value)
        if mode == UltimaWeaponMode.option_shards:
            item_names = [ULTIMA_WEAPON_SHARD_ITEM_NAME] * int(
                self.options.ultima_weapon_shard_count.value
            )
        elif mode == UltimaWeaponMode.option_randomized_pool:
            item_names = [ULTIMA_WEAPON_ITEM_NAME]
        else:
            item_names = []
        protected_item_names = set(PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES)
        funding_plan = getattr(self, "_moogle_shop_funding_plan", None)
        if funding_plan is not None and funding_plan.shop_check_count > 0:
            protected_item_names.update(MUNNY_POUCH_ITEM_NAMES)
        self._replace_removable_item_pool_items(
            item_pool,
            item_names,
            "Ultima Weapon mode items",
            protected_item_names,
        )

    def _ultima_weapon_shop_required_keyblades(self) -> list[str]:
        if int(self.options.ultima_weapon_mode.value) != UltimaWeaponMode.option_shop:
            return []

        excluded_names = {
            ULTIMA_WEAPON_ITEM_NAME,
            *STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES,
        }
        supported_names = [
            keyblade_name
            for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
            if keyblade_name not in excluded_names
        ]
        if (
            effective_keyblade_levels(self.options)
            != KeybladeLevels.option_keyblade_upgrade_sanity
        ):
            return supported_names

        owned_item_names = {
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
            if item.player == self.player
        }
        return [
            keyblade_name
            for keyblade_name in supported_names
            if PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade_name]
            in owned_item_names
        ]

    def _add_trap_items(self, item_pool: list[Item]) -> None:
        protect_munny_pouches = any(
            location.get("type") == "shop"
            for location in self.active_locations
        )
        filler_indices = [
            index
            for index, item in enumerate(item_pool)
            if item.classification == ItemClassification.filler
            and (not protect_munny_pouches or item.name not in MUNNY_POUCH_ITEM_NAMES)
        ]
        trap_count = len(filler_indices) * int(self.options.trap_percentage.value) // 100
        if trap_count <= 0:
            return

        for index in self.random.sample(filler_indices, trap_count):
            item_pool[index] = self.create_item(self.random.choice(TRAP_ITEM_NAMES))

    def _add_moogle_shop_funding_items(self, item_pool: list[Item]) -> None:
        shop_check_count = sum(
            1
            for location in self.active_locations
            if location.get("type") == "shop"
        )
        pouch_items = [
            item
            for item in item_pool + self.multiworld.precollected_items[self.player]
            if item.name in MUNNY_POUCH_ITEM_NAMES
        ]
        existing_pouch_munny = sum(
            MUNNY_POUCH_MUNNY_AMOUNT_BY_NAME[item.name]
            for item in pouch_items
        )
        removable_filler_indices = [
            index
            for index, item in enumerate(item_pool)
            if item.classification == ItemClassification.filler
            and item.name not in MUNNY_POUCH_ITEM_NAMES
        ]
        plan = build_moogle_shop_funding_plan(
            shop_check_count=shop_check_count,
            requested_base_price=int(self.options.moogle_shop_price.value),
            existing_pouch_munny=existing_pouch_munny,
            available_filler_slots=len(removable_filler_indices),
            large_pouch_munny=LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
        )
        self._moogle_shop_funding_plan = plan
        if plan.large_pouch_count <= 0:
            return

        for index in self.random.sample(removable_filler_indices, plan.large_pouch_count):
            item_pool[index] = self.create_item(LARGE_MUNNY_POUCH_ITEM_NAME)

        # At least one funding pouch belongs in the early multiworld sphere.
        # It may still be hosted by another game, which keeps cross-world seeds
        # interesting while preventing KH3's first shop visit from being dry.
        early_items = self.multiworld.early_items[self.player]
        early_items[LARGE_MUNNY_POUCH_ITEM_NAME] = max(
            1,
            int(early_items.get(LARGE_MUNNY_POUCH_ITEM_NAME, 0)),
        )

    def _ensure_required_goal_items_in_pool(self, item_pool: list[Item]) -> None:
        existing_counts = Counter(item.name for item in item_pool
                                  + self.multiworld.precollected_items[self.player])
        # Archipelago has already precollected these items, but removes their
        # pool copies after create_items. Reserve those copies until that pass.
        precollected_counts = Counter(item.name for item in self.multiworld.precollected_items[self.player])
        for name, count in self.options.start_inventory_from_pool.value.items():
            existing_counts[name] -= min(count, precollected_counts[name])
        missing_items = [
            name
            for name, count in sorted(goal_required_item_counts(int(self.options.heart_piece_count.value)).items())
            for _ in range(max(0, count - existing_counts[name]))
        ]
        self._replace_removable_item_pool_items(
            item_pool,
            missing_items,
            "required KH3 goal items",
        )

    def _ensure_randomized_keyblades_in_pool(self, item_pool: list[Item]) -> None:
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        missing_keyblades = sorted(RANDOMIZED_KEYBLADE_ITEM_NAMES.difference(existing_item_names))
        self._replace_removable_item_pool_items(
            item_pool,
            missing_keyblades,
            "randomized KH3 keyblades",
        )

    def _ensure_secret_reports_in_pool(self, item_pool: list[Item]) -> None:
        # Disabled Battlegate checks must not remove their reports from the seed.
        # Retain reports already sourced from enabled checks or starting inventory.
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        self._replace_removable_item_pool_items(
            item_pool,
            [name for name in SECRET_REPORT_ITEM_NAMES if name not in existing_item_names],
            "KH3 Secret Reports",
        )

    def _ensure_required_logic_items_in_pool(self, item_pool: list[Item]) -> None:
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        missing_logic_items = sorted(self._required_logic_item_names().difference(existing_item_names))
        self._replace_removable_item_pool_items(
            item_pool,
            missing_logic_items,
            "required KH3 logic items",
        )

    def _add_randomized_default_base_ability_items_to_pool(self, item_pool: list[Item]) -> None:
        default_base_ability_items = self._default_base_ability_item_names_to_add()
        if not default_base_ability_items:
            return
        # ChrInit is not an AP location, so each suppressed starting ability needs
        # its own pool copy even when the same ability is also awarded elsewhere.
        self._replace_removable_item_pool_items(
            item_pool,
            sorted(default_base_ability_items),
            "KH3 default base ability AP items",
        )

    def _add_retired_growth_path_ability_items_to_pool(self, item_pool: list[Item]) -> None:
        self._replace_removable_item_pool_items(
            item_pool,
            list(RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS),
            "KH3 retired growth-path ability pool items",
        )

    def _required_logic_item_names(self) -> set[str]:
        required_item_names: set[str] = set()
        lucky_emblems_active = False

        def add_requirement_item_names(requirement_routes) -> None:
            for route in requirement_routes:
                for item_names in route:
                    required_item_names.update(item_names)

        for location in self.active_locations + self.logic_reference_locations:
            if (
                location.get("classic_kingdom_check_kind")
                or location.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID
            ):
                required_item_names.update(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
            if location["source_table"] == "LuckyMark":
                lucky_emblems_active = True
            source_key = (location["source_table"], location["source_id"])
            requirement_routes = (
                CHEST_LOGIC_REQUIREMENTS.get(source_key)
                or VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key)
                or EVENT_LOGIC_REQUIREMENTS.get(source_key)
                or INGREDIENT_LOGIC_REQUIREMENTS.get(source_key)
            )
            if requirement_routes:
                add_requirement_item_names(requirement_routes)

        if lucky_emblems_active:
            for spot_requirements in LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD.values():
                for requirement_routes in spot_requirements:
                    if requirement_routes:
                        add_requirement_item_names(requirement_routes)

        required_item_names.difference_update(self._starting_controller_unlock_item_names())
        required_item_names.difference_update(self._starting_default_base_ability_item_names())
        required_item_names.difference_update(FIXED_SORA_CHRINIT_ABILITY_ITEM_NAMES)
        return required_item_names

    def _move_starting_inventory_items_from_pool(self, item_pool: list[Item]) -> None:
        starting_item_count = len(self.starting_inventory_locations)
        protected_item_names = (
            set(goal_required_item_names(int(self.options.heart_piece_count.value)))
            | PORTAL_KEYBLADE_ITEM_NAMES
            | set(CONTROLLER_UNLOCK_ITEM_NAMES)
            | set(KEYBLADE_STYLE_ITEM_NAMES)
            | self._required_logic_item_names()
            | self._default_base_ability_item_names_to_add()
        )
        if self.options.portal_unlocks.value == 1:
            protected_item_names |= PORTAL_PARTY_ITEM_NAMES
        for _ in range(min(starting_item_count, len(item_pool))):
            eligible_indices = [
                index
                for index, item in enumerate(item_pool)
                if item.name not in protected_item_names
            ]
            if not eligible_indices:
                break
            item_index = self.random.choice(eligible_indices)
            item = item_pool.pop(item_index)
            self.multiworld.push_precollected(item)
            self.kh3_starting_inventory_items.append(item.name)

    def _move_starting_portal_keyblade_from_pool(self, item_pool: list[Item]) -> None:
        self.kh3_starting_inventory_items = []
        randomized_starting_worlds = {
            str(location.get("world") or "")
            for location in self.active_locations
            if not uses_replace_location_pool(location, self.options)
        }
        eligible_keyblade_names = {
            keyblade_name
            for region_name, keyblade_name in PORTAL_KEYBLADE_REGION_LOCKS.items()
            if region_name in randomized_starting_worlds
        }
        starting_names = STARTING_PORTAL_KEYBLADE_ITEM_NAMES
        if self.options.portal_unlocks.value == 1:
            starting_names = frozenset(
                name for region, names in PORTAL_PARTY_REGION_LOCKS.items()
                if PORTAL_KEYBLADE_REGION_LOCKS.get(region) in STARTING_PORTAL_KEYBLADE_ITEM_NAMES
                for name in names
            )
            eligible_keyblade_names = {
                name for region, names in PORTAL_PARTY_REGION_LOCKS.items()
                if region in randomized_starting_worlds for name in names
            }
        candidate_indices = [
            index
            for index, item in enumerate(item_pool)
            if item.name in starting_names
            and item.name in eligible_keyblade_names
        ]
        if not candidate_indices:
            candidate_indices = [
                index
                for index, item in enumerate(item_pool)
                if item.name in starting_names
            ]
        if not candidate_indices:
            return

        item_index = self.random.choice(candidate_indices)
        item = item_pool.pop(item_index)
        self.multiworld.push_precollected(item)
        self.kh3_starting_inventory_items.append(item.name)
        item_pool.append(self.create_filler())

    def _add_secondary_vbonus_item_pool_items(self, item_pool: list[Item]) -> None:
        item_names = self._secondary_vbonus_item_pool_item_names()
        if not item_names:
            return
        self._replace_removable_item_pool_items(
            item_pool,
            item_names,
            "KH3 secondary VBonus item pool items",
        )

    def _secondary_vbonus_item_pool_item_names(self) -> list[str]:
        item_names: list[str] = []
        excluded_item_names = set(FIXED_SORA_CHRINIT_ABILITY_ITEM_NAMES)
        excluded_item_names.update({*PROOF_ITEMS, HEART_PIECE_ITEM_NAME})
        for location in self.active_locations:
            if location.get("type") != "vbonus":
                continue
            primary_item_name = str(location.get("primary_item_name") or "")
            primary_item_skipped = False
            vanilla_item_names = location.get("vanilla_item_names")
            if not isinstance(vanilla_item_names, dict):
                continue
            for item_name in vanilla_item_names.values():
                if item_name == primary_item_name and not primary_item_skipped:
                    primary_item_skipped = True
                    continue
                if item_name in SECONDARY_VBONUS_ITEM_POOL_REWARDS and item_name not in excluded_item_names:
                    item_names.append(item_name)
        return item_names

    def _add_controller_unlock_item_pool_items(self, item_pool: list[Item]) -> None:
        if not CONTROLLER_UNLOCK_EXTRA_ITEM_POOL_ITEMS:
            return
        starting_unlock_items = self._starting_controller_unlock_item_names()
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        for item_name in sorted(starting_unlock_items - existing_item_names):
            self.multiworld.push_precollected(self.create_item(item_name))
            self.kh3_starting_inventory_items.append(item_name)
        existing_item_names.update(starting_unlock_items)
        controller_unlock_items = [
            item_name
            for item_name in CONTROLLER_UNLOCK_EXTRA_ITEM_POOL_ITEMS
            if item_name not in {STYLE_CHANGE_UNLOCK_ITEM, SPELLWEAVER_UNLOCK_ITEM}
            if item_name not in starting_unlock_items and item_name not in existing_item_names
        ]
        if not controller_unlock_items:
            return
        self._replace_removable_item_pool_items(
            item_pool,
            controller_unlock_items,
            "KH3 controller unlock item pool items",
        )

    def _add_keyblade_style_item_pool_items(self, item_pool: list[Item]) -> None:
        if not self.options.stylesanity.value:
            return
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        style_items = [
            item_name
            for item_name in KEYBLADE_STYLE_EXTRA_ITEM_POOL_ITEMS
            if item_name not in existing_item_names
        ]
        if not style_items:
            return
        self._replace_removable_item_pool_items(
            item_pool,
            style_items,
            "KH3 keyblade style item pool items",
        )

    def _add_party_member_unlock_item_pool_items(self, item_pool: list[Item]) -> None:
        if not PARTY_MEMBER_UNLOCK_EXTRA_ITEM_POOL_ITEMS:
            return
        existing_item_names = {
            item.name
            for item in item_pool + self.multiworld.precollected_items[self.player]
        }
        party_member_items = [
            item_name
            for item_name in PARTY_MEMBER_UNLOCK_EXTRA_ITEM_POOL_ITEMS
            if item_name not in existing_item_names
        ]
        if not party_member_items:
            return
        self._replace_removable_item_pool_items(
            item_pool,
            party_member_items,
            "KH3 party member unlock item pool items",
        )

    def _add_progressive_keyblade_level_items(self, item_pool: list[Item]) -> None:
        keyblade_levels = effective_keyblade_levels(self.options)
        if keyblade_levels == KeybladeLevels.option_start_at_max:
            return

        protected_item_names: set[str] = set()
        if keyblade_levels == KeybladeLevels.option_keyblade_upgrade_sanity:
            randomized_item_counts = Counter(item.name for item in item_pool)
            randomized_item_counts.subtract(self.options.start_inventory_from_pool.value)
            protected_item_names = {
                keyblade_name
                for keyblade_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE
                if (
                    int(self.options.ultima_weapon_mode.value) != UltimaWeaponMode.option_banished
                    if keyblade_name == ULTIMA_WEAPON_ITEM_NAME
                    else randomized_item_counts[keyblade_name] > 0
                )
            }
            upgrade_sanity_keyblade_names = (
                protected_item_names
                | STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES
            )
            item_names = [
                PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade_name]
                for keyblade_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE
                if keyblade_name in upgrade_sanity_keyblade_names
                for _ in range(PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT)
            ]
        else:
            item_names = [PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME] * PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT
        self._replace_removable_item_pool_items(
            item_pool,
            item_names,
            "Progressive Keyblade Level item pool items",
            protected_item_names,
        )

    def _starting_controller_unlock_item_names(self) -> set[str]:
        item_names = {
            item_name
            for option_name, item_name in CONTROLLER_UNLOCK_ITEM_NAMES_BY_OPTION.items()
            if bool(getattr(self.options, option_name).value)
        }
        item_names.add("Unlock: Airstep")
        starting_aqua = any(
            getattr(self.options, option).value.get("Playable Character: Aqua", 0) > 0
            for option in ("start_inventory", "start_inventory_from_pool")
        )
        if starting_aqua or self.options.kh02_enabled.value:
            item_names.add(SPELLWEAVER_UNLOCK_ITEM)
        if not self.options.stylesanity.value:
            # Mirror the equipped CharInit ability in AP's starting state.
            item_names.add(STYLE_CHANGE_UNLOCK_ITEM)
        return item_names

    def _starting_default_base_ability_item_names(self) -> set[str]:
        if not bool(self.options.default_base_abilities.value):
            return set()
        return set(DEFAULT_BASE_ABILITY_ITEM_NAMES)

    def _default_base_ability_item_names_to_add(self) -> set[str]:
        if bool(self.options.default_base_abilities.value):
            return set()
        return set(DEFAULT_BASE_ABILITY_ITEM_NAMES)

    def _replace_removable_item_pool_items(
        self,
        item_pool: list[Item],
        item_names: tuple[str, ...] | list[str],
        label: str,
        protected_item_names: set[str] | None = None,
    ) -> None:
        if not item_names:
            return
        protected_item_names = set(protected_item_names or ())
        # Useful Classic Kingdom games still gate optional checks. Later pool
        # additions must retain them even after filler has become traps.
        protected_item_names.update(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES)
        # Preserve every report and style unlock when extra rewards need to
        # replace useful items after the pool has run out of filler.
        protected_item_names.update(SECRET_REPORT_ITEM_NAMES)
        protected_item_names.update(KEYBLADE_STYLE_ITEM_NAMES)
        # Extra rewards must not consume upgrade items or the keyblades those
        # upgrades were generated for, even when a small pool runs out of filler.
        protected_item_names.update(PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES)
        upgrade_names = {item.name for item in item_pool}
        protected_item_names.update(
            keyblade_name
            for keyblade_name, upgrade_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE.items()
            if upgrade_name in upgrade_names
        )
        removable_indices = [
            index
            for index, item in enumerate(item_pool)
            if item.classification == ItemClassification.filler and item.name not in protected_item_names
        ]
        if len(removable_indices) < len(item_names):
            removable_indices.extend(
                index
                for index, item in enumerate(item_pool)
                if not item.advancement
                and item.name not in protected_item_names
                and index not in removable_indices
            )

        if len(removable_indices) < len(item_names):
            raise RuntimeError(
                f"Unable to add {label} without replacing progression items."
            )

        for index in sorted(self.random.sample(removable_indices, len(item_names)), reverse=True):
            item_pool.pop(index)
        item_pool.extend(self.create_item(item_name) for item_name in item_names)

    def create_item(self, name: str) -> Item:
        if name == "Orichalcum+":
            raise ValueError("Vanilla Orichalcum+ cannot be randomized; use Ultima Weapon Shard instead.")
        code = self.item_name_to_id.get(name)
        classification = classify_item(name)
        if (name == ENDYMION and self.options.include_space.value
                and self.options.include_space_superbosses.value):
            # Schwarzgeist still needs its 200-speed ship, independently of zone access.
            classification = ItemClassification.progression
        if self.options.portal_unlocks.value == 1 and name in PORTAL_PARTY_ITEM_NAMES:
            classification = ItemClassification.progression
        if name in getattr(self, "keyblade_chest_required_items", ()):
            classification |= ItemClassification.progression
        if int(self.options.keyblade_upgrade_rewards_pool.value) != 1 and (
            name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES or name in KEYBLADE_DISPLAY_NAMES
        ):
            classification |= ItemClassification.progression
        if name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES:
            # High-score checks can hold progression independently of the all-games reward.
            junk_only = (bool(self.options.junk_classic_kingdom_reward.value)
                         and not self.options.classic_kingdom_checks.value)
            classification = ItemClassification.useful if junk_only else ItemClassification.progression
        if name == HEART_PIECE_ITEM_NAME:
            classification = ItemClassification.progression
        return KH3Item(name, classification, code, self.player)

    def collect_item(self, state: CollectionState, item: Item, remove: bool = False) -> str | None:
        try:
            return item.logic_item_name
        except AttributeError:
            pass
        if int(item.classification) & PROGRESSION_ITEM_FLAG:
            return item.name
        return None

    def collect(self, state: CollectionState, item: Item) -> bool:
        try:
            name = item.logic_item_name
        except AttributeError:
            name = self.collect_item(state, item)
        if not name:
            if item.useful and item.name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES:
                record_useful_game(state, self.player, item.name)
            # Optional receipts do not grow AP progression or trigger its sweep.
            return False
        state.add_item(name, self.player)
        totals = getattr(state, "_kh3_reachable_counts", None)
        if totals is not None:
            totals.pop(self.player, None)
        return True

    def remove(self, state: CollectionState, item: Item) -> bool:
        name = self.collect_item(state, item, True)
        if not name:
            if item.useful and item.name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES:
                return record_useful_game(state, self.player, item.name, remove=True)
            return False
        state.remove_item(name, self.player)
        totals = getattr(state, "_kh3_reachable_counts", None)
        if totals is not None:
            totals.pop(self.player, None)
        return True

    def get_filler_item_name(self) -> str:
        return self.random.choice(FILLER_ITEM_NAMES)

    def set_rule(self, spot, rule) -> None:
        pending = getattr(self, "_pending_region_rules", None)
        if pending is not None:
            pending[spot] = rule
        super().set_rule(spot, rule)
        # Keep the default AP predicate for checks whose compiled rule is
        # unconditional. Avoid a Rule Builder call on every reachability sweep.
        if getattr(spot.access_rule, 'always_true', False):
            spot.access_rule = DEFAULT_COLLECTION_RULE

    def create_event_item(self, name: str) -> Item:
        return KH3Item(name, ItemClassification.progression, None, self.player)

    def set_rules(self) -> None:
        # Access predicates are built with their shared regions in create_regions.
        pass

    def _create_access_rules_and_regions(self) -> None:
        if not self.location_name_to_id:
            raise RuntimeError("KH3 extracted location data is empty.")
        from .rule_helpers import cache_reachable_rule_construction

        self._pending_region_rules = {}
        with cache_reachable_rule_construction(self):
            set_rules(self)
            from .Space import set_space_rules
            set_space_rules(self)
            if self._rock_titan_sequence_break_bonus_enabled():
                rock_titan_location = next(
                    self.multiworld.get_location(location["name"], self.player)
                    for location in self.active_locations
                    if (location["source_table"], location["source_id"]) == ROCK_TITAN_VBONUS_SOURCE_KEY
                )
                bonus_location = self.multiworld.get_location(
                    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
                    self.player,
                )
                bonus_location.access_rule = rock_titan_location.access_rule
                bonus_location.item_rule = rock_titan_location.item_rule
                bonus_location.filler_only = rock_titan_location.filler_only
                bonus_location.progress_type = rock_titan_location.progress_type
            set_extra_vbonus_rules(self)
        from .LogicRegions import create_logic_regions
        pending = self._pending_region_rules
        del self._pending_region_rules
        # Moving checks changes CanReachLocation's parent-region dependencies.
        # Resolve against the final layout with a fresh construction cache.
        with cache_reachable_rule_construction(self):
            create_logic_regions(self, pending)
        self.set_completion_rule(Has("Victory"))

    def _rock_titan_sequence_break_bonus_enabled(self) -> bool:
        return any(
            (location["source_table"], location["source_id"]) == ROCK_TITAN_VBONUS_SOURCE_KEY
            for location in self.active_locations
        )

    @classmethod
    def stage_pre_fill(cls, multiworld):
        from .Balancing import mark_optional_balancing_items
        from .StartingInventory import (
            register_post_fill_classifications,
            precollect_overflow,
            precollect_restrictive_starts,
            reserve_all_local_locations,
        )

        # LocalItems forbids exports. A saturated local pool also needs to
        # reserve its own checks before AP places other players' early items.
        register_post_fill_classifications(multiworld)
        reserve_all_local_locations(multiworld)
        precollect_overflow(multiworld)
        precollect_restrictive_starts(multiworld)
        mark_optional_balancing_items(multiworld)

    def build_seed_container_data(self) -> dict[str, Any]:
        slot_data = self.fill_slot_data()
        seed_data = slot_data["kh3_seed"]

        return {
            "format": "kh3_ap_seed",
            "format_version": 1,
            "seed_name": seed_data["seed_name"],
            "archive_name": seed_data["archive_name"],
            "slot": seed_data["slot"],
            "slot_name": seed_data["slot_name"],
            "all_players": seed_data["all_players"],
            "pak_seed": seed_data["pak_seed"],
            "randomizer_config": slot_data["kh3_randomizer"],
            "remote_hint_placements": seed_data["remote_hint_placements"],
            "slot_data": slot_data,
        }

    @classmethod
    def stage_pre_output(cls, multiworld) -> None:
        """Index final remote placements once, before AP starts output threads."""
        worlds = {player: multiworld.worlds[player] for player in multiworld.player_ids
                  if isinstance(multiworld.worlds[player], cls)}
        remote_locations = {player: [] for player in worlds}
        for location in multiworld.get_locations():
            item = location.item
            if (item is not None and item.player in worlds and item.code is not None
                    and location.address is not None and location.player != item.player):
                remote_locations[item.player].append(location)
        for player, world in worlds.items():
            world._output_remote_hint_locations = tuple(remote_locations[player])
            world._output_slot_data = None
            world._output_slot_data_lock = Lock()

    def build_remote_hint_placements(self) -> list[dict[str, Any]]:
        if self.multiworld.is_race:
            return []
        placements: list[dict[str, Any]] = []
        recipient_name = self.multiworld.get_player_name(self.player)

        locations = getattr(self, "_output_remote_hint_locations", None)
        if locations is None:
            locations = self.multiworld.get_locations()
        for location in locations:
            item = location.item
            if (
                item is None
                or item.player != self.player
                or item.code is None
                or location.player == self.player
                or location.address is None
            ):
                continue

            location_game = self.multiworld.game[location.player]
            placements.append({
                "ap_location_id": int(location.address),
                "ap_location_name": location.name,
                "location_type": "archipelago",
                "source_table": "Archipelago",
                "source_id": str(location.address),
                "world": location_game,
                "location_description": location.name,
                "location_game": location_game,
                "location_player_slot": location.player,
                "location_player_name": self.multiworld.get_player_name(location.player),
                "is_remote_location": True,
                "item_id": item.code,
                "item_name": item.name,
                "item_display_name": item.name,
                "item_game": "Kingdom Hearts III",
                "recipient_slot": self.player,
                "recipient_name": recipient_name,
                "is_foreign_item": False,
            })

        return sorted(
            placements,
            key=lambda placement: (
                int(placement["location_player_slot"]),
                int(placement["ap_location_id"]),
            ),
        )

    def generate_output(self, output_directory: str) -> None:
        container = KH3Container(
            path=os.path.join(
                output_directory,
                f"{self.multiworld.get_out_file_name_base(self.player)}{KH3Container.patch_file_ending}",
            ),
            player=self.player,
            player_name=self.multiworld.get_player_name(self.player),
            data=self.build_seed_container_data(),
        )
        container.write()

    def fill_slot_data(self) -> Dict[str, Any]:
        output_lock = getattr(self, "_output_slot_data_lock", None)
        if output_lock is None:
            # Tests and tracker callers may request data before placements are final.
            return encode_slot_data(self.build_runtime_slot_data())
        with output_lock:
            if self._output_slot_data is None:
                self._output_slot_data = encode_slot_data(self.build_runtime_slot_data())
            # AP's patch/server serializers only read this finalized payload.
            return self._output_slot_data

    def build_runtime_slot_data(self) -> Dict[str, Any]:
        funding_plan = getattr(self, "_moogle_shop_funding_plan", None)
        price_tiers = (
            funding_plan.price_tiers
            if funding_plan is not None
            else build_moogle_shop_price_tiers(int(self.options.moogle_shop_price.value))
        )
        bridge_data = build_bridge_slot_data(
            self.active_locations,
            int(self.options.heart_piece_count.value),
            worlds_required=int(self.options.worlds_required.value),
            moogle_shop_price=price_tiers.filler,
            moogle_shop_token_namespace=hashlib.sha256(
                f"{self.multiworld.seed_name}\0{self.player}".encode("utf-8")
            ).hexdigest()[:10].upper(),
        )
        if self._rock_titan_sequence_break_bonus_enabled():
            from .Locations import ROCK_TITAN_BONUS_BRIDGE_LOCATION
            bridge_data["locations"].append(dict(ROCK_TITAN_BONUS_BRIDGE_LOCATION))
            bridge_data.setdefault("conditional_checks", []).append({
                "trigger_bridge_key": ROCK_TITAN_VBONUS_BRIDGE_KEY,
                "ap_location_id": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
                "ap_location_name": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
                "unless_item_id": self.item_name_to_id["Unlock: Wall Run"],
                "unless_item_name": "Unlock: Wall Run",
                "unless_start_option": "start_with_wall_run",
            })
        bridge_data["chest_persistence_version"] = 1
        bridge_data["space_version"] = 1 if self.options.include_space.value else 0
        for bridge_location in bridge_data["locations"]:
            if bridge_location.get("type") == "treasure":
                bridge_location["chest_persistence_version"] = 1
            location = self.multiworld.get_location(bridge_location["ap_location_name"], self.player)
            if location.item is None:
                continue
            item = location.item
            bridge_location.update({
                "assigned_item_id": item.code,
                "assigned_item_name": item.name,
                "assigned_item_player": item.player,
                "assigned_item_player_name": self.multiworld.get_player_name(item.player),
                "assigned_item_game": self.multiworld.game[item.player],
                "assigned_item_classification": int(item.classification),
                "is_foreign_item": item.player != self.player,
            })
            if bridge_location.get("type") == "shop":
                bridge_location["shop_price"] = moogle_shop_price_for_classification(
                    item.classification,
                    price_tiers,
                )
            if (
                item.player == self.player
                and local_seeded_reward_requires_bridge_delivery(
                    bridge_location.get("bridge_key"),
                    bridge_location.get("type"),
                    resolve_item_runtime_token(item.name),
                )
            ):
                bridge_location["bridge_delivery_required"] = True

        shop_bridge_locations = [
            location
            for location in bridge_data["locations"]
            if location.get("type") == "shop"
        ]
        actual_shop_cost = sum(
            int(location.get("shop_price") or 0)
            for location in shop_bridge_locations
        )
        economy_summary = (
            funding_plan.as_dict()
            if funding_plan is not None
            else {
                "shop_check_count": len(shop_bridge_locations),
                "requested_base_price": int(self.options.moogle_shop_price.value),
                "effective_base_price": price_tiers.filler,
                "price_tiers": price_tiers.as_dict(),
                "planned_pouch_munny": 0,
            }
        )
        economy_summary.update({
            "actual_shop_cost": actual_shop_cost,
            "actual_shop_item_count": len(shop_bridge_locations),
        })
        bridge_data["moogle_shop_economy"] = economy_summary

        slot_data = self.options.as_dict(
            "portal_unlocks",
            "ally_characters",
            "gate_opener_count",
            "include_space",
            "include_grindy_space_missions",
            "include_space_superbosses",
            "synthesis_pool",
            "photo_missions_pool",
            "keyblade_upgrade_rewards_pool",
            "moogle_synthesis_limit",
            "heart_piece_count",
            "worlds_required",
            "battle_level_mode",
            "lucky_emblems_per_check",
            "classic_kingdom_checks",
            "kh02_enabled",
            "kh02_challenges",
            "festival_dance_max_rank",
            "verum_rex_max_rank",
            "frozen_slider_max_rank",
            "flash_tracer_course_a_max_rank",
            "flash_tracer_course_b_max_rank",
            "ingredients",
            "olympus_coliseum_pool",
            "music_chest_count",
            "music_chest_vanilla_conversion",
            "melody_of_memory_song_whitelist",
            "melody_of_memory_song_blacklist",
            "chestsanity",
            "roxas_chestsanity",
            "junk_san_fransokyo_chests",
            "junk_the_caribbean_chests",
            "melody_of_memory_difficulty",
            "melody_of_memory_party",
            "melody_of_memory_play_style",
            "melody_of_memory_starting_item",
            "battle_level_offset",
            "ultima_weapon_mode",
            "ultima_weapon_shard_count",
            "moogle_shop_checks",
            "moogle_shop_price",
            "trap_percentage",
            "auto_skip_cutscenes",
            "auto_equip_essential_abilities",
            "color_randomization",
            "music_shuffle",
            "include_custom_music",
            "form_themes",
            "map_overlay_keybinds",
            "map_overlay_enabled",
            "song_display",
            "hide_locked_chest_icons",
            "map_chest_icons",
            "map_emblem_icons",
            "map_ingredient_icons",
            "minimap_icons",
            "large_map_icons",
            "skip_guardians_and_mickey_walk",
            "fast_union_cross",
            "fast_dance_minigame",
            "fast_beat_of_lead",
            "fun_10k_heartless",
            "dark_baymax_phase_1_skip",
            "max_level_ship",
            "death_link",
            "death_link_amnesty",
            "start_with_wall_run",
            "stylesanity",
            "start_with_magic",
            "start_with_links",
            "start_with_attraction_flow",
        )
        # Airstep is baseline movement, not a YAML setting.
        slot_data["start_with_airstep"] = 1
        # Every music chest awards a bonus item; this is not a YAML setting.
        slot_data["music_chest_bonus_checks"] = 1
        # Keep the native/client wire key compatible with the installed probe.
        slot_data["ingredient_checks"] = int(self.options.ingredients.value == 1)
        slot_data["adversary_checks"] = int(self.options.adversary_checks.value)
        slot_data["keyblade_levels"] = effective_keyblade_levels(self.options)
        slot_data["ultima_weapon_shop_required_keyblades"] = (
            self._ultima_weapon_shop_required_keyblades()
        )
        slot_data.update({
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "bridge": bridge_data,
            "location_id_to_alias": {
                str(row["ap_location_id"]): self.location_id_to_alias[row["ap_location_id"]]
                for row in bridge_data["locations"] if row["ap_location_id"] in self.location_id_to_alias
            },
            "moogle_shop_price_tiers": price_tiers.as_dict(),
            "moogle_shop_economy": economy_summary,
            "kh3_randomizer": build_randomizer_generation_config(self.options),
            "kh3_seed": {
                "format": "kh3_server_seed",
                "format_version": 1,
                "seed_creation_version": KH3_WORLD_VERSION,
                "seed_name": self.multiworld.seed_name,
                "archive_name": f"AP_{self.multiworld.seed_name}.zip",
                "slot": self.player,
                "slot_name": self.multiworld.get_player_name(self.player),
                "all_players": [
                    self.multiworld.get_player_name(player)
                    for player in self.multiworld.player_ids
                ],
                "pak_seed": self.multiworld.get_out_file_name_base(self.player),
                "remote_hint_placements": self.build_remote_hint_placements(),
            },
        })
        slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY] = (
            build_universal_tracker_slot_data(self.options)
        )
        slot_data["race_mode"] = bool(
            self.multiworld.is_race
            or getattr(self, "_tracker_runtime_slot_data", {}).get("race_mode")
        )
        if slot_data["race_mode"]:
            from .RaceMode import race_slot_data
            return race_slot_data(slot_data)
        return slot_data


# AutoWorld supplies catch-all groups after class creation. Keep KH3 location
# selection explicit and apply the ability exclusions to its item catch-all.
KH3World.location_name_groups.pop("Everywhere", None)
KH3World.item_name_groups["Everything"] -= ABILITY_GROUP_EXCLUSIONS
