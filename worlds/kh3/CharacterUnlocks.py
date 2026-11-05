"""Guaranteed, local AP character rewards at their completion milestones."""

CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX = "KH3AP_CHARACTER_UNLOCK::"
CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME = {
    "Playable Character: Kairi": f"{CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX}16",
    "Playable Character: Roxas": f"{CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX}17",
    "Playable Character: Aqua": f"{CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX}10",
    "Playable Character: Riku": f"{CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX}8",
}
CHARACTER_UNLOCK_ITEM_NAMES = tuple(CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME)
CHARACTER_UNLOCK_ITEM_ALIASES = {
    f"PC: {item_name.removeprefix('Playable Character: ')}": item_name
    for item_name in CHARACTER_UNLOCK_ITEM_NAMES
}
CHARACTER_MOVEMENT_ITEM_NAMES = frozenset({"Ability: Doubleflight", "Ability: Air Slide"})
CHARACTER_UNLOCK_LOCATION_ID_BASE = 0x4B4D3600
DATA_BATTLE_COMPLETION_SOURCE_KEYS = tuple(("Event", f"EVENT_DATAB_{index:03}") for index in range(1, 14))
ROXAS_REQUIRED_KEYBLADES = ("Oblivion", "Oathkeeper")
CHARACTER_UNLOCK_REWARDS = (
    ("Re Mind - Unlock Kairi", "Re Mind", "Kairi", "VBonus:VBonus_DLC_015"),
    ("Keyblade Graveyard - Unlock Roxas", "Keyblade Graveyard", "Roxas", "VBonus:Vbonus_075"),
    ("0.2 - Unlock Aqua", "Kingdom Hearts 0.2", "Aqua", "KH02:Boss:DemonTide"),
    ("Data Battles - Unlock Riku", "Data Battles", "Riku", None),
)


def character_unlock_locations(*, kh02_enabled: bool) -> list[dict]:
    locations = [dict(
        name=name, type="character_unlock", world=world,
        source_table="CharacterUnlock", source_id=character,
        ap_location_id=CHARACTER_UNLOCK_LOCATION_ID_BASE + index,
        primary_item_name=f"Playable Character: {character}",
        fixed_character_reward=f"Playable Character: {character}",
        vanilla_item="NONE", vanilla_item_name="",
        trigger_bridge_key=trigger,
    ) for index, (name, world, character, trigger) in enumerate(CHARACTER_UNLOCK_REWARDS)
        if character != "Aqua" or kh02_enabled]
    for row in locations:
        if row["source_id"] == "Roxas":
            row["required_item_names"] = ROXAS_REQUIRED_KEYBLADES
        if row["trigger_bridge_key"] is None:
            # Native check reconciliation earns CharacterUnlock:Riku only
            # after observing every Data battle completion event.
            row.pop("trigger_bridge_key")
        if row["source_id"] == "Aqua":
            # Award Aqua directly from the chapter's final victory flag.
            row.update(source_table="KH02", source_id="Boss:UnlockAqua",
                       kh02_flag="BattleBonus_DW_dw_10_DevilWave")
            row.pop("trigger_bridge_key")
    return locations


ALL_CHARACTER_UNLOCK_LOCATIONS = tuple(character_unlock_locations(kh02_enabled=True))
