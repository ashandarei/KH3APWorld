from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root


import json
import re
import unittest
from collections import Counter
from pathlib import Path

from BaseClasses import ItemClassification

from .runtime_sources import read_composition_sources
from ..Data import (
    DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS,
    KEYBLADE_STYLE_ITEM_NAMES,
    PARTY_MEMBER_DEFINITIONS,
    PARTY_MEMBER_UNLOCK_ITEM_NAMES,
    PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_PREFIX,
    build_item_name_to_id,
    classify_runtime_grant_method,
    load_extracted_data,
    resolve_item_runtime_token,
)
from ..export_ap_kh3_randomized_options import bridge_only_item_runtime_token
from ..Items import classify_item
from ..Minigames import MINIGAME_APPENDED_ITEMS
from ..Space import SPACE_ITEM_IDS
from . import KH3TestBase

REPO_ROOT = Path(__file__).resolve().parents[3]
KH3_ROOT = Path(__file__).resolve().parents[1]
CPP_ROOT = native_project_root() / "KH3APProbeCpp"
PARTY_ROOT = CPP_ROOT / "kh3ap" / "features" / "party"
PARTY_SERVICE = PARTY_ROOT / "party_member_service.cpp"
GOA_MENU = CPP_ROOT / "kh3ap" / "features" / "ui" / "goa_randomization_menu.cpp"


