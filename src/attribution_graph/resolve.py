"""Entity resolution.

Identifiers are clustered into entities by agglomerating pairwise links whose
posterior exceeds a merge threshold, subject to must-not-link constraints.

Merging is done highest-confidence-first rather than in discovery order, because
union-find is order-dependent: a single premature weak merge is unrecoverable and
silently contaminates every later decision. Sorting by confidence bounds that
damage.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model import (
    AttributionGraph,
    Claim,
    Entity,
    EntityType,
    Identifier,
    IdKind,
    Predicate,
)
from .scoring import DEFAULT_UNIVERSE, Assessment, Band, assess

#: Posterior required to merge two identifiers into one entity.
#: Merge policy is stated in evidence-score space, not probability space.
#:
#: This was `MERGE_THRESHOLD = 0.90` applied to `Assessment.probability`. Once
#: that value is acknowledged as an unvalidated logistic transform rather than a
#: measured frequency, a 0.90 cut has no decision-theoretic meaning -- renaming
#: the display while keeping the same number as an automatic identity decision
#: would have been cosmetic.
#:
#: The equivalent score is log(0.9/0.1) + |log(PRIOR_ODDS)| nats above the
#: prior, and it is expressed that way so the quantity being thresholded is the
#: one the model actually computes. It remains unfitted, and merging stays
#: gated behind the corroboration requirement.
#: Posterior log-odds required for an inferential merge.
#:
#: log(0.9/0.1) = 2.197 nats, which preserves the previous 0.90 decision
#: boundary exactly while comparing the quantity the model computes.
#:
#: The previous value was `2.197 + 11.513`, adding |log(PRIOR_ODDS)| on the
#: theory that the threshold applied to evidence *above* the prior. But
#: `Assessment.log_odds` already includes the prior, so this compared a
#: posterior against an evidence-increment threshold -- and since the code then
#: compared `probability` (max 1.0) against 13.71, **every inferential merge
#: was disabled**. A pair at p=0.9889 with two independent groups and
#: STRONG_EVIDENCE produced zero clusters.
#:
#: Unfitted, like every constant here, and gated behind the corroboration
#: requirement.
MERGE_LOG_ODDS_THRESHOLD = 2.197

#: Predicates whose object is an attribute of the subject's entity rather than a
#: separate entity (used to attach labels, not to merge).
ATTRIBUTE_PREDICATES = {
    Predicate.INCORPORATED_IN, Predicate.REGISTERED_ADDRESS, Predicate.TIMEZONE_HINT,
}

#: LEGAL_NAME is deliberately NOT an attribute predicate. It is the join that
#: bridges a monetization identifier to a registry record: "seller_id X trades as
#: Acme Media Ltd" plus "LEI Y is Acme Media Ltd" is how the two halves of an
#: investigation meet. The common-name problem this creates is handled where it
#: belongs -- by the selectivity term, which prices a name by how many entities
#: actually carry it, so "Media Ltd" contributes nothing and an unusual legal
#: name contributes a great deal.
LABEL_PREDICATES = {Predicate.LEGAL_NAME}

#: Predicates that assert a *relationship between distinct entities*. Treating
#: these as same-entity evidence is the classic failure that merges a director
#: into the company they direct.
RELATIONSHIP_PREDICATES = {
    Predicate.OFFICER_OF, Predicate.BENEFICIAL_OWNER_OF, Predicate.PARENT_OF,
    Predicate.EMPLOYED_BY,
}

TYPE_BY_KIND = {
    IdKind.ORG_NAME: EntityType.COMPANY,
    IdKind.LEI: EntityType.COMPANY,
    IdKind.CIK: EntityType.COMPANY,
    IdKind.COMPANY_NUMBER: EntityType.COMPANY,
    IdKind.SELLER_ID: EntityType.COMPANY,
    IdKind.PERSON_NAME: EntityType.PERSON,
    IdKind.EMAIL: EntityType.PERSONA,
    IdKind.HANDLE: EntityType.PERSONA,
    IdKind.GRAVATAR_HASH: EntityType.PERSONA,
    IdKind.PGP_FPR: EntityType.PERSONA,
    IdKind.SSH_FPR: EntityType.PERSONA,
}


def _clusters_forbidden(dsu, forbidden, pair) -> bool:
    """Whether joining these two clusters would violate any must-not-link.

    A pairwise check is not enough: constraints must hold over the *clusters*
    the union would create, or a forbidden pair can be brought together through
    an intermediary that is permitted with both.
    """
    ka, kb = pair
    ra, rb = dsu.find(ka), dsu.find(kb)
    if ra == rb:
        return False
    left = dsu.members(ra)
    right = dsu.members(rb)
    for a in left:
        for b in right:
            if tuple(sorted([a, b])) in forbidden:
                return True
    return False


class _DSU:
    """Union-find with explicit membership.

    Membership is tracked so must-not-link constraints can be checked over the
    clusters a union would create, not just the candidate pair.
    """

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self._members: dict[str, set[str]] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self._members[x] = {x}
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def members(self, x: str) -> set[str]:
        """Every key in x's cluster. An unseen key is its own singleton."""
        return self._members.get(self.find(x), {x})

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        self._members.setdefault(ra, {ra}).update(self._members.pop(rb, {rb}))
        return True


