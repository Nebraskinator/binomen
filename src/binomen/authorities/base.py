"""The authority extension point.

Design note. The obvious design is a single `lookup(name) -> str` interface.
That is wrong here for a specific reason: authorities do not agree, and an
interface that returns one string forces every implementation to hide its
disagreement with the others before the resolver ever sees it. So the contract
is that an authority returns *its own* answer with *its own* native status
vocabulary and provenance, and reconciliation happens once, in the resolver,
where it can be reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..codes import Code, TaxonStatus
from ..models import ChangeEvent, Provenance


@dataclass
class AuthorityResult:
    """One authority's view of one name."""

    authority: str
    found: bool
    accepted_name: str | None = None
    identifier: str | None = None
    rank: str | None = None
    author_citation: str | None = None
    status: TaxonStatus | None = None
    synonyms: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    changes: list[ChangeEvent] = field(default_factory=list)
    provenance: Provenance | None = None
    match_type: str = "none"          # exact | case_insensitive | fuzzy | none
    confidence: float | None = None
    error: str | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        return {
            "authority": self.authority,
            "found": self.found,
            "accepted_name": self.accepted_name,
            "identifier": self.identifier,
            "rank": self.rank,
            "author_citation": self.author_citation,
            "status": self.status.to_dict() if self.status else None,
            "synonyms": self.synonyms,
            "match_type": self.match_type,
            "confidence": self.confidence,
            "error": self.error,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


@runtime_checkable
class Authority(Protocol):
    """Contract for a name authority.

    Implementations must not raise on network failure. They return a result
    with `found=False` and `error` set, because a resolver that crashes when
    one of four sources is down is worse than one that reports three sources
    and says the fourth was unreachable -- and an agent that sees a crash will
    answer from memory.
    """

    name: str
    tier: int
    codes: tuple[Code, ...]
    license_note: str
    redistributable: bool

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult: ...


REGISTRY: dict[str, Authority] = {}


def register(authority: Authority) -> Authority:
    REGISTRY[authority.name] = authority
    return authority


def authorities_for(code: Code, *, max_tier: int = 4, enabled: set[str] | None = None) -> list[Authority]:
    """Authorities that claim jurisdiction over a code, in tier order."""
    # An authority listing Code.UNDETERMINED is declaring "consult me when the
    # code could not be determined", not "I cover everything". GBIF should not
    # be asked about viruses just because it is broad.
    out = [a for a in REGISTRY.values()
           if code in a.codes
           and a.tier <= max_tier
           and (enabled is None or a.name in enabled)]
    return sorted(out, key=lambda a: a.tier)
