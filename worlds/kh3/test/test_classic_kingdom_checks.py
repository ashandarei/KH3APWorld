from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root


import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from BaseClasses import CollectionState, ItemClassification
from Fill import balance_multiworld_progression, distribute_items_restrictive
from test.general import setup_multiworld
from worlds.AutoWorld import call_all

from .runtime_sources import cpp_function_body, read_composition_sources
from .. import KH3World
from ..Data import (
    CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
    CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
    CLASSIC_KINGDOM_APPENDED_NATIVE_ITEM_NAMES,
    CLASSIC_KINGDOM_GAME_IDS,
    CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND,
    CLASSIC_KINGDOM_HIGH_SCORE_SOURCE_TABLE,
    CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME,
    CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID,
    AP_ONLY_ITEM_ID_NAMES,
    load_extracted_data,
    resolve_item_runtime_token,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
    build_item_name_to_token,
    export_randomized_options_from_bridge_data,
)
from ..Options import ClassicKingdomChecks, KH3Options
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


def _classic_kingdom_locations(world) -> list[dict]:
    return [
        location
        for location in world.active_locations
        if location.get("classic_kingdom_check_kind")
        or location.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID
    ]


class TestClassicKingdomChecks(KH3WorldIntegrityMixin, KH3TestBase):
    def test_useful_game_receipts_preserve_progression_and_copy_independently(self):
        self.world_setup(seed=31)
        names = tuple(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
        state = self._state_without_precollected_items()
        progression = {player: counts.copy() for player, counts in state.prog_items.items()}
        for name in names:
            item = self.world.create_item(name)
            self.assertEqual(item.classification, ItemClassification.useful)
            self.assertIsNone(self.world.collect_item(state, item))
            self.assertFalse(state.collect(item))
        self.assertEqual(progression, state.prog_items)
        aggregate = self.multiworld.get_location(CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME, self.player)
        self.assertTrue(aggregate.can_reach(state))
        copied = state.copy()
        first = self.world.create_item(names[0])
        copied.remove(first)
        self.assertFalse(aggregate.can_reach(copied))
        self.assertTrue(aggregate.can_reach(state))
        copied.collect(first)
        self.assertTrue(aggregate.can_reach(copied))
        state.collect(first)
        state.remove(first)
        self.assertTrue(aggregate.can_reach(state))
        state.remove(first)
        self.assertFalse(aggregate.can_reach(state))

    def test_optional_game_rule_serializes_without_losing_its_receipt_semantics(self):
        from ..ClassicKingdomLogic import HasClassicKingdomGames
        self.world_setup(seed=31)
        names = tuple(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
        rule = HasClassicKingdomGames(*names)
        restored = self.world.rule_from_dict(rule.to_dict()).resolve(self.world)
        state = self._state_without_precollected_items()
        self.assertFalse(restored(state))
        for name in names:
            state.collect(self.world.create_item(name))
        self.assertTrue(restored(state))
        self.assertEqual(restored.item_names, tuple(sorted(names)))

    def test_default_is_off_but_keeps_existing_classic_tone_location(self) -> None:
        self.world_setup(seed=31)

        self.assertIn("classic_kingdom_checks", KH3Options.type_hints)
        option_type = KH3Options.type_hints["classic_kingdom_checks"]
        self.assertIs(option_type, ClassicKingdomChecks)
        self.assertEqual(option_type.display_name, "Classic Kingdom Checks")
        self.assertEqual(option_type.default, option_type.option_off)
        self.assertEqual(option_type.options["off"], 0)
        self.assertEqual(option_type.options["high_score"], 1)
        self.assertEqual(option_type.get_option_name(1), "High Score")

        locations = _classic_kingdom_locations(self.world)
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["name"], CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME)
        self.assertEqual(locations[0]["primary_item_name"], "Classic Tone")
        self.assertEqual(self.world.build_runtime_slot_data()["classic_kingdom_checks"], 0)
        self.assertEqual(
            sum(item.name == "Classic Tone" for item in self.multiworld.itempool if item.player == self.player),
            1,
        )
        for item_name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values():
            with self.subTest(item_name=item_name):
                self.assertEqual(
                    self.world.create_item(item_name).classification,
                    ItemClassification.useful,
                )

        aggregate = self.multiworld.get_location(
            CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
            self.player,
        )
        self.assertFalse(aggregate.can_reach(self._state_without_precollected_items()))

    def test_aggregate_requires_every_game_regardless_of_check_and_reward_options(self) -> None:
        unlock_names = tuple(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
        for check_mode in (0, 1):
            for junk_reward in (0, 1):
                with self.subTest(check_mode=check_mode, junk_reward=junk_reward):
                    self.options = {
                        "classic_kingdom_checks": check_mode,
                        "junk_classic_kingdom_reward": junk_reward,
                    }
                    self.world_setup(seed=37)
                    aggregate = self.multiworld.get_location(
                        CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME, self.player,
                    )
                    self.assertEqual(
                        Counter(item.name for item in self.multiworld.itempool
                                if item.player == self.player and item.name in unlock_names),
                        Counter(unlock_names),
                    )
                    self.assertTrue(set(unlock_names) <= self.world._required_logic_item_names())
                    self.assertFalse(aggregate.can_reach(self._state_without_precollected_items()))
                    expected_classification = (ItemClassification.progression if check_mode or not junk_reward
                                               else ItemClassification.useful)
                    for missing_game in unlock_names:
                        with self.subTest(missing_game=missing_game):
                            item = self.world.create_item(missing_game)
                            self.assertEqual(item.classification, expected_classification)
                            if junk_reward:
                                self.assertFalse(aggregate.item_rule(item))
                            state = self._state_without_precollected_items()
                            for item_name in unlock_names:
                                if item_name != missing_game:
                                    state.collect(self.world.create_item(item_name))
                            self.assertFalse(aggregate.can_reach(state))
                            state.collect(self.world.create_item(missing_game))
                            self.assertTrue(aggregate.can_reach(state))
                            state.remove(item)
                            self.assertFalse(aggregate.can_reach(state))

    def test_high_score_adds_exactly_23_optional_locations(self) -> None:
        self.options = {"classic_kingdom_checks": 1}
        self.world_setup(seed=41)

        locations = _classic_kingdom_locations(self.world)
        high_score_checks = [
            location
            for location in locations
            if location.get("classic_kingdom_check_kind") == CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND
        ]
        self.assertEqual(len(high_score_checks), 23)
        self.assertEqual(len(locations), 24)  # 23 optional + existing aggregate reward.
        self.assertEqual(
            {location["source_id"] for location in high_score_checks},
            set(CLASSIC_KINGDOM_GAME_IDS),
        )
        self.assertTrue(
            all(location["source_table"] == CLASSIC_KINGDOM_HIGH_SCORE_SOURCE_TABLE for location in high_score_checks)
        )

        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["classic_kingdom_checks"], 1)
        bridge_checks = [
            location for location in slot_data["bridge"]["locations"] if location.get("classic_kingdom_check_kind")
        ]
        self.assertEqual(len(bridge_checks), 23)
        self.assertEqual(
            {location["classic_kingdom_check_kind"] for location in bridge_checks},
            {CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND},
        )

        for item_name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values():
            with self.subTest(item_name=item_name):
                self.assertEqual(
                    self.world.create_item(item_name).classification,
                    ItemClassification.progression,
                )

        expected_unlock_counts = Counter(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
        self.assertEqual(
            Counter(
                item.name
                for item in self.multiworld.itempool
                if item.player == self.player and item.name in expected_unlock_counts
            ),
            expected_unlock_counts,
        )

    def test_conditional_unlock_classifications_survive_solo_and_multiworld_fill(self) -> None:
        unlock_names = set(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
        for check_mode in (0, 1):
            for junk_reward in (0, 1):
                for players in (1, 2):
                    with self.subTest(check_mode=check_mode, junk_reward=junk_reward, players=players):
                        options = [{
                            "classic_kingdom_checks": check_mode,
                            "junk_classic_kingdom_reward": junk_reward,
                        }]
                        if players == 2:
                            options.append({
                                "classic_kingdom_checks": 1 - check_mode,
                                "junk_classic_kingdom_reward": junk_reward,
                            })
                        multiworld = setup_multiworld([KH3World] * players, seed=67, options=options)
                        distribute_items_restrictive(multiworld)
                        call_all(multiworld, "post_fill")
                        if players == 2:
                            balance_multiworld_progression(multiworld)
                        call_all(multiworld, "finalize_multiworld")

                        self.assertFalse(multiworld.get_unfilled_locations())
                        self.assertTrue(multiworld.can_beat_game(CollectionState(multiworld)))
                        self.assertTrue(multiworld.fulfills_accessibility(CollectionState(multiworld)))
                        for player, player_options in enumerate(options, 1):
                            can_hold_progression = (
                                player_options["classic_kingdom_checks"]
                                or not player_options["junk_classic_kingdom_reward"]
                            )
                            expected = (ItemClassification.progression if can_hold_progression
                                        else ItemClassification.useful)
                            unlocks = [location.item for location in multiworld.get_filled_locations()
                                       if location.item.player == player and location.item.name in unlock_names]
                            self.assertEqual(Counter(item.name for item in unlocks), Counter(unlock_names))
                            self.assertTrue(all(item.classification == expected for item in unlocks))
                            if junk_reward:
                                aggregate = multiworld.get_location(CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME, player)
                                self.assertFalse(aggregate.item.advancement)
                                self.assertNotIn(aggregate.item.name, unlock_names)

    def test_built_in_game_unlocks_are_registered_as_native_items(self) -> None:
        self.world_setup(seed=43)
        exported_tokens = build_item_name_to_token(load_extracted_data())
        previous_published_tail = max(self.world.item_name_to_id[name] for name in AP_ONLY_ITEM_ID_NAMES)

        self.assertEqual(len(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID), 23)
        self.assertEqual(
            set(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID),
            set(CLASSIC_KINGDOM_GAME_IDS),
        )
        for game_id, item_name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.items():
            with self.subTest(game_id=game_id, item_name=item_name):
                self.assertIn(item_name, self.world.item_name_to_id)
                self.assertEqual(
                    CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME[item_name],
                    game_id,
                )
                self.assertEqual(resolve_item_runtime_token(item_name), game_id)
                self.assertEqual(exported_tokens[item_name], game_id)

        self.assertEqual(
            [self.world.item_name_to_id[item_name] for item_name in CLASSIC_KINGDOM_APPENDED_NATIVE_ITEM_NAMES],
            list(
                range(
                    previous_published_tail + 1,
                    previous_published_tail + 1 + len(CLASSIC_KINGDOM_APPENDED_NATIVE_ITEM_NAMES),
                )
            ),
        )
        self.assertEqual(
            self.world.create_item("Gummiphone").classification,
            ItemClassification.filler,
        )


    def test_logic_requires_each_native_game_unlock_without_gummiphone(self) -> None:
        self.options = {"classic_kingdom_checks": 1}
        self.world_setup(seed=53)
        state = self._state_without_precollected_items()
        built_in_game = self.multiworld.get_location(
            "Classic Kingdom - Mickey, The Mail Pilot - High Score",
            self.player,
        )
        collectible_game = self.multiworld.get_location(
            "Classic Kingdom - Giantland - High Score",
            self.player,
        )
        aggregate = self.multiworld.get_location(
            CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
            self.player,
        )

        self.assertFalse(built_in_game.can_reach(state))
        self.assertFalse(collectible_game.can_reach(state))
        state.collect(self.world.create_item("Gummiphone"))
        self.assertFalse(built_in_game.can_reach(state))
        self.assertFalse(collectible_game.can_reach(state))
        state.collect(self.world.create_item("CK: Mickey, The Mail Pilot"))
        self.assertTrue(built_in_game.can_reach(state))
        self.assertFalse(collectible_game.can_reach(state))
        state.collect(self.world.create_item("CK: Giantland"))
        self.assertTrue(collectible_game.can_reach(state))
        self.assertFalse(aggregate.can_reach(state))
        for item_name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values():
            if item_name not in {"CK: Giantland", "CK: Mickey, The Mail Pilot"}:
                state.collect(self.world.create_item(item_name))
        self.assertTrue(aggregate.can_reach(state))

    def test_junk_classic_kingdom_reward_rejects_progression_at_aggregate(self) -> None:
        self.world_setup(seed=59)
        aggregate = self.multiworld.get_location(
            CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
            self.player,
        )
        progression_item = self.world.create_item("Proof of Promises")
        self.assertFalse(aggregate.item_rule(progression_item))
        self.assertFalse(aggregate.item_rule(self.world.create_item("CK: Mickey, The Mail Pilot")))
        self.assertTrue(aggregate.item_rule(self.world.create_item("Potion")))

    def test_non_junk_classic_kingdom_reward_allows_progression_at_aggregate(self) -> None:
        self.options = {"junk_classic_kingdom_reward": 0}
        self.world_setup(seed=61)
        aggregate = self.multiworld.get_location(
            CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
            self.player,
        )
        unlock_item = self.world.create_item("CK: Mickey, The Mail Pilot")
        self.assertEqual(unlock_item.classification, ItemClassification.progression)
        self.assertTrue(aggregate.item_rule(unlock_item))

        state = self._state_without_precollected_items()
        self.assertFalse(aggregate.can_reach(state))
        for item_name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values():
            state.collect(self.world.create_item(item_name))
        self.assertTrue(aggregate.can_reach(state))

    def test_export_suppresses_native_final_reward_and_bridge_delivers_items(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {event_id: {"RandomizedItem": "ITEM_POTION\0"} for event_id in ALWAYS_ON_EVENT_IDS}
            event_table[CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID] = {"Reward": "WEP_KEYBLADE_SO_012\0"}
            default_path.write_text(json.dumps({"Event": event_table}), encoding="utf-8")
            extracted_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {"name": "Potion", "id": "ITEM_POTION"},
                            {"name": "Kingdom Key", "id": "WEP_KEYBLADE_SO_00"},
                        ],
                        "locations": [],
                    }
                ),
                encoding="utf-8",
            )
            bridge_locations = [
                {
                    "ap_location_id": 1263023439,
                    "ap_location_name": "Classic Kingdom - Mickey Steps Out - High Score",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Potion",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "ClassicKingdomHighScore:LSIGAME23",
                    "source_id": "LSIGAME23",
                    "source_table": CLASSIC_KINGDOM_HIGH_SCORE_SOURCE_TABLE,
                    "type": "classic_kingdom",
                },
                {
                    "ap_location_id": 1263023440,
                    "ap_location_name": CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Kingdom Key",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": f"Event:{CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID}",
                    "source_id": CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
                    "source_table": "Event",
                    "subtype": "Reward",
                    "type": "event",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["Event"][CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID]["Reward"],
                f"{SUPPRESSED_ALWAYS_ON_EVENT_TOKEN}\0",
            )
            placements = {location["bridge_key"]: location for location in result.placement_summary["locations"]}
            self.assertTrue(placements["ClassicKingdomHighScore:LSIGAME23"]["bridge_delivery_required"])
            aggregate = placements[f"Event:{CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID}"]
            self.assertTrue(aggregate["bridge_delivery_required"])
            self.assertEqual(aggregate["runtime_token"], "WEP_KEYBLADE_SO_00")

    def test_runtime_is_targeted_and_event_driven(self) -> None:
        runtime_root = native_project_root() / "KH3APProbeCpp" / "kh3ap" / "features" / "story" / "minigames"
        header = (runtime_root / "classic_kingdom_completion.h").read_text(encoding="utf-8")
        service = (runtime_root / "classic_kingdom_completion_service.cpp").read_text(encoding="utf-8")

        self.assertEqual(header.count("OnUpdatedScore_"), 23)
        self.assertEqual(header.count("/Script/TresGame.TresLSIGameActor:"), 23)
        self.assertNotIn("TresLSIGameActorMain", header)
        self.assertNotIn("ReturnToTitleGameClear", header + service)
        self.assertIn("UObjectGlobals::RegisterHook", service)
        self.assertIn("persist_high_score", service)
        self.assertNotIn("mark_played", header + service)
        self.assertNotIn("played_games", header + service)
        self.assertIn("kh3ap.runtime_classic_kingdom_state.v3", service)
        for forbidden in (
            "IsReal",
            "StaticFindObject",
            "GetAllActors",
            "ForEachUObject",
            "UObjectArray",
            "FindAllOf",
            "tick()",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, service)

    def test_runtime_grants_wait_for_the_next_playable_context_event(self) -> None:
        cpp_root = native_project_root() / "KH3APProbeCpp" / "kh3ap"
        story_root = cpp_root / "features" / "story" / "minigames"
        grants_root = cpp_root / "features" / "grants"
        service_header = (story_root / "classic_kingdom_completion_service.h").read_text(encoding="utf-8")
        service_source = (story_root / "classic_kingdom_completion_service.cpp").read_text(encoding="utf-8")
        state_source = (grants_root / "grant_state.cpp").read_text(encoding="utf-8")
        safety_source = (grants_root / "grant_safety.cpp").read_text(encoding="utf-8")
        context_source = (grants_root / "delivery" / "item_delivery_context.cpp").read_text(encoding="utf-8")
        execution_source = (grants_root / "delivery" / "item_delivery_execution.cpp").read_text(encoding="utf-8")
        player_context_source = (cpp_root / "features" / "player" / "context" / "player_context_readiness.cpp").read_text(
            encoding="utf-8"
        )
        composition_source = read_composition_sources()
        native_hook_source = (cpp_root / "hooks" / "hook_service.cpp").read_text(encoding="utf-8")

        self.assertIn("runtime_grant_transition_started", service_header)
        callback_index = service_source.index("config.runtime_grant_transition_started(")
        persist_index = service_source.index(
            "persist_high_score(entry->game_index)",
            callback_index,
        )
        self.assertLess(callback_index, persist_index)

        self.assertIn("mark_classic_kingdom_transition_waiting", state_source)
        self.assertIn("clear_classic_kingdom_transition_gate", state_source)
        self.assertIn(
            "classic_kingdom_waiting_for_playable_context",
            safety_source,
        )
        self.assertEqual(
            context_source.count("current_runtime_classic_kingdom_transition_gate_status("),
            1,
        )
        self.assertIn("prepare_delivery_context(deps, policy, true, now)", execution_source)
        self.assertIn("prepare_delivery_context(deps, policy, false, now)", execution_source)
        self.assertNotIn("playable_context_observed", player_context_source)
        self.assertRegex(player_context_source, r"\.clear_classic_kingdom_transition_gate\(\s*playable_context_source,")
        ready_event_path = cpp_function_body(player_context_source, "process_pending_grant_from_runtime_ready_event")
        self.assertIn("RuntimeReadyEvent:{}", ready_event_path)
        self.assertIn("process_pending_grant_now", ready_event_path)
        for forbidden in (
            "FindAllOf",
            "UObject::IsReal",
            "StaticFindObject",
            "GetAllActors",
            "ForEachUObject",
        ):
            with self.subTest(ui_release_forbidden=forbidden):
                self.assertNotIn(forbidden, ready_event_path)

        map_release_start = native_hook_source.index('STR("RuntimeControlReady:{}")')
        map_release_end = native_hook_source.index(
            "config.battle_level_sync.sync_observed(source);",
            map_release_start,
        )
        map_release_path = native_hook_source[map_release_start:map_release_end]
        self.assertIn("clear_classic_kingdom_transition_gate", map_release_path)
        map_process_index = native_hook_source.index(
            "config.player_context.process_pending_grant_from_runtime_ready_event(",
            map_release_end,
        )
        self.assertLess(map_release_end, map_process_index)

        transition_callbacks = composition_source.split(".runtime_grant_transition_started")[1:]
        self.assertEqual(len(transition_callbacks), 2)
        event_gate_wiring = ready_event_path + map_release_path + "".join(
            callback.split("},", 1)[0] for callback in transition_callbacks
        )
        for forbidden in (
            "FindAllOf",
            "UObject::IsReal",
            "StaticFindObject",
            "GetAllActors",
            "ForEachUObject",
            "tick()",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, event_gate_wiring)
