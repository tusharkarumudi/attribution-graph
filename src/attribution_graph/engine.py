"""Frontier-driven orchestration, agnostic to how claims are collected.

    seeds -> [frontier non-empty?] -> pick by priority -> scope gate
          -> collector fan-out -> filter -> enqueue -> loop -> resolve
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field

from . import filters
from .model import AttributionGraph, Claim, Identifier, IdKind
from .protocol import Collector, InMemoryIndex, SelectivityIndex
from .resolve import ResolutionResult, resolve
from .scope import CaseScope

#: Frontier priority by identifier kind. Lower runs first. High-selectivity
#: identifiers are worked before names and infrastructure because they are what
#: actually collapse the hypothesis space.
PRIORITY: dict[IdKind, int] = {
    IdKind.SELLER_ID: 1, IdKind.LEI: 1, IdKind.CIK: 1, IdKind.COMPANY_NUMBER: 1,
    IdKind.ANALYTICS_ID: 2, IdKind.GRAVATAR_HASH: 2, IdKind.PGP_FPR: 2,
    IdKind.SSH_FPR: 2, IdKind.DOMAIN: 2,
    IdKind.EMAIL: 3, IdKind.HANDLE: 3,
    IdKind.ORG_NAME: 4, IdKind.PERSON_NAME: 4,
    IdKind.IP: 5, IdKind.ASN: 5,
}

SEED_PREFIXES = {k.value: k for k in IdKind}

#: Collector names whose output describes natural persons. Gated on the case
#: file's entity_types_allowed. Downstream packages register their own here.
PERSON_SCOPED_COLLECTORS: set[str] = set()


@dataclass(order=True)
class FrontierItem:
    priority: int
    depth: int
    key: str = field(compare=False)
    ident: Identifier = field(compare=False)


#: Identifier kinds worth re-collecting on. The sibling pivot -- the reason the
#: seed's silence is not the end -- runs by re-collecting on what siblings
#: reveal, so this set decides how far the fan-out reaches. A header value or a
#: raw HTML comment is an observation to record, not a pivot to chase; a domain,
#: an org, a person, a seller ID, an analytics ID, an email or a code-host
#: handle each opens a genuinely new surface.
PIVOTABLE_KINDS = frozenset({
    IdKind.DOMAIN, IdKind.ORG_NAME, IdKind.PERSON_NAME, IdKind.SELLER_ID,
    IdKind.ANALYTICS_ID, IdKind.EMAIL, IdKind.HANDLE, IdKind.LEI,
    IdKind.COMPANY_NUMBER, IdKind.GRAVATAR_HASH, IdKind.CIK,
})


#: Exceptions that mean the world was uncooperative, not that we are broken.
#:
#: `Engine._process` caught every Exception and recorded `collector_error`. A
#: PolicyEngine signature mismatch therefore became twelve ordinary "source
#: unavailable" entries, and the run reported success with zero claims. A
#: defect that presents as a dead source is a defect nobody investigates.
OPERATIONAL_FAILURES: tuple[type[BaseException], ...] = (
    TimeoutError, ConnectionError, OSError, ValueError, KeyError,
)


class _UnreachableBudgetError(Exception):
    """Placeholder so `except` always has a type when the transport package is
    absent. Never raised."""


#: Exceptions meaning "the declared limit was reached", not "something broke".
#: Resolved by name so the inference core keeps no dependency on the transport
#: package; anything unresolvable simply never matches.


def _is_budget_exhaustion(exc: BaseException) -> bool:
    """Whether an exception means "the declared limit was reached".

    Decided when the exception is raised, not at import. Resolving the type at
    module scope ran before `paytrace` was importable -- it imports this
    package -- so the tuple always bound the placeholder and every budgeted run
    reported its own safety control as an internal defect.

    Matched by qualified name so the inference core keeps no import-time
    dependency on the transport package.
    """
    t = type(exc)
    return f"{t.__module__}.{t.__name__}" in {
        "paytrace.net.BudgetExceeded",
        "attribution_graph.engine.BudgetExceeded",
    }


def _collector_context(name: str):
    """The collector-attribution context manager, if the transport provides one.

    Kept optional so ``attribution-graph`` does not acquire a dependency on the
    collector package; the boundary that makes this package's scoring auditable
    is that it performs no network I/O and imports nothing that does.
    """
    try:
        from paytrace.net import collector_context
    except ImportError:
        return None
    return collector_context(name)


class Engine:
    def __init__(
        self,
        scope: CaseScope,
        collectors: list[Collector],
        index: SelectivityIndex | None = None,
        concurrency: int = 6,
        strict: bool = False,
    ) -> None:
        for c in collectors:
            scope.check_source_class(c.name, c.source_class)  # raises on denied class
        self.scope = scope
        self.collectors = sorted(collectors, key=lambda c: c.priority)
        self.graph = AttributionGraph(case_ref=scope.case_ref)
        self.index = index or InMemoryIndex(self.graph)
        # Validated here because this is the single boundary every entry point
        # crosses. Case-file loading rejected it, but `--concurrency 0` is a
        # separate input path and reached asyncio.Semaphore(0), where no task
        # can ever acquire a permit and the run hangs indefinitely. The earlier
        # regression tested configuration loading rather than this boundary.
        if concurrency < 1:
            raise ValueError(
                f"concurrency must be at least 1, got {concurrency}. "
                "A zero-permit semaphore hangs rather than disabling "
                "concurrency.")

        self.frontier: list[FrontierItem] = []
        #: Unexpected exceptions raised by collectors -- our defects, not the
        #: world being uncooperative. Referenced by the runner, which must not
        #: report a normal result when this is non-empty. It was previously
        #: written to `self.stats`, which does not exist, so the handler raised
        #: AttributeError and masked the original defect entirely.
        self.internal_defects: list[dict] = []
        #: Set when the declared request budget stopped the run. The result is
        #: then incomplete, not invalid: everything collected is sound, there
        #: is simply less of it than an unbudgeted run would have produced.
        self.budget_exhausted = False
        #: Set when the node budget stopped frontier expansion. A safety limit
        #: that silently shortens the search while the result still looks
        #: complete can change an attribution conclusion.
        self.nodes_truncated = False
        #: Set when the wall-clock budget stopped traversal with frontier work
        #: still queued. Checked only between batches previously, and never
        #: surfaced -- so a run cut short by its own time limit still reported
        #: complete, turning a safety limit into a silent change of result.
        self.runtime_exhausted = False
        #: Re-raise unexpected exceptions instead of recording them as
        #: collector failures. On in CI; off in a live run so one defect
        #: does not lose an investigation.
        self.strict = strict
        self.seen: set[str] = set()
        self.sem = asyncio.Semaphore(concurrency)
        self.started = 0.0

    # ---- frontier --------------------------------------------------------- #

    def enqueue(self, ident: Identifier, depth: int) -> None:
        if ident.key in self.seen:
            return
        if not self.scope.within_radius(depth):
            return
        if len(self.seen) >= self.scope.max_nodes:
            # Record the truncation. Returning silently meant the node budget
            # shortened the search while the result still reported complete,
            # and a shortened search can change an attribution conclusion.
            if not self.nodes_truncated:
                self.nodes_truncated = True
                self.scope.audit("nodes_truncated", limit=self.scope.max_nodes)
            return
        self.seen.add(ident.key)
        heapq.heappush(
            self.frontier,
            FrontierItem(PRIORITY.get(ident.kind, 5), depth, ident.key, ident),
        )

    @staticmethod
    def parse_seed(seed: str) -> Identifier:
        if ":" in seed:
            prefix, val = seed.split(":", 1)
            if prefix in SEED_PREFIXES:
                return Identifier(SEED_PREFIXES[prefix], val)
        if "@" in seed:
            return Identifier(IdKind.EMAIL, seed)
        return Identifier(IdKind.DOMAIN, seed)

    # ---- run -------------------------------------------------------------- #

    async def run(self) -> ResolutionResult:
        self.started = time.monotonic()
        self.scope.audit(
            "run_start", seeds=self.scope.seeds,
            collectors=[c.name for c in self.collectors],
            pivot_radius=self.scope.pivot_radius,
        )
        for s in self.scope.seeds:
            self.enqueue(self.parse_seed(s), depth=0)

        while self.frontier:
            if time.monotonic() - self.started > self.scope.max_runtime_s:
                # Mark it. Stopping silently turned a safety limit into an
                # undetectable change of result: the run reported complete
                # while frontier work was still queued.
                self.runtime_exhausted = True
                self.scope.audit(
                    "budget_stop", reason="max_runtime_s",
                    remaining_frontier=len(self.frontier))
                break
            batch = [heapq.heappop(self.frontier) for _ in range(min(8, len(self.frontier)))]
            try:
                await asyncio.gather(*(self._process(i) for i in batch))
            except _Budget as e:
                self.scope.audit("budget_stop", reason=str(e))
                break

        result = resolve(self.graph, self.scope.entity_types_allowed, index=self.index)
        self.scope.audit(
            "run_complete",
            identifiers=len(self.graph.identifiers),
            claims=len(self.graph.claims),
            entities=len(self.graph.entities),
        )
        return result

    async def _process(self, item: FrontierItem) -> None:
        async with self.sem:
            for col in self.collectors:
                if item.ident.kind not in col.accepts:
                    continue
                if col.name in PERSON_SCOPED_COLLECTORS:
                    ok, why = self.scope.allows_persona_collector(col.name)
                    if not ok:
                        # Recorded, not silent: a reviewer must be able to see
                        # which sources were available and deliberately unused.
                        self.scope.audit("collector_gated", collector=col.name,
                                         identifier=item.ident.key, reason=why)
                        continue
                try:
                    # Name the collector for the duration of its fetches, so
                    # every capture it causes is attributable. Without this
                    # every capture recorded collector="fetcher" and the
                    # evidence package could not say what asked for anything.
                    #
                    # Imported lazily and optionally: the inference core does
                    # not depend on the collector package, and a run without it
                    # simply records the default.
                    ctx = _collector_context(col.name)
                    if ctx is not None:
                        with ctx:
                            claims = list(await col.collect(item.ident))
                    else:
                        claims = list(await col.collect(item.ident))
                except OPERATIONAL_FAILURES as e:
                    # One bad collector must not kill a run, but the failure is
                    # recorded -- a silently skipped source is a silently
                    # weakened conclusion.
                    self.scope.audit(
                        "collector_error", collector=col.name,
                        identifier=item.ident.key, error=repr(e),
                    )
                    continue
                except Exception as e:
                    # One handler, dispatched on meaning rather than on type.
                    #
                    # Budget exhaustion is the safety control working, not a
                    # defect; it stops the run and marks the result INCOMPLETE.
                    # Anything else unexpected is our bug and marks the result
                    # INVALID, because a conclusion computed with silently
                    # missing collectors is not a weaker answer, it is an
                    # unknown one.
                    if _is_budget_exhaustion(e):
                        self.budget_exhausted = True
                        self.scope.audit(
                            "budget_exhausted", collector=col.name,
                            identifier=item.ident.key, error=repr(e),
                        )
                        self.frontier.clear()
                        break

                    self.internal_defects.append({
                        "collector": col.name,
                        "identifier": item.ident.key,
                        "error": type(e).__name__,
                    })
                    self.scope.audit(
                        "internal_defect", collector=col.name,
                        identifier=item.ident.key, error=repr(e),
                    )
                    if self.strict:
                        raise
                    continue
                self.scope.audit(
                    "collect", collector=col.name, identifier=item.ident.key,
                    depth=item.depth, claims=len(claims),
                )
                for c in claims:
                    self.ingest(c, item.depth)

    def ingest(self, claim: Claim, depth: int) -> None:
        target = claim.object if isinstance(claim.object, Identifier) else None
        holders = self.index.holders(target) if target else 1
        verdict, reason = filters.evaluate(claim, holders)

        if verdict is filters.Verdict.DROP:
            self.scope.audit(
                "filter_drop", claim=claim.object_key,
                collector=claim.collector, reason=reason,
            )
            return
        if verdict is filters.Verdict.DEMOTE:
            claim.weight = 0.0
            claim.raw["demoted"] = reason

        self.graph.add_claim(claim)
        # Re-collect only on kinds that open a new surface. Everything else is
        # recorded as evidence but not chased, which keeps the fan-out on the
        # identifiers that actually carry the investigation across siblings.
        if (target and verdict is filters.Verdict.KEEP
                and target.kind in PIVOTABLE_KINDS):
            self.enqueue(target, depth + 1)


class _Budget(RuntimeError):
    pass
