from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .BridgeSession import KH3BridgeSession


def _normalize_bridge_dir(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def command_status(bridge_dir: Path) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    status = session.build_status()
    print(f"Bridge directory: {status['bridge_dir']}")
    print(f"Protocol: {status['protocol_name']} v{status['protocol_version']}")
    print(f"Tracked KH3 checks: {status['tracked_checks']}")
    print(f"Reported checks: {status['reported_checks']}")
    print(f"Received AP items: {status['received_item_count']}")
    print(f"Pending AP items: {status['pending_ap_item_count']}")
    print(f"Applied AP items: {status.get('applied_ap_item_count', 0)}")
    print(f"Last applied AP item index: {status['last_applied_item_index']}")
    ap_status = status["ap_status"]
    if ap_status:
        print(f"AP connected: {ap_status.get('connected')}")
        print(f"AP checked location count: {ap_status.get('checked_location_count')}")
        print(f"Goal complete: {status['game_state'].get('goal_complete')}")
    print_status_summary(status)
    return 0


def command_check(bridge_dir: Path, tokens: list[str]) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    added = session.add_checks(tokens)
    print(f"Added {added} KH3 checks.")
    return 0


def command_goal(bridge_dir: Path, complete: bool) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    session.set_goal_complete(complete)
    print(f"Set goal_complete to {complete}.")
    return 0


def command_clear(bridge_dir: Path) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    session.clear_game_state()
    print("Reset KH3 game_state.json.")
    return 0


def command_items(bridge_dir: Path) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    pending_items = session.list_pending_items()
    if not pending_items:
        print("No pending AP items are available.")
        return 0
    for item in pending_items:
        print(
            f"{item['index']} | {item['item_name']} | from={item['from_player_name']} | "
            f"location={item['location_name']}"
        )
    return 0


def command_item_plan(bridge_dir: Path) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    print_item_consumer_plan(session.load_item_consumer_plan())
    return 0


def command_next_item(bridge_dir: Path) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    action = session.get_next_pending_action()
    if action is None:
        print("No pending KH3 item action is available.")
        return 0
    print_pending_action(action)
    return 0










def command_list(bridge_dir: Path, world_name: str | None) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    for location in session.list_locations(world_name):
        print(f"{location['bridge_key']} | {location['ap_location_name']}")
    return 0


def command_candidates(bridge_dir: Path, hint_text: str | None) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    analyses = session.list_treasure_candidate_analyses()
    if not analyses:
        print("No KH3 treasure candidate analyses are available.")
        return 0
    for analysis in analyses:
        print_candidate_analysis(analysis, hint_text=hint_text)
    return 0


def _hint_tokens(hint_text: str | None) -> list[str]:
    if not hint_text:
        return []
    return [token for token in re.findall(r"[a-z0-9]+", hint_text.casefold()) if len(token) >= 3]


def _apply_hint_rerank(candidates: list[dict[str, object]], hint_text: str | None) -> list[dict[str, object]]:
    tokens = _hint_tokens(hint_text)
    if not tokens:
        return list(candidates)

    reranked: list[dict[str, object]] = []
    phrase = " ".join(tokens)
    for candidate in candidates:
        score = int(candidate.get("score", 0))
        reasons = list(candidate.get("reasons", []))
        description = str(candidate.get("location_description") or "")
        name = str(candidate.get("name") or "")
        vanilla_item_name = str(candidate.get("vanilla_item_name") or "")
        haystack = f"{name} {description} {vanilla_item_name}".casefold()
        matched_tokens = [token for token in tokens if token in haystack]
        if phrase and phrase in haystack:
            score += 20
            reasons.append(f"review hint phrase matches location text: {phrase}")
        elif matched_tokens:
            score += 8 * len(matched_tokens)
            reasons.append(f"review hint tokens match location/item text: {', '.join(matched_tokens)}")

        chest_size = str(candidate.get("chest_size") or "")
        if "small" in tokens and chest_size == "small":
            score += 15
            reasons.append("review hint prefers small chest")
        if "large" in tokens and chest_size == "large":
            score += 15
            reasons.append("review hint prefers large chest")

        reranked_candidate = dict(candidate)
        reranked_candidate["score"] = score
        reranked_candidate["reasons"] = reasons
        reranked.append(reranked_candidate)

    reranked.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("name", ""))))
    return reranked