@dataclass
class ResolutionResult:
    graph: AttributionGraph
    assessments: dict[tuple[str, str], Assessment] = field(default_factory=dict)
    rejected: list[tuple[str, str, str]] = field(default_factory=list)


def _cluster_types_compatible(dsu, a_key: str, b_key: str,
                              kinds: dict[str, IdKind]) -> bool:
    """Whether merging two clusters keeps every type constraint intact.

    A person and a company are not the same entity. That was checked on the
    candidate pair's two endpoints only, so A(person)~B, B~C(company) merged
    transitively into a cluster containing both -- the same shape as the
    must-not-link violation, and the same fix: compare whole clusters before
    joining, not the two identifiers that happen to be in hand.
    """
    from .model import IdKind

    person = {IdKind.PERSON_NAME}
    company = {IdKind.ORG_NAME, IdKind.LEI, IdKind.COMPANY_NUMBER, IdKind.CIK}

    merged = {kinds.get(k) for k in dsu.members(a_key)} | \
             {kinds.get(k) for k in dsu.members(b_key)}
    return not (merged & person and merged & company)


def resolve(
    graph: AttributionGraph,
    allowed_types: set[EntityType],
    merge_threshold: float = MERGE_LOG_ODDS_THRESHOLD,
    index=None,
) -> ResolutionResult:
    holders = index.holders if index is not None else graph.holders
    universe = index.universe() if index is not None else DEFAULT_UNIVERSE
    # ---- 1. group linking claims by unordered identifier pair -------------- #
    pairs: dict[tuple[str, str], list[Claim]] = defaultdict(list)
    #: object key -> claims asserting it, for co-reference pairing below
    by_object: dict[str, list[Claim]] = {}
    for c in graph.claims:
        if not isinstance(c.object, Identifier):
            continue
        if c.predicate in RELATIONSHIP_PREDICATES or c.predicate in ATTRIBUTE_PREDICATES:
            continue
        a, b = sorted([c.subject.key, c.object.key])
        pairs[(a, b)].append(c)
        by_object.setdefault(c.object.key, []).append(c)

    # ---- 1b. co-reference pairs -------------------------------------------- #
    #
    # Two subjects asserting the same object are candidates for being the same
    # entity, and that is the toolkit's central case: two domains carrying one
    # analytics ID, two sites declaring one seller account.
    #
    # Only directly-claimed subject-object edges were scored, so the pair
    # (a.example, b.example) was never assessed at all. Each leg had a single
    # correlation group, the corroboration rule correctly refused to merge on
    # one group, and the two-group evidence that actually existed was never
    # pooled -- so no inferential merge could occur under any threshold. The
    # earlier "the threshold is wrong" diagnosis was true and insufficient.
    #
    # Pooling both legs is the standard record-linkage construction: the
    # evidence for A == B is the evidence connecting each of them to what they
    # share, and two shared identifiers give two independent groups.
    for _object_key, sharing in by_object.items():
        subjects = {c.subject.key: c for c in sharing}
        if len(subjects) < 2:
            continue
        keys = sorted(subjects)
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                # Both legs, so the pair inherits the correlation groups of
                # each -- and the dependence correction still applies, so two
                # legs of one shared identifier do not become two lines of
                # evidence on their own.
                pairs[(ka, kb)].extend(
                    [c for c in sharing if c.subject.key in (ka, kb)])

    # ---- 2. score every candidate pair ------------------------------------ #
    scored: list[tuple[float, tuple[str, str], Assessment]] = []
    for pair, claims in pairs.items():
        a = assess(claims, holders, universe)
        # Sort and threshold on log-odds: the same quantity the threshold is
        # expressed in. Storing probability here is what let a nats threshold
        # be compared against a value that can never reach it.
        scored.append((a.log_odds, pair, a))

    # Highest confidence first: bounds the blast radius of an early bad merge.
    scored.sort(key=lambda t: -t[0])

    # ---- 3. must-not-link constraints ------------------------------------- #
    forbidden: set[tuple[str, str]] = set()
    for c in graph.claims:
        if c.predicate is Predicate.CONTRADICTS and isinstance(c.object, Identifier):
            forbidden.add(tuple(sorted([c.subject.key, c.object.key])))  # type: ignore[arg-type]
        if c.predicate in RELATIONSHIP_PREDICATES and isinstance(c.object, Identifier):
            # A director is not their company.
            forbidden.add(tuple(sorted([c.subject.key, c.object.key])))  # type: ignore[arg-type]

    dsu = _DSU()
    result = ResolutionResult(graph=graph)

    for log_odds, pair, a in scored:
        result.assessments[pair] = a
        # A registry identity assertion is a merge, not an inference to be
        # weighed against a threshold tuned for inferential evidence.
        mergeable = log_odds >= merge_threshold or (
            a.definitional and a.band in (Band.MODERATE_EVIDENCE, Band.STRONG_EVIDENCE)
        )
        if not mergeable:
            continue
        # Cluster-aware constraint check.
        #
        # Checking only the candidate pair let a contradiction be violated
        # transitively: with A~B, B~C both trusted and A!~C explicit, the
        # resolver produced one cluster containing all three. Before joining
        # two clusters, no member of one may be forbidden with any member of
        # the other.
        if _clusters_forbidden(dsu, forbidden, pair):
            result.rejected.append((
                *pair,
                "must-not-link: the merge would place contradicting identifiers "
                "in one cluster, directly or transitively"))
            continue
        if a.band in (Band.WEAK, Band.UNSUPPORTED) and not a.definitional:
            result.rejected.append((*pair, "insufficient independent evidence groups"))
            continue

        ka, kb = pair
        # Cluster-aware, for the same reason the must-not-link check above is.
        #
        # This compared the candidate pair's two endpoints only, so
        # A(person)~B and B~C(company) merged transitively into one cluster
        # containing both a person and a company. The endpoints were compatible
        # at each step; the result was not.
        types = {k: TYPE_BY_KIND.get(_kind_of(k), EntityType.UNKNOWN)
                 for k in dsu.members(ka) | dsu.members(kb) | {ka, kb}}
        present = set(types.values())
        if EntityType.COMPANY in present and EntityType.PERSON in present:
            result.rejected.append((
                *pair,
                "type conflict: the merge would place a person and a company "
                "in one cluster, directly or transitively"))
            continue
        dsu.union(ka, kb)

    # ---- 4. materialize entities ------------------------------------------ #
    clusters: dict[str, list[Identifier]] = defaultdict(list)
    for key, ident in graph.identifiers.items():
        clusters[dsu.find(key)].append(ident)

    graph.entities.clear()
    for idents in clusters.values():
        etype = _cluster_type(idents)
        if etype not in allowed_types and etype is not EntityType.UNKNOWN:
            continue
        if len(idents) == 1 and etype is EntityType.UNKNOWN:
            continue
        ent = Entity(type=etype, identifiers=set(idents))
        for i in idents:
            if i.kind in (IdKind.ORG_NAME, IdKind.PERSON_NAME):
                ent.labels[i.value] = ent.labels.get(i.value, 0.0) + 1.0
        _attach_attributes(graph, ent)
        best = max(
            (result.assessments[p].log_odds
             for p in result.assessments
             if p[0] in {i.key for i in idents} and p[1] in {i.key for i in idents}),
            default=0.0,
        )
        ent.log_odds = best
        graph.entities[ent.id] = ent

    return result


