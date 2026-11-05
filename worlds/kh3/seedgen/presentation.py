# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""Seed-owned AP inventory rows and localized item/shop presentation."""

from __future__ import annotations

import copy
import re

from .assets import get
from .localization import GOALS, SHARD, SPECIAL_NAMES, Locres, text_ids

ICON_ROOT = "/Game/UI/Icon/Item/"
GAME_MARKERS = {
    "kingdomhearts": ("\u2065", "ap_kh1"),
    "kingdomhearts1": ("\u2065", "ap_kh1"),
    "kingdomheartsi": ("\u2065", "ap_kh1"),
    "kh1": ("\u2065", "ap_kh1"),
    "kingdomhearts2": ("\u2066", "ap_kh2"),
    "kingdomheartsii": ("\u2066", "ap_kh2"),
    "kh2": ("\u2066", "ap_kh2"),
    "khbbs": ("\u2067", "ap_kh_bbs"),
    "khcom": ("\u2068", "ap_kh_com"),
    "khrecom": ("\u2068", "ap_kh_com"),
    "khddd": ("\u2069", "ap_kh_ddd"),
}


def native_token(value):
    return value.startswith(("AP_ITEM_", "ITEM_AP_"))


def reserved_row(token, shop=False):
    match = re.match(r"ITEM_AP_(I0[27]\d{3})", token)
    if match and match[1].startswith("I07" if shop else "I02") and 1 <= int(match[1][3:]) <= 255:
        return match[1]
    return None


def icon(name):
    return ICON_ROOT + name + "." + name


def game_marker(game):
    normalized = "".join(c for c in game.casefold() if c.isalnum())
    for part, result in (
        ("birthbysleep", ("\u2067", "ap_kh_bbs")),
        ("chainofmemories", ("\u2068", "ap_kh_com")),
        ("dreamdropdistance", ("\u2069", "ap_kh_ddd")),
    ):
        if part in normalized:
            return result
    return GAME_MARKERS.get(normalized, ("", ""))


