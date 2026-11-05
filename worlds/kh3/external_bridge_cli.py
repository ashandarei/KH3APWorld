"""Command-line interface and console reporting for the KH3 external bridge."""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .BridgeSession import KH3BridgeSession

from . import ExternalBridge as _bridge_backend

Output = Callable[[object], None]


def print_status(session: KH3BridgeSession, output: Output = print) -> None:
    status = session.build_status()
    output(f"Bridge directory: {status['bridge_dir']}")
    output(f"Protocol: {status['protocol_name']} v{status['protocol_version']}")
    output(f"Tracked KH3 checks: {status['tracked_checks']}")
    output(f"Reported checks: {status['reported_checks']}")
    output(f"Received AP items: {status['received_item_count']}")
    output(f"Pending AP items: {status['pending_ap_item_count']}")
    output(f"Applied AP items: {status.get('applied_ap_item_count', 0)}")
    output(f"Last applied AP item index: {status['last_applied_item_index']}")
    ledger_summary = status.get("item_delivery_ledger")
    if isinstance(ledger_summary, dict):
        output(f"Ledger status counts: {ledger_summary.get('status_counts')}")
        output(f"Ledger replay policies: {ledger_summary.get('replay_policy_counts')}")
    ap_status = status["ap_status"]
    if ap_status:
        output(f"AP connected: {ap_status.get('connected')}")
        output(f"AP checked location count: {ap_status.get('checked_location_count')}")
        output(f"Goal complete: {status['game_state'].get('goal_complete')}")
    runtime_loot = status.get("last_runtime_loot_event")
    if runtime_loot:
        output(
            f"Last runtime loot event: {runtime_loot.get('bridge_key')} | "
            f"actor={runtime_loot.get('actor_name')} | opened_at={runtime_loot.get('opened_at')}"
        )


def print_locations(session: KH3BridgeSession, world_name: str | None, output: Output = print) -> None:
    for location in session.list_locations(world_name):
        output(f"{location['bridge_key']} | {location['ap_location_name']}")


def print_pending_items(session: KH3BridgeSession, output: Output = print) -> None:
    pending_items = session.list_pending_items()
    if not pending_items:
        output("No pending AP items are available.")
        return
    for item in pending_items:
        output(
            f"{item['index']} | {item['item_name']} | from={item['from_player_name']} | "
            f"location={item['location_name']}"
        )


def print_candidate_analyses(session: KH3BridgeSession, output: Output = print) -> None:
    analyses = session.list_treasure_candidate_analyses()
    if not analyses:
        output("No KH3 treasure candidate analyses are available.")
        return
    for analysis in analyses:
        print_candidate_analysis(analysis, output=output)


def print_candidate_analysis(analysis: dict[str, object], limit: int = 5, output: Output = print) -> None:
    recommendation = analysis.get("recommendation", {}) or {}
    candidates = analysis.get("candidate_treasure_checks", []) or []
    inferred_signals = analysis.get("inferred_signals", {}) or {}
    output(
        f"{analysis.get('analysis_key')} | {analysis.get('active_world_name')} | "
        f"{analysis.get('active_level_name')} | {analysis.get('active_route_name')}"
    )
    output(
        f"  target={analysis.get('target_file')} | chest={inferred_signals.get('effective_chest_size')} | "
        f"top={recommendation.get('top_bridge_key')} | score={recommendation.get('top_score')} | "
        f"margin={recommendation.get('score_margin')} | safe={recommendation.get('auto_confirm_safe')}"
    )
    for index, candidate in enumerate(candidates[:limit], start=1):
        output(
            f"  {index}. {candidate.get('bridge_key')} | {candidate.get('name')} | "
            f"score={candidate.get('score')} | chest={candidate.get('chest_size')}"
        )
        description = candidate.get("location_description")
        if description:
            output(f"     {description}")
        reasons = candidate.get("reasons", [])
        if reasons:
            output(f"     reasons: {'; '.join(reasons)}")
    if len(candidates) > limit:
        output(f"  ... {len(candidates) - limit} more candidates omitted")


def print_confirmation_result(result: dict[str, object], output: Output = print) -> None:
    if result.get("status") == "already_applied":
        output(f"Treasure candidate {result.get('analysis_key')} was already confirmed as {result.get('bridge_key')}.")
        return
    output(f"Confirmed treasure candidate {result.get('analysis_key')} as {result.get('bridge_key')}.")