def print_candidate_analysis(analysis: dict[str, object], limit: int = 5, hint_text: str | None = None) -> None:
    recommendation = analysis.get("recommendation", {}) or {}
    candidates = _apply_hint_rerank(list(analysis.get("candidate_treasure_checks", []) or []), hint_text)
    inferred_signals = analysis.get("inferred_signals", {}) or {}

    print(
        f"{analysis.get('analysis_key')} | {analysis.get('active_world_name')} | "
        f"{analysis.get('active_level_name')} | {analysis.get('active_route_name')}"
    )
    print(
        f"  target={analysis.get('target_file')} | chest={inferred_signals.get('effective_chest_size')} | "
        f"top={recommendation.get('top_bridge_key')} | score={recommendation.get('top_score')} | "
        f"margin={recommendation.get('score_margin')} | safe={recommendation.get('auto_confirm_safe')}"
    )
    if hint_text:
        print(f"  review_hint={hint_text}")

    for index, candidate in enumerate(candidates[:limit], start=1):
        print(
            f"  {index}. {candidate.get('bridge_key')} | {candidate.get('name')} | "
            f"score={candidate.get('score')} | chest={candidate.get('chest_size')}"
        )
        description = candidate.get("location_description")
        if description:
            print(f"     {description}")
        vanilla_item_name = candidate.get("vanilla_item_name")
        if vanilla_item_name:
            print(f"     vanilla: {vanilla_item_name}")
        reasons = candidate.get("reasons", [])
        if reasons:
            print(f"     reasons: {'; '.join(reasons)}")
    if len(candidates) > limit:
        print(f"  ... {len(candidates) - limit} more candidates omitted")


def command_show(bridge_dir: Path, analysis_key: str, hint_text: str | None) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    analysis = session.get_treasure_candidate_analysis(analysis_key)
    print_candidate_analysis(analysis, limit=10, hint_text=hint_text)
    return 0


def command_confirm(bridge_dir: Path, analysis_key: str, bridge_key: str | None) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    result = session.confirm_treasure_candidate(analysis_key, bridge_key)
    if result.get("status") == "already_applied":
        print(f"Treasure candidate {analysis_key} was already confirmed as {result.get('bridge_key')}.")
    else:
        print(f"Confirmed treasure candidate {analysis_key} as {result.get('bridge_key')}.")
    return 0




def print_status_summary(status: dict[str, object]) -> None:
    ledger_summary = status.get("item_delivery_ledger")
    if isinstance(ledger_summary, dict):
        print(f"Ledger status counts: {ledger_summary.get('status_counts')}")
        print(f"Ledger replay policies: {ledger_summary.get('replay_policy_counts')}")
        latest_applied = ledger_summary.get("latest_applied")
        if isinstance(latest_applied, list) and latest_applied:
            latest = latest_applied[0]
            if isinstance(latest, dict):
                print(
                    "Latest ledger applied item: "
                    f"#{latest.get('index')} {latest.get('item_name')} "
                    f"source={latest.get('apply_source') or latest.get('last_action_kind') or '-'}"
                )

    last_confirmed = status.get("last_confirmed_treasure")
    if last_confirmed:
        print(
            f"Last confirmed treasure: {last_confirmed.get('bridge_key')} | "
            f"score={last_confirmed.get('top_score')} | "
            f"margin={last_confirmed.get('score_margin')}"
        )
    else:
        print("Last confirmed treasure: none")

    last_runtime_loot_event = status.get("last_runtime_loot_event")
    if last_runtime_loot_event:
        print(
            f"Last runtime loot event: {last_runtime_loot_event.get('bridge_key')} | "
            f"actor={last_runtime_loot_event.get('actor_name')} | "
            f"opened_at={last_runtime_loot_event.get('opened_at')}"
        )
    else:
        print("Last runtime loot event: none")

    last_applied_actions = status.get("last_applied_actions", []) or []
    if last_applied_actions:
        print("Last applied AP item action:")
        for action in last_applied_actions:
            print_pending_action(action, prefix="  last")
    else:
        print("Last applied AP item action: none")

    next_pending = status.get("next_pending_action")
    if next_pending:
        print("Next pending AP item action:")
        print_pending_action(next_pending, prefix="  next")
    else:
        print("Next pending AP item action: none")


