"""Regression coverage for filesystem and challenge polling fast paths."""
from pathlib import Path
from unittest.mock import patch

import pytest

from ..client_support import mom_challenges as mom


@pytest.fixture
def challenge():
    from .test_mom_challenges import TestMoMChallenges
    case = TestMoMChallenges()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def test_unchanged_state_does_not_serialize_or_write(challenge):
    with patch.object(mom.json, "dumps", side_effect=AssertionError("idle serialization")), \
         patch.object(mom, "atomic_write_json") as write:
        assert challenge.service.poll() == set()
        assert challenge.service.poll() == set()
    write.assert_not_called()

def test_seen_result_is_not_reopened_but_partial_result_retries(challenge):
    challenge.event(outcome="failed", nativeExit="GameOver", cleared=False, eligibleForChallenge=False)
    assert challenge.service.poll() == set()
    consumed = next(challenge.events.glob("*.json"))
    original = Path.read_text
    def read(path, *args, **kwargs):
        assert path != consumed, "already applied result was reopened"
        return original(path, *args, **kwargs)
    with patch.object(Path, "read_text", read):
        assert challenge.service.poll() == set()
        challenge.event()
        pending = next(path for path in challenge.events.glob("*.json") if path != consumed)
        payload = pending.read_text()
        pending.write_text('{"schemaVersion":')
        assert challenge.service.poll() == set()
        pending.write_text(payload)
        assert challenge.service.poll()