def party_sources(*names: str) -> str:
    paths = [PARTY_ROOT / name for name in names] if names else [
        path for path in sorted(PARTY_ROOT.iterdir())
        if path.suffix in {".h", ".cpp"} and not path.name.startswith("party_test_service")
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def party_function(filename: str, name: str) -> str:
    """Read a production function independently of source-file method ordering."""
    source = party_sources(filename)
    start = source.index(f"auto {name}(")
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class TestPartyMemberCatalog(unittest.TestCase):
    def test_catalog_matches_supported_party_members(self) -> None:
        default_party_members = json.loads(
            (KH3_ROOT / "randomizer/wwwroot/DefaultPartyMembers.json").read_text(encoding="utf-8")
        )
        definitions = {
            row_id: (display_name, npc_id)
            for row_id, display_name, npc_id in PARTY_MEMBER_DEFINITIONS
        }

        self.assertEqual(
            {row_id: npc_id for row_id, (_, npc_id) in definitions.items() if row_id in default_party_members},
            {row_id: npc_id.rstrip("\0") for row_id, npc_id in default_party_members.items()},
        )
        self.assertEqual(set(definitions) - set(default_party_members), {"PartyMember_017", "PartyMember_018"})
        self.assertEqual(len(definitions), 26)
        self.assertEqual(definitions["PartyMember_010"], ("Marshmallow", "n_fz214"))
        self.assertNotIn("PartyMember_021", definitions)
        self.assertEqual(definitions["PartyMember_017"], ("Lea", "n_ex010"))
        self.assertEqual(definitions["PartyMember_018"], ("Kairi", "n_ex005"))

        self.assertEqual(default_party_members["PartyMember_010"], "n_fz214\0")

    def test_randomized_party_members_are_useful_native_ap_unlock_items(self) -> None:
        self.assertEqual(len(PARTY_MEMBER_UNLOCK_ITEM_NAMES), 18)
        self.assertIn("Party Member: Marshmallow", PARTY_MEMBER_UNLOCK_ITEM_NAMES)
        for row_id, display_name, npc_id in PARTY_MEMBER_DEFINITIONS:
            item_name = f"Party Member: {display_name}"
            token = f"{PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_PREFIX}{row_id}"
            with self.subTest(item_name=item_name):
                if row_id in DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS:
                    self.assertNotIn(item_name, PARTY_MEMBER_UNLOCK_ITEM_NAMES)
                    self.assertIsNone(resolve_item_runtime_token(item_name))
                    self.assertIsNone(bridge_only_item_runtime_token(item_name))
                    continue
                self.assertIn(item_name, PARTY_MEMBER_UNLOCK_ITEM_NAMES)
                self.assertEqual(classify_item(item_name), ItemClassification.useful)
                self.assertEqual(resolve_item_runtime_token(item_name), token)
                self.assertEqual(bridge_only_item_runtime_token(item_name), token)
                self.assertEqual(classify_runtime_grant_method(token), "party_member_unlock")

    def test_party_member_ids_retain_published_positions(self) -> None:
        item_ids = build_item_name_to_id(load_extracted_data())
        party_ids = [item_ids[name] for name in PARTY_MEMBER_UNLOCK_ITEM_NAMES]
        self.assertGreater(item_ids["Unlock: Links"], max(party_ids))
        self.assertGreater(min(SPACE_ITEM_IDS.values()), item_ids["Unlock: Links"])
        self.assertGreater(min(item_ids[name] for name in KEYBLADE_STYLE_ITEM_NAMES), max(party_ids))
        self.assertGreater(
            min(item_ids[name] for name in MINIGAME_APPENDED_ITEMS),
            max(item_ids[name] for name in KEYBLADE_STYLE_ITEM_NAMES),
        )
        expected_ids = [
            1263022605 + offset
            for offset in range(26)
            if offset not in {0, 1, 20, 21, 22, 23, 24, 25}
        ]
        self.assertEqual(party_ids, expected_ids)
        self.assertEqual(item_ids["Party Member: Marshmallow"], 1263022614)
        for row_id, display_name, _ in PARTY_MEMBER_DEFINITIONS:
            if row_id in DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS:
                self.assertNotIn(f"Party Member: {display_name}", item_ids)

    def test_runtime_replaces_the_animation_notify_actor_route(self) -> None:
        service = party_sources()
        menu = GOA_MENU.read_text(encoding="utf-8")
        apply_helper = (
            CPP_ROOT
            / "kh3ap"
            / "features"
            / "grants"
            / "delivery"
            / "item_grant_apply_helpers.cpp"
        ).read_text(encoding="utf-8")

        for required in (
            "/Script/TresGame.TresFriendComponent:SetFriendAll",
            "/Script/TresGame.TresFriendComponent:SetFriend",
            "/Script/TresGame.TresFriendComponent:DeleteFriend",
            "FWeakObjectPtr current_friend_manager_",
            "is_uobject_valid_fast(",
            "KH3AP_PARTY_MEMBER_UNLOCK::",
            "installed_pak_seed_settings()",
            ".available = active_",
            "std::array<RuntimePartyMemberDefinition, 28>",
            'L"PartyMember_017"',
            'L"PartyMember_018"',
            "set_reroll_every_room(bool enabled)",
            "settings.party_randomization_enabled &&",
            "settings.party_reroll_every_room",
        ):
            with self.subTest(required=required):
                self.assertIn(required, service)
        self.assertNotIn("A_GoA_PartyReplacement", service)
        self.assertIn('L"Marshmallow"', service)
        self.assertIn('L"PartyMember_010"', service)
        self.assertIn("runtime_party_is_character_disabled", service)
        self.assertNotIn("Debug", service)
        self.assertIn('request.grant_method == STR("party_member_unlock")', apply_helper)
        self.assertIn("deps.party_member_unlock(", apply_helper)
        self.assertIn("maybe_show_party_member_unlock_information(", apply_helper)
        self.assertIn('STR("New friend unlocked: {}")', apply_helper)
        self.assertIn("RuntimeGrantTokens::effective_show_info(request)", apply_helper)
        dispatch = apply_helper.split(
            'request.grant_method == STR("party_member_unlock")', 1
        )[1].split('request.grant_method == STR("keyblade_style")', 1)[0]
        self.assertIn("apply_party_unlock(request, world_context_object, deps, candidate_failure)", dispatch)
        party_apply = apply_helper.split(
            "apply_party_unlock(const RuntimeItemGrantRequest& request,", 1
        )[1].split("return applied;", 1)[0]
        self.assertLess(
            party_apply.index("deps.party_member_unlock("),
            party_apply.index("maybe_show_party_member_unlock_information("),
        )

        self.assertIn('STR("Randomize Party Members")', menu)
        self.assertIn('STR("Reroll Party Every Room")', menu)
        self.assertIn("party_members.set_reroll_every_room(enabled)", menu)
        self.assertIn('STR("Friend 1")', menu)
        self.assertIn('STR("Friend 2")', menu)
        self.assertIn("status.available &&", menu)
        self.assertIn("!status.randomization_enabled", menu)
        self.assertIn("party_members.choice_available(", menu)
        self.assertNotIn("reroll_every_room_", service)

    def test_all_donald_and_goofy_forms_are_runtime_unlocked_without_items(self) -> None:
        catalog = party_sources("party_catalog.h")

        default_choices = re.search(
            r"default_unlocked_choices\{\{([\s\d,]+)\}\}", catalog
        )
        self.assertIsNotNone(default_choices)
        choices = [int(value) for value in re.findall(r"\d+", default_choices[1])]
        roster = catalog.split("party_choices{{", 1)[1].split("}};", 1)[0]
        row_ids = re.findall(r'\{\s*L"([^"]*)",\s*L"', roster)
        self.assertEqual(len(choices), 8)
        self.assertEqual(
            {row_ids[choice] for choice in choices},
            DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS,
        )
        install = party_function("party_member_service.cpp", "RuntimePartyMemberService::install_once")
        self.assertIn(
            "for (const std::size_t choice : default_unlocked_choices)",
            install,
        )
        self.assertIn("(void)unlock_choice(choice);", install)
        self.assertLess(
            install.index("(void)unlock_choice(choice);"),
            install.index("(void)refresh_unlocks_from_bridge();"),
        )

    def test_goa_entry_queues_current_sora_without_destructive_spawn_retry(self) -> None:
        service = party_sources("party_reconciliation.cpp")
        entry = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::observe_goa_map_ready")
        self.assertIn("pawn_state.first_seen_at() + RuntimeGrantPawnState::stability_delay", entry)
        self.assertLess(entry.index("runtime_party_requires_entry_reconcile"), entry.index("request_reconcile("))
        self.assertIn("goa_entry_pawn_.Get() == pawn", entry)
        resolve = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::resolve_friend_manager")
        self.assertNotIn("current_friend_manager_.Get()", resolve)
        self.assertIn('STR("MyFrdMgr")', resolve)
        finish = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::finish_reconcile")
        self.assertLess(finish.index("friend_manager != resolve_friend_manager()"), finish.index("set_friend_path"))
        self.assertLess(finish.index('STR("m_pFriendPawn")'), finish.index("active_choices_ = pending_choices_"))
        self.assertIn("persistent_level_for(party_pawn) != level", finish)
        verification = finish.split('STR("m_pFriendPawn")', 1)[1].split(
            "apply_custom_party_ai_for_friend_choice(choice);", 1)[0]
        self.assertNotIn("return false", verification)
        self.assertIn("no_respawn=true", verification)
        self.assertIn("return accepted && *accepted;", service)
        tick = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::tick")
        self.assertNotIn("observe_goa_map_ready", tick)
        self.assertNotIn("pawn_state", tick)
        failure = tick.split("if (!completed)", 1)[1]
        self.assertNotIn("next_reconcile_attempt_ =", failure)
        self.assertIn("reconcile_pending_ = false;", failure)
        self.assertNotIn("next_goa_entry_check_", service)
        hooks = (native_project_root() / 'KH3APProbeCpp/kh3ap/hooks/hook_service.cpp').read_text(encoding="utf-8")
        composition = read_composition_sources()
        self.assertIn("config.goa_party_map_ready();", hooks)
        self.assertIn("m_party_members.observe_goa_map_ready();", composition)

    def test_ap_unlocks_do_not_reconcile_the_live_party(self) -> None:
        apply_unlock = party_function("party_member_service.cpp", "RuntimePartyMemberService::apply_unlock_item")
        refresh_unlocks = party_function("party_member_service.cpp", "RuntimePartyMemberService::refresh_unlocks_from_bridge")

        self.assertNotIn("request_reconcile", apply_unlock)
        self.assertNotIn("random_choices_valid_ = false", apply_unlock)
        self.assertNotIn("request_reconcile", refresh_unlocks)
        self.assertNotIn("random_choices_valid_ = false", refresh_unlocks)
        self.assertIn("Runtime party member unlocked", apply_unlock)

    def test_party_first_use_does_not_search_all_objects_for_owned_functions_or_assets(self) -> None:
        # Check each owner directly; a warm runtime cache can hide global scans.
        for filename, name in (
            ("party_reconciliation.cpp", "invoke_friend_function"),
            ("party_helpers.cpp", "run_behavior_tree"),
            ("party_helpers.cpp", "invoke_bool_getter"),
            ("party_reconciliation.cpp", "RuntimePartyMemberService::begin_reconcile"),
            ("party_ai.cpp", "RuntimePartyMemberService::reconcile_action_viable_state_patches"),
            ("party_ai.cpp", "RuntimePartyMemberService::apply_custom_party_ai_for_possession"),
        ):
            with self.subTest(function=name):
                method = party_function(filename, name)
                self.assertNotIn("StaticFindObject", method)
                self.assertNotIn("ForEachUObject", method)
                self.assertNotIn("FindAllOf", method)

        loader = (CPP_ROOT / "kh3ap/core/objects/asset_load_helpers.cpp").read_text(encoding="utf-8")
        resolution = loader.split("auto resolve_loaded_soft_reference(", 1)[1].split(
            "auto request_runtime_asset_load(", 1)[0]
        # Conversion must remain nonloading, and partially loaded objects
        # must never pass the readiness check just because they exist.
        self.assertIn("Conv_SoftClassReferenceToClass", resolution)
        self.assertIn("Conv_SoftObjectReferenceToObject", resolution)
        self.assertNotIn("LoadAsset", resolution)
        self.assertNotIn("request_runtime_asset", resolution)
        for flag in ("RF_NeedLoad", "RF_NeedPostLoad", "RF_NeedPostLoadSubobjects"):
            self.assertIn(flag, resolution)

    def test_party_preload_failure_cannot_delete_or_force_spawn(self) -> None:
        service = party_sources("party_reconciliation.cpp")
        begin = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::begin_reconcile")
        ready = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::party_assets_ready")
        tick = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::tick")
        self.assertNotIn("delete_friend_path", begin)
        self.assertNotIn("native_spawn_fallback=true", service)
        failure = begin.split("Party preload unavailable", 1)[1].split("uint32_t requested", 1)[0]
        self.assertIn("reconcile_pending_ = false", failure)
        self.assertIn("return false;", failure)
        timeout = ready.split("Party preload deadline reached", 1)[1]
        self.assertIn("reconcile_pending_ = false", timeout)
        self.assertIn("return false;", timeout)
        self.assertNotIn("path.clear()", ready)
        self.assertIn("now < party_asset_deadline_", ready)
        self.assertIn("next_party_asset_request_ = now + party_asset_request_interval", ready)
        self.assertIn("++party_asset_request_sequence_", ready)
        self.assertIn("klass->ForEachFunctionInChain()", ready)
        self.assertNotIn("klass->GetFunctionByNameInChain(", ready)
        self.assertLess(tick.index("if (!party_assets_ready())"), tick.index("delete_current_party()"))
        self.assertLess(tick.index("if (!party_assets_ready())"), tick.index("finish_reconcile()"))
        self.assertLess(tick.index("reconcile_friend_manager_.Get() != resolve_friend_manager()"),
                        tick.index("if (!party_assets_ready())"))

    def test_runtime_assigns_scoped_clean_party_trees(self) -> None:
        service = party_sources()
        tick = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::tick")
        assignment = party_function("party_ai.cpp", "RuntimePartyMemberService::apply_custom_party_ai_for_possession")
        reconcile = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::begin_reconcile")
        self.assertNotIn("write_roxas_ai_diagnostic(now)", tick)
        self.assertNotIn("maintain_custom_party_ai(now)", tick)
        self.assertNotIn("synchronize_party_battle_presentation(now)", tick)
        self.assertIn('L"/Script/Engine.Controller:Possess"', service)
        self.assertIn("observe_controller_possess(context.Context)", service)
        self.assertIn("apply_custom_party_ai_for_possession(controller, pawn)", service)
        self.assertIn("apply_custom_party_ai_for_friend_choice(choice)", service)
        self.assertIn("SetFriend is the authoritative party-spawn event", service)
        self.assertIn("SetFriend event could not resolve ", service)
        self.assertIn("custom party pawn character=", service)
        self.assertIn("Party event assigned clean party AI", service)
        self.assertIn("invoke_set_disable_battle_mode_ai(pawn, false)", service)
        self.assertIn("BT_GoA_RoxasParty.BT_GoA_RoxasParty", service)
        self.assertIn("BT_GoA_KairiParty.BT_GoA_KairiParty", service)
        self.assertNotIn("load_runtime_asset_blocking(", assignment)
        self.assertEqual(reconcile.count("request_runtime_asset_load("), 1)
        self.assertIn("custom_party_asset_settle_ticks", reconcile)
        self.assertIn("settle_ticks_ = requested_asset_settle_ticks", reconcile)
        self.assertIn("requested_asset_settle_ticks = friend_settle_ticks", reconcile)
        self.assertIn("definition.tree_path", assignment)
        self.assertNotIn("FindAllOf", assignment)
        self.assertNotIn("steady_clock", assignment)
        self.assertNotIn("load_runtime_asset_blocking(definition.class_path)", service)
        self.assertIn("custom_party_tree_resolution_attempted_", service)
        self.assertIn("SetRootSet()", service)
        self.assertIn("run_behavior_tree(controller, behavior_tree)", service)
        self.assertIn("persistent_level_for(pawn) != sora_level", service)
        self.assertIn("is_party_presentation_story_level(sora_level)", service)
        self.assertNotIn("apply_roxas_ai_patch(now)", tick)
        self.assertNotIn("ensure_roxas_battle_ai(now)", tick)
        self.assertNotIn("update_roxas_follow(now)", tick)

    def test_roxas_and_kairi_presentation_tracks_the_cooked_bt_battle_gate(self) -> None:
        service = party_sources()

        for required in (
            "constexpr std::size_t kairi_choice = 17",
            "constexpr std::size_t roxas_choice = 18",
            'L"IsBattleMode"',
            'L"SetBattleMode"',
            'L"OnChangeBattleMode"',
            "GetFunctionByNameInChain(owned_name.c_str())",
            "invoke_bool_getter(pawn, can_battle_path)",
            "invoke_named_single_bool_function(pawn, set_battle_mode_function_name",
            "party_presentation_modes_",
            "runtime_party_map_policy(full_name)",
            "observe_can_battle(context.Context, result != 0)",
            "CanBattle event synced party ",
            "presentation slot=",
        ):
            with self.subTest(required=required):
                self.assertIn(required, service)

        synchronization = party_function("party_ai.cpp", "RuntimePartyMemberService::observe_can_battle")
        self.assertNotIn("set_weapon_visible_function_name,", synchronization)
        self.assertNotIn("IsReal", synchronization)


    def test_knockout_floor_fix_is_scoped_to_selected_lea_friend(self) -> None:
        body = party_function("party_ai.cpp", "RuntimePartyMemberService::initialize_party_knockout")
        for guard in (
            "RuntimePartyMapPolicy::managed", "read_friend_slots(manager, slots)",
            "active_choices_[slot] != lea_choice", "is_uobject_valid_fast(pawn)",
            "persistent_level_for(pawn) != level", 'STR("n_ex010_Pawn_C")',
        ):
            self.assertIn(guard, body)
        self.assertIn('STR("SetConditionHpLimitOne"), false', body)
        self.assertIn('STR("IsConditionHpLimitOne")) != false', body)
        self.assertNotIn("SetHitPoint", body)
        self.assertNotIn("DefaultObject", body)
        finish = party_function("party_reconciliation.cpp", "RuntimePartyMemberService::finish_reconcile")
        self.assertLess(finish.index("active_choices_ = pending_choices_"),
                        finish.index("initialize_party_knockout();"))

    def test_custom_party_ai_runtime_paths_and_states(self) -> None:
        runtime = party_sources("party_catalog.h", "party_ai.cpp", "party_runtime_constants.h")
        self.assertIn("state_id_idle = 1", runtime)
        self.assertIn("state_id_turn = 44", runtime)
        self.assertIn("patch.original_values", runtime)
        self.assertIn("states->Empty", runtime)
        specs = {
            "Ventus": {
                "asset": "BT_GoA_VentusParty",
                "category": "ventus_party_ai",
                "npc": "n_ex006",
                "actions": (
                    "Attack10_LandCombo",
                    "Attack11_AirCombo",
                    "Attack12_WindRaid",
                ),
            },
            "Lea": {
                "asset": "BT_GoA_LeaParty",
                "category": "lea_party_ai",
                "npc": "n_ex010",
                "actions": (
                    "Attack1_LandCombo",
                    "Attack2_FigaCutter",
                    "Attack3_FigaRaid",
                    "Attack5_FigaSlicer",
                ),
            },
            "Roxas": {
                "asset": "BT_GoA_RoxasParty",
                "category": "roxas_party_ai",
                "npc": "n_ex009",
                "actions": (
                    "Attack1_XCutDown",
                    "Attack2_LandCombo",
                    "Attack3_CrossShot",
                ),
            },
            "Kairi": {
                "asset": "BT_GoA_KairiParty",
                "category": "kairi_party_ai",
                "npc": "n_ex005",
                "actions": (
                    "Attack1_LandCombo",
                    "Attack2_SpiralBloom",
                    "Attack3_FlowerShoot",
                ),
            },
        }

        for character, spec in specs.items():
            with self.subTest(character=character):
                self.assertIn(f"{spec['asset']}.{spec['asset']}", runtime)
                for action in spec["actions"]:
                    self.assertIn(f"{spec['npc']}_{action}.{spec['npc']}_{action}_C", runtime)

    def test_seed_builder_emits_party_randomization_settings(self) -> None:
        from ..seedgen.remaps import add_runtime_settings
        metadata = {}
        add_runtime_settings(KH3_ROOT / "randomizer", "party-contract-test", {"pools":{"Party Members":"Randomize"}, "exceptions":{"Party Chaos":True}}, metadata)
        self.assertTrue(metadata["party_randomization_initially_enabled"])
        self.assertTrue(metadata["party_randomize_on_room_change"])



class TestPartyMemberItemPool(KH3TestBase):
    def test_all_party_members_are_available_once(self) -> None:
        self.world_setup()
        counts = Counter(
            item.name
            for item in self.multiworld.itempool
            + self.multiworld.precollected_items[self.player]
        )

        self.assertEqual(
            {name: counts[name] for name in PARTY_MEMBER_UNLOCK_ITEM_NAMES},
            {name: 1 for name in PARTY_MEMBER_UNLOCK_ITEM_NAMES},
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_default_party_forms_are_not_ap_items(self) -> None:
        self.world_setup()
        precollected = Counter(
            item.name for item in self.multiworld.precollected_items[self.player]
        )
        pool = Counter(item.name for item in self.multiworld.itempool)

        starting_members = set()
        for character in ("Donald", "Goofy"):
            for suffix in ("", " (Caribbean)", " (Monstropolis)", " (Toy Box)"):
                name = f"Party Member: {character}{suffix}"
                starting_members.add(name)
                with self.subTest(item_name=name):
                    self.assertEqual(precollected[name], 0)
                    self.assertEqual(pool[name], 0)
                    self.assertNotIn(name, self.world.kh3_starting_inventory_items)
                    self.assertNotIn(name, self.world.item_name_to_id)
                    self.assertNotIn(name, self.world.item_name_groups["Party Member Unlocks"])
        for name in set(PARTY_MEMBER_UNLOCK_ITEM_NAMES) - starting_members:
            with self.subTest(item_name=name):
                self.assertEqual(pool[name], 1)
                self.assertEqual(precollected[name], 0)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))


if __name__ == "__main__":
    unittest.main()
