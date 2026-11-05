"""Keep accelerated executable inspection equivalent to masked byte matching."""

import random

import pytest

from ..installer_support.paths import (
    _bytes_contain_signature,
)


@pytest.mark.parametrize("data,signature,expected", [
    (b"", "", False),
    (b"x", "? ?", False),
    (b"xy", "?? ?", True),
    (b"\x00\xff\x00", "00 FF 00", True),
    (b"\x00\xff\x00", "? FF ?", True),
    (b"\xff\x00", "? FF ?", False),
    (b"AAAAAB", "41 41 ? 42", True),  # overlapping anchor candidates
    (b"ABBCC", "41 ? 42 43 43", True),  # longest anchor is not first
    (b"ABBCC", "41 ? 42 43 44", False),
    (b"xABC", "? 41 42 43", True),
    (b"ABC", "? 41 42 43", False),
    (b"ABCx", "41 42 43 ?", True),
])
def test_signature_boundaries_and_wildcards(data, signature, expected):
    assert _bytes_contain_signature(data, signature) is expected


def test_signature_search_matches_naive_reference():
    rng = random.Random(317)
    for _ in range(500):
        data = bytes(rng.randrange(4) for _ in range(rng.randrange(60)))
        pattern = [rng.choice((None, 0, 1, 2, 3)) for _ in range(rng.randrange(1, 12))]
        signature = " ".join("?" if value is None else f"{value:02X}" for value in pattern)
        expected = any(
            all(value is None or data[offset + index] == value for index, value in enumerate(pattern))
            for offset in range(len(data) - len(pattern) + 1)
        )
        assert _bytes_contain_signature(data, signature) == expected, (data, signature)
