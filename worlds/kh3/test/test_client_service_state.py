"""Public client state and service cursors share one owner across lazy setup."""

from unittest.mock import Mock

import pytest

from ..Client import KH3Context

STATE_CASES = (
    (
        "bridge_sync_service",
        "_get_bridge_sync_service",
        "_bridge_observation_state",
        {
            "last_processed_game_state_seq": ("last_sequence", 17),
            "last_processed_game_state_file_signature": ("last_file_signature", (10, 20)),
            "goal_status_sent": ("goal_reported", True),
        },
    ),
    (
        "client_notification_service",
        "_get_client_notification_service",
        "_notification_state",
        {
            "last_processed_bridge_notification_seq": ("last_bridge_notification_seq", 12),
        },
    ),
)


@pytest.mark.parametrize("service_name,get_service,get_state,properties", STATE_CASES)
def test_lazy_creation_adopts_existing_state_without_side_effects(service_name, get_service, get_state, properties):
    ctx = KH3Context.__new__(KH3Context)
    ctx.send_msgs, ctx.send_death, ctx.update_death_link = Mock(), Mock(), Mock()
    for public, (_, value) in properties.items():
        setattr(ctx, public, value)
    assert service_name not in ctx.__dict__
    initial = getattr(ctx, get_state)()
    service = getattr(ctx, get_service)()
    assert service.state is initial
    for public, (field, value) in properties.items():
        assert getattr(ctx, public) == getattr(service, field) == value
        assert "_" + public not in ctx.__dict__
    ctx.send_msgs.assert_not_called()
    ctx.send_death.assert_not_called()
    ctx.update_death_link.assert_not_called()


@pytest.mark.parametrize("service_name,get_service,get_state,properties", STATE_CASES)
def test_context_and_service_writes_remain_visible_after_detachment(service_name, get_service, get_state, properties):
    ctx = KH3Context.__new__(KH3Context)
    service = getattr(ctx, get_service)()
    for public, (field, value) in properties.items():
        setattr(service, field, value)
        assert getattr(ctx, public) == value
    delattr(ctx, service_name)
    assert getattr(ctx, get_state)() is service.state
    for public, (field, value) in properties.items():
        assert getattr(ctx, public) == value
        setattr(ctx, public, value)
        assert getattr(service, field) == value
    assert getattr(ctx, get_service)().state is service.state


@pytest.mark.parametrize("service_name,get_service,get_state,properties", STATE_CASES)
def test_new_service_replaces_the_current_state_without_cross_context_leaks(
    service_name, get_service, get_state, properties
):
    first, second = KH3Context.__new__(KH3Context), KH3Context.__new__(KH3Context)
    original = getattr(first, get_state)()
    replacement = getattr(second, get_service)()
    for _, (field, value) in properties.items():
        setattr(replacement, field, value)
    setattr(first, service_name, replacement)
    assert getattr(first, get_state)() is replacement.state
    assert replacement.state is not original
    for public, (_, value) in properties.items():
        assert getattr(first, public) == value
    fresh = KH3Context.__new__(KH3Context)
    assert getattr(fresh, get_state)() is not replacement.state


@pytest.mark.parametrize(
    "public,value,expected",
    [
        ("last_processed_game_state_seq", "19", 19),
        ("goal_status_sent", 1, True),
        ("last_processed_bridge_notification_seq", None, 0),
    ],
)
def test_public_properties_retain_input_normalization(public, value, expected):
    ctx = KH3Context.__new__(KH3Context)
    setattr(ctx, public, value)
    assert getattr(ctx, public) == expected
    assert type(getattr(ctx, public)) is type(expected)
