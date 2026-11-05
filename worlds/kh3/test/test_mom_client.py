"""MoM client coordination across worker waits and changing AP destinations."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ..client_support.mom_client import MoMClientRoute, MoMClientService


@pytest.fixture
def session():
    coordinator = MoMClientService()
    challenge = Mock(scope={"slot": 1, "seed_name": "seed"}, challenges={123: {}})
    challenge.poll.return_value = {123, 124, 125}
    challenge.pending_checks.side_effect = lambda completed: completed
    route = MoMClientRoute(Path("bridge"), 1, "seed", frozenset({124}))
    return coordinator, challenge, route


@pytest.mark.parametrize("available,location", [(False, 123), (True, 999)])
def test_launch_ignores_unavailable_challenge(session, available, location):
    coordinator, challenge, _ = session
    start = Mock()
    coordinator.launch(location, current=lambda: challenge if available else None, start_task=start)
    start.assert_not_called()
    assert not coordinator.launch_running


@pytest.mark.parametrize("outcome", ["success", "failure", "replaced", "cancelled"])
def test_launch_revalidates_after_lock_wait_and_always_releases_busy_state(session, outcome):
    coordinator, challenge, _ = session
    selected = challenge

    async def run():
        nonlocal selected
        tasks = []
        await coordinator._io_lock.acquire()
        coordinator.launch(
            123, current=lambda: selected, start_task=lambda task: tasks.append(asyncio.create_task(task))
        )
        assert coordinator.launch_running
        # Repeated UI requests while the first launch is queued must not spawn work.
        coordinator.launch(123, current=lambda: selected, start_task=Mock(side_effect=AssertionError("duplicate")))
        await asyncio.sleep(0)
        if outcome == "replaced":
            selected = Mock()
        elif outcome == "failure":
            challenge.launch.side_effect = OSError("launch failed")
        elif outcome == "cancelled":
            tasks[0].cancel()
        coordinator._io_lock.release()
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
        else:
            await tasks[0]
        assert not coordinator.launch_running

    asyncio.run(run())
    if outcome in {"success", "failure"}:
        challenge.launch.assert_called_once_with(123)
    else:
        challenge.launch.assert_not_called()


def test_poll_skips_all_work_while_launch_is_queued(session):
    coordinator, _, _ = session
    coordinator.launch_running = True
    unexpected = Mock(side_effect=AssertionError("poll while launching"))
    asyncio.run(
        coordinator.poll(
            current=unexpected, route=unexpected, is_connected=unexpected, launch=unexpected, send=unexpected
        )
    )


@pytest.mark.parametrize("change", ["challenge", "seed", "slot", "disconnect", "acknowledged", "none"])
def test_poll_rechecks_destination_and_acknowledgements_after_barrier_io(session, change):
    coordinator, challenge, initial_route = session
    selected, destination, connected = challenge, initial_route, True
    send, launch = AsyncMock(), Mock()

    def barrier(service, route):
        nonlocal selected, destination, connected
        assert service is challenge and route is initial_route
        if change == "challenge":
            selected = Mock()
        elif change in {"seed", "slot"}:
            destination = MoMClientRoute(
                route.barrier_dir,
                2 if change == "slot" else 1,
                "other" if change == "seed" else "seed",
                route.checked_locations,
            )
        elif change == "disconnect":
            connected = False
        elif change == "acknowledged":
            destination = MoMClientRoute(route.barrier_dir, 1, "seed", frozenset({123, 124, 125}))
        return None

    with patch.object(coordinator, "_sync_barrier", side_effect=barrier):
        asyncio.run(
            coordinator.poll(
                current=lambda: selected,
                route=lambda: destination,
                is_connected=lambda: connected,
                launch=launch,
                send=send,
            )
        )
    if change == "none":
        send.assert_awaited_once_with([{"cmd": "LocationChecks", "locations": [123, 125]}])
    else:
        send.assert_not_awaited()
    launch.assert_not_called()


@pytest.mark.parametrize("requested", [None, 123, 124, 999])
def test_interaction_only_launches_unfinished_unchecked_challenge(session, requested):
    coordinator, challenge, route = session
    challenge.poll.return_value = {123}
    send, launch = AsyncMock(), Mock()
    with patch.object(coordinator, "_sync_barrier", return_value=requested):
        asyncio.run(
            coordinator.poll(
                current=lambda: challenge, route=lambda: route, is_connected=lambda: False, launch=launch, send=send
            )
        )
    if requested == 999:
        launch.assert_called_once_with(999)
    else:
        launch.assert_not_called()
    send.assert_not_awaited()


def test_replaced_challenge_during_barrier_does_not_launch_interaction(session):
    coordinator, challenge, route = session
    selected = challenge

    def barrier(*args):
        nonlocal selected
        selected = None
        return 999

    launch, send = Mock(), AsyncMock()
    with patch.object(coordinator, "_sync_barrier", side_effect=barrier):
        asyncio.run(
            coordinator.poll(
                current=lambda: selected, route=lambda: route, is_connected=lambda: True, launch=launch, send=send
            )
        )
    launch.assert_not_called()
    send.assert_not_awaited()


def test_failed_ap_send_is_retried_until_acknowledged(session):
    coordinator, challenge, route = session
    send = AsyncMock(side_effect=[OSError("disconnected"), None])
    options = dict(current=lambda: challenge, route=lambda: route, is_connected=lambda: True, launch=Mock(), send=send)

    async def run():
        with patch.object(coordinator, "_sync_barrier", return_value=None):
            with pytest.raises(OSError, match="disconnected"):
                await coordinator.poll(**options)
            await coordinator.poll(**options)

    asyncio.run(run())
    assert send.await_count == 2
    assert send.await_args_list[0] == send.await_args_list[1]


@pytest.mark.parametrize("matching", [True, False])
def test_barrier_publication_order_and_scoped_checks(session, matching):
    coordinator, challenge, route = session
    if not matching:
        route = MoMClientRoute(route.barrier_dir, 2, "other", route.checked_locations)
    events = []
    challenge.publish_clears.side_effect = lambda *args: events.append(("chests", args))
    with (
        patch(
            "worlds.kh3.client_support.mom_barrier.publish_clear",
            side_effect=lambda *args: events.append(("clear", args)),
        ),
        patch(
            "worlds.kh3.client_support.mom_barrier.consume_interaction",
            side_effect=lambda *args: events.append(("interaction", args)) or 123,
        ),
    ):
        assert coordinator._sync_barrier(challenge, route) == 123
    checked = {124} if matching else set()
    assert events == [
        ("chests", (route.barrier_dir,)),
        ("clear", (challenge, route.barrier_dir, checked)),
        ("interaction", (challenge, route.barrier_dir)),
    ]


def test_launch_waits_until_poll_and_barrier_io_finish(session):
    coordinator, challenge, route = session
    events = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        tasks = []

        async def worker(function, *args):
            if function == challenge.poll:
                events.append("poll")
                entered.set()
                await release.wait()
                return set()
            if function == coordinator._sync_barrier:
                events.append("barrier")
                return None
            assert function == challenge.launch and args == (123,)
            events.append("launch")

        with patch("worlds.kh3.client_support.mom_client.asyncio.to_thread", side_effect=worker):
            polling = asyncio.create_task(
                coordinator.poll(
                    current=lambda: challenge,
                    route=lambda: route,
                    is_connected=lambda: False,
                    launch=Mock(),
                    send=AsyncMock(),
                )
            )
            await entered.wait()
            coordinator.launch(
                123, current=lambda: challenge, start_task=lambda task: tasks.append(asyncio.create_task(task))
            )
            await asyncio.sleep(0)
            assert events == ["poll"]
            release.set()
            await polling
            await tasks[0]
        assert not coordinator.launch_running

    asyncio.run(run())
    assert events == ["poll", "barrier", "launch"]
