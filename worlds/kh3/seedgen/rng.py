"""Deterministic seeded generator and shuffles for KH3 options."""
from __future__ import annotations


class SeedRandom:
    @staticmethod
    def int32(value):
        value &= 0xFFFFFFFF
        return value if value < 0x80000000 else value - 0x100000000

    def __init__(self, seed: str):
        hashed = 0
        raw = seed.encode('utf-16-le')
        for i in range(0, len(raw), 2):
            hashed = (31 * hashed + int.from_bytes(raw[i:i+2], 'little')) & 0xFFFFFFFF
        signed = hashed if hashed < 0x80000000 else hashed - 0x100000000
        subtraction = 2147483647 if signed == -2147483648 else abs(signed)
        mj = 161803398 - subtraction
        self.state = [0] * 56
        self.state[55] = mj
        mk = 1
        for i in range(1, 55):
            ii = (21 * i) % 55
            self.state[ii] = mk
            mk = self.int32(mj - mk)
            if mk < 0:
                mk += 2147483647
            mj = self.state[ii]
        for _ in range(4):
            for i in range(1, 56):
                self.state[i] = self.int32(self.state[i] - self.state[1 + (i + 30) % 55])
                if self.state[i] < 0:
                    self.state[i] += 2147483647
        self.a, self.b = 0, 21

    def sample(self):
        self.a = self.a + 1 if self.a < 55 else 1
        self.b = self.b + 1 if self.b < 55 else 1
        value = self.int32(self.state[self.a] - self.state[self.b])
        if value == 2147483647:
            value -= 1
        if value < 0:
            value += 2147483647
        self.state[self.a] = value
        return value

    def next(self, minimum=None, maximum=None):
        if minimum is None:
            return self.sample()
        if maximum is None:
            minimum, maximum = 0, minimum
        if minimum > maximum:
            raise ValueError('Invalid random bounds')
        return minimum + int(self.sample() * (1.0 / 2147483647) * (maximum - minimum))

    def shuffle(self, values):
        for i in range(len(values) - 1, 0, -1):
            j = self.next(0, i + 1)
            values[i], values[j] = values[j], values[i]

    def choice(self, values):
        if not values:
            raise ValueError('Cannot choose from an empty randomization pool')
        return values[self.next(0, len(values))]