def _kind_of(key: str) -> IdKind:
    try:
        return IdKind(key.split(":", 1)[0])
    except ValueError:
        return IdKind.URL


def _cluster_type(idents: list[Identifier]) -> EntityType:
    votes = [TYPE_BY_KIND.get(i.kind) for i in idents]
    votes = [v for v in votes if v]
    if EntityType.COMPANY in votes:
        return EntityType.COMPANY
    if EntityType.PERSON in votes:
        return EntityType.PERSON
    if EntityType.PERSONA in votes:
        return EntityType.PERSONA
    return EntityType.UNKNOWN


def _attach_attributes(graph: AttributionGraph, ent: Entity) -> None:
    keys = {i.key for i in ent.identifiers}
    for c in graph.claims:
        if c.subject.key not in keys:
            continue
        if c.predicate in LABEL_PREDICATES:
            val = c.object.value if isinstance(c.object, Identifier) else str(c.object)
            ent.labels[val] = ent.labels.get(val, 0.0) + float(c.reliability) * 2
            continue
        if c.predicate not in ATTRIBUTE_PREDICATES:
            continue
        val = c.object.value if isinstance(c.object, Identifier) else str(c.object)
        ent.attributes.setdefault(c.predicate.value, [])
        if val not in ent.attributes[c.predicate.value]:
            ent.attributes[c.predicate.value].append(val)
