"""Protocols that decouple the inference core from data collection.

This library performs no network I/O. Collectors live in downstream packages
and satisfy the ``Collector`` protocol; selectivity counts come from a
``SelectivityIndex`` supplied by the caller. Keeping the core free of I/O is
what makes the scoring model auditable: every number in an assessment is a pure
function of claims plus index counts, with no hidden network state.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from .model import Claim, Identifier, IdKind


@runtime_checkable
class Collector(Protocol):
    """A source of claims about an identifier."""

    name: str
    accepts: Sequence[IdKind]
    source_class: object          # scope.SourceClass; typed loosely to avoid a cycle
    priority: int

    async def collect(self, ident: Identifier) -> Iterable[Claim]:
        ...


@runtime_checkable
class SelectivityIndex(Protocol):
    """Supplies the counts that give evidence its weight.

    ``holders(ident)`` is the number of distinct entities observed carrying that
    identifier value. This is the single most important input to the scoring
    model: it is what distinguishes an analytics ID held by one site from an IP
    held by four hundred thousand, without either being hardcoded.

    A per-case in-memory index (the default) systematically *overestimates*
    selectivity, because it can only count what the current investigation has
    seen. Production deployments should back this with a persistent corpus.
    """

    def holders(self, ident: Identifier) -> int:
        ...

    def universe(self) -> int:
        ...


class InMemoryIndex:
    """Default index over a single case graph.

    Correct but optimistic: an identifier this case has seen once looks unique
    even if it is held by thousands of entities globally. Assessments produced
    with this index are upper bounds on confidence. Ship a corpus-backed index
    before treating output as evidential.
    """

    def __init__(self, graph, universe: int = 1_000_000) -> None:
        self._graph = graph
        self._universe = universe

    def holders(self, ident: Identifier) -> int:
        return self._graph.holders(ident)

    def universe(self) -> int:
        return self._universe


class CompositeIndex:
    """Prefers a persistent corpus, falls back to the case graph.

    Use this when a corpus covers some identifier kinds well (ads.txt seller IDs,
    certificate hashes) and not others (personal names).
    """

    def __init__(self, primary: SelectivityIndex, fallback: SelectivityIndex) -> None:
        self.primary = primary
        self.fallback = fallback

    def holders(self, ident: Identifier) -> int:
        n = self.primary.holders(ident)
        return n if n > 0 else self.fallback.holders(ident)

    def universe(self) -> int:
        return max(self.primary.universe(), self.fallback.universe())