def print_item_consumer_plan(plan: dict[str, object]) -> None:
    print(
        f"last_applied_index={plan.get('last_applied_index')} | "
        f"pending_action_count={plan.get('pending_action_count')}"
    )
    for action in plan.get("pending_actions", []):
        print_pending_action(action)


def print_pending_action(action_entry: dict[str, object], prefix: str = "  next") -> None:
    action = action_entry.get("action", {}) or {}
    print(
        f"{prefix}: index={action_entry.get('index')} | item={action_entry.get('item_name')} | "
        f"kind={action.get('kind')} | from={action_entry.get('from_player_name')} | "
        f"location={action_entry.get('location_name')}"
    )
    extra_parts = [
        f"{key}={value}"
        for key, value in action.items()
        if key not in {"kind", "category", "item_name"} and value is not None
    ]
    secondary_rewards = action_entry.get("kh3_secondary_rewards")
    if isinstance(secondary_rewards, list):
        extras = [str(value) for value in secondary_rewards if value]
        if extras:
            extra_parts.append(f"secondary_rewards={', '.join(extras)}")
    if extra_parts:
        print(f"       action: {' | '.join(extra_parts)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KH3 mod-side bridge stub for Archipelago testing.")
    parser.add_argument("bridge_dir", help="Path to a KH3 bridge directory containing slot_data.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show current bridge status")

    check_parser = subparsers.add_parser("check", help="Report one or more KH3 checks")
    check_parser.add_argument("locations", nargs="+", help="Bridge key or AP location name")

    goal_parser = subparsers.add_parser("goal", help="Set goal completion state")
    goal_parser.add_argument("value", choices=("complete", "incomplete"))

    subparsers.add_parser("clear", help="Reset game_state.json")

    subparsers.add_parser("items", help="List pending received AP items")
    subparsers.add_parser("item-plan", help="Show the normalized KH-side pending item action plan")
    subparsers.add_parser("next-item", help="Show the next pending KH-side item action to apply")

    list_parser = subparsers.add_parser("list", help="List known KH3 bridge locations")
    list_parser.add_argument("--world", default=None, help="Filter to a KH3 world grouping")

    candidates_parser = subparsers.add_parser("candidates", help="List current treasure candidate analyses")
    candidates_parser.add_argument("--hint", default=None, help="Optional free-text landmark hint like 'rooftop' or 'theater'")

    show_parser = subparsers.add_parser("show", help="Show one treasure analysis in detail")
    show_parser.add_argument("analysis_key", help="Stable analysis key from treasure_candidates.json")
    show_parser.add_argument("--hint", default=None, help="Optional free-text landmark hint like 'rooftop' or 'theater'")

    confirm_parser = subparsers.add_parser("confirm", help="Confirm a reviewed treasure candidate")
    confirm_parser.add_argument("analysis_key", help="Stable analysis key from treasure_candidates.json")
    confirm_parser.add_argument("bridge_key", nargs="?", help="Optional bridge key override. Defaults to top candidate.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    bridge_dir = _normalize_bridge_dir(args.bridge_dir)

    if args.command == "status":
        return command_status(bridge_dir)
    if args.command == "check":
        return command_check(bridge_dir, args.locations)
    if args.command == "goal":
        return command_goal(bridge_dir, args.value == "complete")
    if args.command == "clear":
        return command_clear(bridge_dir)
    if args.command == "items":
        return command_items(bridge_dir)
    if args.command == "item-plan":
        return command_item_plan(bridge_dir)
    if args.command == "next-item":
        return command_next_item(bridge_dir)
    if args.command == "list":
        return command_list(bridge_dir, args.world)
    if args.command == "candidates":
        return command_candidates(bridge_dir, args.hint)
    if args.command == "show":
        return command_show(bridge_dir, args.analysis_key, args.hint)
    if args.command == "confirm":
        return command_confirm(bridge_dir, args.analysis_key, args.bridge_key)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