def print_pending_action(
    action_entry: dict[str, object],
    prefix: str = "  next",
    output: Output = print,
) -> None:
    action = action_entry.get("action", {}) or {}
    output(
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
        output(f"       action: {' | '.join(extra_parts)}")


class ConsoleReporter:
    """Render bridge events with the legacy console wording."""

    def write(self, message: object = "") -> None:
        print(message)

    def error(self, message: object, *, include_traceback: bool = False) -> None:
        print(message, file=sys.stderr)
        if include_traceback:
            traceback.print_exc()

    def status(self, session: KH3BridgeSession) -> None:
        print_status(session)

    def locations(self, session: KH3BridgeSession, world_name: str | None) -> None:
        print_locations(session, world_name)

    def pending_items(self, session: KH3BridgeSession) -> None:
        print_pending_items(session)

    def candidate_analyses(self, session: KH3BridgeSession) -> None:
        print_candidate_analyses(session)

    def confirmation_result(self, result: dict[str, object]) -> None:
        print_confirmation_result(result)


    def pending_action(self, action: dict[str, object], prefix: str = "  next") -> None:
        print_pending_action(action, prefix)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KH3 external bridge runner.")
    parser.add_argument("bridge_dir", help="Path to a KH3 bridge directory containing slot_data.json")
    parser.add_argument(
        "--backend",
        choices=("manual", "command-file", "observed-state", "save-files", "runtime-savepoint", "runtime-loot-events"),
        default="manual",
        help="External bridge backend to run.",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval for polling-based backends.")
    parser.add_argument(
        "--save-dir",
        default=str(_bridge_backend.default_save_directory()),
        help="KH3 save directory for the save-files backend.",
    )
    parser.add_argument(
        "--process-name",
        default="KINGDOM HEARTS III.exe",
        help="Target KH3 process name for runtime backends.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Target KH3 process id for runtime backends. Overrides --process-name when provided.",
    )
    return parser


def create_backend(args: argparse.Namespace, reporter: ConsoleReporter):
    if args.backend == "manual":
        return _bridge_backend.ManualBackend(reporter=reporter)
    if args.backend == "command-file":
        return _bridge_backend.CommandFileBackend(args.poll_interval, reporter=reporter)
    if args.backend == "observed-state":
        return _bridge_backend.ObservedStateBackend(args.poll_interval, reporter=reporter)
    if args.backend == "save-files":
        return _bridge_backend.SaveFilesBackend(
            Path(args.save_dir),
            args.poll_interval,
            reporter=reporter,
        )
    if args.backend == "runtime-loot-events":
        return _bridge_backend.RuntimeLootEventsBackend(args.poll_interval, reporter=reporter)
    return _bridge_backend.RuntimeSavepointBackend(
        save_dir=Path(args.save_dir),
        process_name=args.process_name,
        pid=args.pid,
        poll_interval=args.poll_interval,
        reporter=reporter,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reporter = ConsoleReporter()
    live_pipe = None
    pipe_module = importlib.import_module("worlds.kh3.transport.pipe_transport")
    if args.backend in {"save-files", "runtime-loot-events"}:
        live_pipe = pipe_module.register(Path(args.bridge_dir))
    try:
        if live_pipe is not None:
            live_pipe.start()
        session = _bridge_backend.KH3BridgeSession(Path(args.bridge_dir))
        session.initialize()
        status_details = {}
        if args.backend == "save-files":
            resolved_save_dir = str(Path(args.save_dir).expanduser().resolve())
            status_details = {
                "configured_save_dir": resolved_save_dir,
                "save_dir": resolved_save_dir,
                "save_dir_source": "configured",
            }
        status_writer = _bridge_backend.BridgeRuntimeStatusWriter(session, args.backend, details=status_details)
        status_writer.mark_starting()
        backend = create_backend(args, reporter)
        if live_pipe is not None and args.backend == "save-files":
            backend.activity_event = live_pipe.activity
        status_writer.mark_running()
        try:
            result = backend.run(session, status_writer)
        except Exception as exc:
            status_writer.mark_error(str(exc))
            raise
        status_writer.mark_stopped()
        return result
    finally:
        if live_pipe is not None:
            pipe_module.unregister(live_pipe)



def launch(argv: list[str] | None = None) -> None:
    raise SystemExit(main(argv))


if __name__ == "__main__":
    launch(sys.argv[1:])