def build_presentation(tables, items, metadata, hints):
    tokens = {
        value.rstrip("\0")
        for rows in items.values()
        for fields in rows.values()
        for value in fields.values()
        if isinstance(value, str) and native_token(value)
    }
    names, details, prices, classifications, game_icons = {}, {}, {}, {}, {}
    locations = metadata["placement_summary"]["locations"]
    bridge = metadata["slot_data"].get("bridge", {})
    bridge_classifications = {
        int(p["ap_location_id"]): int(p.get("assigned_item_classification", 0)) for p in bridge.get("locations", [])
    }
    for entry in metadata.get("foreign_item_manifest", {}).get("foreign_items", []):
        token = entry.get("placeholder_token", "").rstrip("\0")
        if native_token(token):
            names[token] = entry.get("item_display_name") or entry.get("item_name") or "Archipelago Item"
    for entry in locations:
        token = entry.get("export_token", "").rstrip("\0")
        shop = entry.get("location_type") == "shop"
        if native_token(token):
            names[token] = (
                (entry.get("item_name") or entry.get("item_display_name"))
                if shop
                else (entry.get("item_display_name") or entry.get("item_name"))
            ) or "Archipelago Item"
        if shop:
            token = (entry.get("shop_item_token") or token).rstrip("\0")
            if not native_token(token):
                raise ValueError(f"Shop item lacks a native AP token: {token}")
            tokens.add(token)
            prices[token] = max(0, int(entry["shop_price"]))
            classification = int(
                entry.get(
                    "item_classification",
                    entry.get(
                        "assigned_item_classification", bridge_classifications.get(int(entry["ap_location_id"]), 0)
                    ),
                )
            )
            classifications[token] = classification
            marker = "\u2064" if classification & 1 else "\u2062" if classification & 2 else "\u2063"
            game, game_icon = game_marker(entry.get("item_game") or entry.get("assigned_item_game") or "")
            names[token] = marker + game + names.get(token, entry.get("item_name") or "Archipelago Item")
            if game_icon:
                game_icons[token] = game_icon
            details[token] = entry.get("item_display_name") or entry.get("item_name") or "An Archipelago item."
    asset = tables.load("Items")

    def row_for(token):
        matches = [row for row in asset.rows.values() if get(row, "m_KeyName").value == token]
        if len(matches) != 1:
            raise ValueError(f"TresItemData must have exactly one {token} row")
        return matches[0]

    template = copy.deepcopy(row_for("ITEM_APUP"))
    shop_template = copy.deepcopy(row_for("MAT_ITEM60"))
    for token in GOALS:
        row = row_for(token)
        name_id, help_id = text_ids(token)
        if get(row, "m_NameTextID").value != name_id or get(row, "m_HelpTextID").value != help_id:
            raise ValueError(f"Goal item {token} requires its dedicated localization")
    get(row_for("KEY_ITEM17"), "m_MaxNum").set(99)
    for token in ("KEY_ITEM18", "KEY_ITEM19", "KEY_ITEM20"):
        row = copy.deepcopy(row_for("KEY_ITEM17"))
        row_name = "I090" + token[8:]
        for name, existing in list(asset.rows.items()):
            if name == row_name or get(existing, "m_KeyName").value == token:
                del asset.rows[name]
        asset.rows[row_name] = row
        for field, value in {
            "m_KeyName": token,
            "m_UIPriority": 5106 + int(token[8:]) - 18,
            "m_Comment": SPECIAL_NAMES[token],
            "m_IconAsset": icon("ap_archipelago_useful" if token == "KEY_ITEM20" else "ap_munny_pouch"),
            "m_NameTextID": text_ids(token)[0],
            "m_HelpTextID": text_ids(token)[1],
            "m_BuyPrice": 0,
            "m_SellPrice": 0,
            "m_bCanSell": False,
            "m_bCanDrop": False,
            "m_bCanbeSoldOut": False,
            "m_MaxNum": 999,
            "m_ShopLevel": 0,
            "m_GumiShopLevel": 0,
            "m_bNeedMogCard": False,
        }.items():
            get(row, field).set(value)
    ultima_mode = metadata["slot_data"].get("ultima_weapon_mode", 0)
    if isinstance(ultima_mode, str) and not ultima_mode.isdecimal():
        ultima_mode = {"shards": 0, "shop": 1, "yozora": 2, "randomized_pool": 3, "banished": 4}[ultima_mode]
    ultima_mode = int(ultima_mode)
    presentation_tokens = tokens | set(GOALS) | {"KEY_ITEM18", "KEY_ITEM19", "KEY_ITEM20"}
    if ultima_mode in (0, 1):
        row = row_for("MAT_ITEM57" if ultima_mode == 0 else "WEP_KEYBLADE_SO_015")
        values = {
            "m_BuyPrice": 0,
            "m_SellPrice": 0,
            "m_ShopLevel": 0,
            "m_GumiShopLevel": 0,
            "m_bNeedMogCard": False,
            "m_bCanSell": False,
            "m_bCanDrop": False,
            "m_bCanbeSoldOut": ultima_mode == 1,
            "m_MaxNum": 99 if ultima_mode == 0 else 1,
        }
        if ultima_mode == 0:
            presentation_tokens.add(SHARD)
            values.update(
                m_Comment=SPECIAL_NAMES[SHARD],
                m_IconAsset=icon("ap_ultima_weapon"),
                m_NameTextID=text_ids(SHARD)[0],
                m_HelpTextID=text_ids(SHARD)[1],
            )
        for field, value in values.items():
            get(row, field).set(value)
    reserved = set()
    for token in tokens:
        row_name = reserved_row(token, token in prices)
        if token in prices and not row_name:
            raise ValueError(f"Shop token does not encode a reserved material row: {token}")
        if row_name:
            if row_name in reserved:
                raise ValueError(f"Duplicate reserved AP inventory row: {row_name}")
            reserved.add(row_name)
    for name, row in list(asset.rows.items()):
        if name in tokens or name in reserved or get(row, "m_KeyName").value in tokens:
            del asset.rows[name]
    next_row = max((int(name[1:]) for name in asset.rows if re.fullmatch(r"I\d+", name)), default=0) + 1
    for token in sorted(tokens):
        shop = token in prices
        row_name = reserved_row(token, shop)
        if row_name is None:
            while f"I{next_row:05}" in asset.rows:
                next_row += 1
            row_name = f"I{next_row:05}"
            next_row += 1
        row = copy.deepcopy(shop_template if shop else template)
        display = names.get(token, "Archipelago Item")
        shard = display.lstrip("".join(chr(n) for n in range(0x2062, 0x206A))) == "Ultima Weapon Shard"
        values = {
            "m_KeyName": token,
            "m_UIPriority": 105,
            "m_Comment": display,
            "m_NameTextID": text_ids(token)[0],
            "m_HelpTextID": text_ids(token)[1],
            "m_BuyPrice": prices.get(token, 0),
            "m_SellPrice": 0,
            "m_bCanSell": False,
            "m_bCanDrop": False,
            "m_bCanbeSoldOut": shop,
            "m_MaxNum": 1 if shop else 99,
        }
        if shard:
            values["m_IconAsset"] = icon("ap_ultima_weapon")
        elif token in game_icons:
            values["m_IconAsset"] = icon(game_icons[token])
        if shop:
            values.update(
                m_CategoryIcon="ETresItemCategoryIcon::NONE", m_ShopLevel=1, m_GumiShopLevel=0, m_bNeedMogCard=False
            )
            if not shard and token not in game_icons:
                classification = classifications[token]
                values["m_IconAsset"] = icon(
                    "ap_archipelago_progression"
                    if classification & 1
                    else "ap_archipelago_useful"
                    if classification & 2
                    else "ap_archipelago"
                )
        for field, value in values.items():
            get(row, field).set(value)
        asset.rows[row_name] = row
        if reserved_row(token):
            asset.rows[token] = copy.deepcopy(row)
    mobile = Locres((tables.root / "Content/Locres/kh3_mobile.locres").read_bytes())
    mobile.reports(hints)
    mobile.item_labels(presentation_tokens, names, details)
    for placement in locations:
        if placement.get("source_table") == "LuckyMark" and placement.get("is_foreign_item"):
            n = int(placement["source_id"])
            text = f"You have photographed {n} lucky emblem{'s' if n != 1 else ''}!\rObtained {placement.get('item_display_name') or placement['item_name']}."
            for namespace in ("kh3_level.tt", "kh3_level.ex"):
                mobile.set(namespace, f"information_{n:04}", text)
    item_locres = Locres((tables.root / "Content/Localization/Game/en/kh3_item.locres").read_bytes())
    item_locres.item_labels(presentation_tokens, names, details)
    tables.files["KINGDOM HEARTS III/Content/Localization/Game/en/kh3_mobile.locres"] = mobile.encode()
    tables.files["KINGDOM HEARTS III/Content/Localization/Game/en/kh3_item.locres"] = item_locres.encode()
