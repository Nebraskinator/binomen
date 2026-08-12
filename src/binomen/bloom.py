"""Bloom filters for the stage-1 existence-and-code check.

Why a probabilistic structure is defensible in a tool whose entire premise is
that confident wrong answers are the enemy:

A Bloom filter has **no false negatives**. If a name is absent from the filter,
it is absent, with certainty. We exploit that direction and never rely on the
other one:

  name NOT in any code filter  ->  we have no record of it. Say so. Certain.
  name in exactly one filter   ->  no change is recorded for it under that code.
                                   A false positive here means we say "no change
                                   recorded" about a string that is not really a
                                   name -- the cost is a missed 'unknown', not a
                                   fabricated rename.
  name in more than one filter ->  either a genuine cross-code homonym or a
                                   false positive. Both escalate to stage 2,
                                   which answers exactly.

Critically, every name that has *actually changed* lives in the exact stage-1
verdict table, not in a filter. No name with a real nomenclatural history ever
gets a probabilistic answer. The filters only ever certify absence of interest.

Sized for p = 0.001 by default: ~14.4 bits per name, so roughly 1.8 MB per
million names.
"""

from __future__ import annotations

import hashlib
import math
import struct


class BloomFilter:
    __slots__ = ("m", "k", "n", "bits")

    def __init__(self, m: int, k: int, bits: bytearray | None = None, n: int = 0):
        self.m = max(8, m)
        self.k = max(1, k)
        self.n = n
        self.bits = bits if bits is not None else bytearray((self.m + 7) // 8)

    @classmethod
    def sized(cls, expected: int, p: float = 0.001) -> BloomFilter:
        expected = max(1, expected)
        m = int(math.ceil(-expected * math.log(p) / (math.log(2) ** 2)))
        k = max(1, int(round((m / expected) * math.log(2))))
        return cls(m, k)

    def _positions(self, s: str):
        """Kirsch-Mitzenmacher double hashing: two independent 64-bit hashes
        generate k indices without k separate digests.

        SHA-256 rather than BLAKE2b, and the reason is interoperability rather
        than cryptography. These filters are written by Python and read by the
        Node extension, and BLAKE2b's digest length is part of its parameter
        block -- blake2b-128 is not the first 16 bytes of blake2b-512. Node's
        crypto exposes only blake2b512, so the two implementations could never
        have agreed on a single bit, and every membership test would have been
        wrong in the direction of "no record of this name", which reads as a
        legitimate negative result.

        SHA-256 truncated to 16 bytes is available identically everywhere and
        the speed difference is irrelevant at this scale.
        """
        d = hashlib.sha256(s.encode("utf-8")).digest()[:16]
        h1, h2 = struct.unpack("<QQ", d)
        h2 |= 1                                  # keep the stride odd
        for i in range(self.k):
            # No 64-bit wraparound: Python integers are arbitrary precision and
            # the Node reader matches this deliberately. Masking on one side
            # only produced silent false negatives -- see node/src/names.js.
            yield (h1 + i * h2) % self.m

    def add(self, s: str) -> None:
        for pos in self._positions(s):
            self.bits[pos >> 3] |= 1 << (pos & 7)
        self.n += 1

    def __contains__(self, s: str) -> bool:
        return all(self.bits[pos >> 3] & (1 << (pos & 7)) for pos in self._positions(s))

    @property
    def nbytes(self) -> int:
        return len(self.bits)

    def false_positive_rate(self) -> float:
        """Actual expected FP rate given how many items were really inserted."""
        if not self.n:
            return 0.0
        return (1 - math.exp(-self.k * self.n / self.m)) ** self.k

    # -- serialization ------------------------------------------------------
    def dumps(self) -> bytes:
        return struct.pack("<4sIII", b"BLM2", self.m, self.k, self.n) + bytes(self.bits)

    @classmethod
    def loads(cls, blob: bytes) -> BloomFilter:
        magic, m, k, n = struct.unpack("<4sIII", blob[:16])
        if magic == b"BLM1":
            raise ValueError(
                "this filter was built with the old BLAKE2b hash (BLM1) and cannot be read "
                "by the current code or by the Node extension. Rebuild the index.")
        if magic != b"BLM2":
            raise ValueError("not a binomen bloom filter")
        return cls(m, k, bytearray(blob[16:]), n)
