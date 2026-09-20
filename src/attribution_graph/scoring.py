"""Probabilistic attribution scoring.

The whole model in one line:

    log_odds(same entity) = log(prior_odds) + SUM over correlation groups of
                            [ max(llr in group) + log(1 + n_sources_in_group) ]

where each individual observation contributes

    llr = reliability * decay(age) * log(1 / selectivity)

Selectivity is the quantity that does the work. It is *measured* from the
observation corpus, not asserted by the analyst. An identifier held by one entity
is worth ~14 nats; an identifier held by half the internet is worth ~0.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from .dependence import (
    DependenceClass,
    adjust_for_dependence,
    effective_independent_groups,
)
from .model import Claim, Identifier, Predicate, Reliability

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

#: Base rate that two arbitrary identifiers in a large corpus denote the same
#: entity. Low by design: with 1e5 candidates, anything higher links everything.
PRIOR_ODDS = 1e-5

#: Floor on selectivity, caps any single observation at ~13.8 nats.
SELECTIVITY_FLOOR = 1e-6

#: Ceiling on total contribution from one correlation group. Deliberately below
#: |log(PRIOR_ODDS)| so that no single inferential group can carry a merge on its
#: own -- corroboration is structural, not advisory.
GROUP_CAP = 8.0

#: Definitional groups (authoritative registry identity assertions) are exempt
#: from GROUP_CAP. Capping them would make it impossible for GLEIF to tell the
#: system that an LEI belongs to the company it belongs to.
DEFINITIONAL_GROUP_CAP = 20.0

#: Laplace smoothing for the selectivity estimate.
ALPHA = 0.5

#: Assumed corpus size when no corpus counter is wired in.
DEFAULT_UNIVERSE = 1_000_000

#: Evidence half-life in days, per predicate. inf = does not decay.
HALF_LIFE_DAYS: dict[Predicate, float] = {
    Predicate.INCORPORATED_IN: math.inf,
    Predicate.LEGAL_NAME: math.inf,
    Predicate.SELLER_OF: 1095.0,
    Predicate.OWNER_DOMAIN: 1095.0,
    Predicate.MANAGER_DOMAIN: 730.0,
    Predicate.OFFICER_OF: 1825.0,
    Predicate.BENEFICIAL_OWNER_OF: 1825.0,
    Predicate.PARENT_OF: 1825.0,
    Predicate.REGISTRANT: 730.0,
    Predicate.REGISTERED_ADDRESS: 1460.0,
    Predicate.COMMIT_EMAIL: 1095.0,
    Predicate.KEY_BINDING: 1460.0,
    Predicate.PROFILE_BINDING: 730.0,
    Predicate.SHARES_ANALYTICS_ID: 545.0,
    Predicate.SHARES_GRAVATAR: 1095.0,
    Predicate.SHARES_CERT: 365.0,
    Predicate.SHARES_FAVICON: 365.0,
    Predicate.CO_HOSTED: 180.0,
    Predicate.TIMEZONE_HINT: 365.0,
    Predicate.SAME_AS: math.inf,
}

#: Predicates that are corroborative only. They can raise a score that already
#: has independent support, but never establish a link on their own.
CORROBORATIVE_ONLY: frozenset[Predicate] = frozenset({
    Predicate.TIMEZONE_HINT,
    Predicate.CO_HOSTED,
    Predicate.SHARES_FAVICON,
})


# --------------------------------------------------------------------------- #
# Bands
# --------------------------------------------------------------------------- #

class Band(StrEnum):
    """Evidence-strength bands.

    Renamed from probability language (ATTRIBUTED / PROBABLE / POSSIBLE) after
    an external audit made the point that the strongest surface is the one
    downstream users quote. A disclaimer in a methodology file does not travel
    with the word "ATTRIBUTED" once it is pasted into a report.

    These name the strength of the evidence, which is what the model actually
    measures. They will be replaced by probability language only when a
    calibration study supplies a fitted score-to-frequency mapping.
    """

    STRONG_EVIDENCE = "STRONG_EVIDENCE"
    MODERATE_EVIDENCE = "MODERATE_EVIDENCE"
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"
    WEAK = "WEAK"
    UNSUPPORTED = "UNSUPPORTED"


#: ICD 203 estimative language, so findings are directly quotable in a report.
ESTIMATIVE: dict[Band, str] = {
    # Evidence-strength phrasing, not ICD 203 estimative probability.
    #
    # "almost certainly" is a claim about frequency and the model has not
    # measured one. These describe how much the evidence supports the link,
    # which is what is actually known. ICD 203 language returns when a
    # calibration study supplies a fitted score-to-frequency mapping.
    Band.STRONG_EVIDENCE: "strongly supported by the evidence",
    Band.MODERATE_EVIDENCE: "moderately supported by the evidence",
    Band.LIMITED_EVIDENCE: "weakly supported by the evidence",
    Band.WEAK: "insufficiently supported",
    Band.UNSUPPORTED: "contradicted or unsupported",
}

_BAND_EDGES = [
    (0.95, Band.STRONG_EVIDENCE),
    (0.80, Band.MODERATE_EVIDENCE),
    (0.55, Band.LIMITED_EVIDENCE),
    (0.20, Band.WEAK),
]

#: Minimum independent correlation groups required to report above WEAK.
MIN_INDEPENDENT_GROUPS = 2

#: Predicates that are *definitional* rather than inferential when they come
#: from an authoritative registry. GLEIF stating that an LEI belongs to a legal
#: name is not evidence about an identity -- it is the identity. Requiring
#: corroboration here would make it impossible to ever resolve a company to its
#: own LEI or CIK, which is the opposite of conservative.
DEFINITIONAL_PREDICATES: frozenset[Predicate] = frozenset({Predicate.SAME_AS})


#: Collectors permitted to make a definitional identity assertion.
#:
#: The carve-out exists so a statutory registry can bind an entity to an
#: identifier it *issues* -- GLEIF saying an LEI belongs to a legal name is not
#: offering evidence about an identity, it is the identity. That privilege must
#: be granted by the engine, never claimed by the claim.
#:
#: It previously keyed on ``Reliability.AUTHORITATIVE``, which any collector can
#: set on any claim. An audit constructed a single SAME_AS claim sourced from
#: the subject's own page, marked it AUTHORITATIVE, and `resolve()` merged
#: `evil.example` into `Victim Corp` on that alone. A subject-controlled
#: assertion self-granted the strongest privilege in the model.
#: Listed by their exact registered names, including the agent tool names,
#: because both are engine-controlled. A name is matched literally: no
#: substring or pattern matching, so a collector called
#: "gleif_lookalike_from_subject_page" gets nothing.
DEFINITIONAL_COLLECTORS: frozenset[str] = frozenset({
    # collectors
    "gleif", "sec_edgar", "companies_house_uk", "cninfo_disclosure",
    "opencorporates", "rdap", "icij_offshore_leaks",
    # agent tools wrapping the same sources, listed with the exact prefix the
    # engine emits -- not matched by suffix
    "lookup_gleif", "lookup_companies_house", "lookup_rdap",
    "agent:lookup_gleif", "agent:lookup_companies_house", "agent:lookup_rdap",
})

#: Source classes whose assertions can be definitional. A registry issuing an
#: identifier qualifies; anything the subject authored never does.
DEFINITIONAL_SOURCE_CLASSES: frozenset[str] = frozenset({
    "public_registry", "public_protocol",
})


def is_definitional(claim: Claim) -> bool:
    """Whether a claim may bypass the corroboration requirement.

    Requires all four:

    1. a definitional predicate (SAME_AS, not an inference),
    2. AUTHORITATIVE reliability,
    3. positive weight (a demoted claim is not definitional),
    4. **an engine-recognised registry collector or source class.**

    The fourth is the one that matters. Reliability is a scalar the claim author
    supplies; without a trusted-source check, the carve-out is self-service.
    """
    if claim.predicate not in DEFINITIONAL_PREDICATES:
        return False
    if claim.reliability is not Reliability.AUTHORITATIVE:
        return False
    if claim.weight <= 0.0:
        return False

    # Exact match on the whole collector name. This used to split on ":" and
    # take the last segment, so `subject_html:gleif` -- a name any collector can
    # choose -- inherited the strongest privilege in the model. A trust check
    # that can be satisfied by string construction is not a trust check.
    collector = (claim.collector or "").strip().lower()
    if collector in DEFINITIONAL_COLLECTORS:
        return True

    source_class = getattr(claim, "source_class", None)
    value = getattr(source_class, "value", source_class)
    return str(value or "").lower() in DEFINITIONAL_SOURCE_CLASSES


def band_for(p: float, independent_groups: int, definitional: bool = False) -> Band:
    if definitional:
        return _raw_band(p)
    if independent_groups < MIN_INDEPENDENT_GROUPS:
        # Single-source attribution is a lead, not a finding, so take the
        # WEAKER of the raw band and the cap.
        #
        # _band_rank orders ATTRIBUTED=0 .. UNSUPPORTED=4, so the weaker band is
        # the HIGHER rank. This was `min`, which silently returned the stronger
        # band and disabled the cap entirely whenever the probability was high
        # -- exactly the case the cap exists for. Synthetic tests missed it
        # because their probabilities were already low, so both branches agreed.
        return max(Band.WEAK, _raw_band(p), key=_band_rank)
    return _raw_band(p)


def _raw_band(p: float) -> Band:
    for edge, band in _BAND_EDGES:
        if p >= edge:
            return band
    return Band.UNSUPPORTED


def _band_rank(b: Band) -> int:
    order = [Band.STRONG_EVIDENCE, Band.MODERATE_EVIDENCE,
             Band.LIMITED_EVIDENCE, Band.WEAK, Band.UNSUPPORTED]
    return order.index(b)


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

def selectivity(holders: int, universe: int = DEFAULT_UNIVERSE, cardinality: int = 2) -> float:
    """P(two unrelated entities both carry this identifier value).

    ``holders`` is the number of distinct entities observed with the value.
    """
    holders = max(holders, 1)
    s = (holders - 1 + ALPHA) / (universe + ALPHA * cardinality)
    return max(s, SELECTIVITY_FLOOR)


def decay(predicate: Predicate, observed_at: datetime, now: datetime | None = None) -> float:
    hl = HALF_LIFE_DAYS.get(predicate, 730.0)
    if math.isinf(hl):
        return 1.0
    now = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_days = max((now - observed_at).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / hl)


def claim_llr(
    claim: Claim,
    holders: int,
    universe: int = DEFAULT_UNIVERSE,
    now: datetime | None = None,
) -> float:
    """Log-likelihood ratio contributed by a single observation."""
    if claim.weight <= 0.0:
        return 0.0

    if claim.predicate is Predicate.CONTRADICTS:
        return -float(claim.reliability) * claim.weight * 4.0

    sel = selectivity(holders, universe)
    base = math.log(1.0 / sel)
    return (
        float(claim.reliability)
        * claim.weight
        * decay(claim.predicate, claim.observed_at, now)
        * base
    )


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

@dataclass
class Assessment:
    log_odds: float
    probability: float
    band: Band
    estimative: str
    independent_groups: int
    top_evidence: list[tuple[str, float]]
    #: What the conditional-dependence correction did. ``None`` only for
    #: assessments constructed directly in tests.
    dependence: object | None = None
    definitional: bool = False

    def to_dict(self) -> dict:
        return {
            "probability": round(self.probability, 4),
            "log_odds": round(self.log_odds, 3),
            "band": self.band.value,
            "estimative_language": self.estimative,
            "evidence_score_nats": round(self.log_odds, 3)
            if hasattr(self, "log_odds") else None,
            "calibration_status": "unvalidated",
            "probability_note": (
                "`probability` is the logistic transform of an evidence score, "
                "not a measured frequency. log(1/selectivity) informs the "
                "denominator of a likelihood ratio; P(evidence | same entity) "
                "is not modelled. Read the band as an ordering until a "
                "calibration study fits a score-to-frequency mapping."),
            "independent_evidence_groups": self.independent_groups,
            **({"dependence_reduction": round(self.dependence.reduction, 3),
                "dependence_classes": self.dependence.classes}
               if self.dependence is not None else {}),
            "authoritative_definitional": self.definitional,
            "top_evidence": [
                {"correlation_group": g, "llr": round(v, 3)} for g, v in self.top_evidence
            ],
        }


def canonical_group(group: str) -> str:
    """Canonical form of a correlation-group label.

    Applies the same normalization as identifier keying, then strips a trailing
    numeric or single-character discriminator -- the shape a splitting attack
    produces (``analytics|a.example|0``, ``|1``, ``|2``).
    """
    from .obfuscation import normalize_value

    g = normalize_value(group, case_fold=True, collapse_space=True).normalized
    g = re.sub(r"\|[0-9]{1,3}$", "", g)
    return g


def assess(
    claims: list[Claim],
    holder_lookup,
    universe: int = DEFAULT_UNIVERSE,
    prior_odds: float = PRIOR_ODDS,
    now: datetime | None = None,
) -> Assessment:
    """Aggregate a set of linking claims into a calibrated assessment.

    ``holder_lookup`` maps an Identifier to the count of distinct entities
    observed carrying it.

    Correlated observations are aggregated within their correlation group as
    ``max + log(1 + n)`` rather than summed. Summing is what lets one chatty
    collector -- 400 commits from one repo, 50 subdomains on one host -- drive
    the posterior to certainty on what is really a single observation.
    """
    # Correlation groups are canonicalized before bucketing. A collector that
    # builds its group label from a raw observed value would otherwise let an
    # adversary split one fact across several groups by publishing cosmetic
    # variants -- four spellings of one analytics ID turning WEAK into
    # ATTRIBUTED. Canonicalizing here makes the defense structural rather than
    # dependent on every collector normalizing correctly.
    groups: dict[str, list[float]] = {}
    substantive_groups: set[str] = set()
    definitional_groups: set[str] = set()
    # Collector name per group, so dependence classification can fall back to
    # the collector when a group label is uninformative.
    group_collector_names: dict[str, str] = {}
    #: Dependence classes declared on claims, per canonical group. Typed
    #: metadata was added to Claim but never gathered here, so "declared always
    #: wins" was true of the helper and false of production scoring.
    group_declared: dict[str, object] = {}
    definitional = False

    for c in claims:
        if is_definitional(c):
            definitional = True
            definitional_groups.add(canonical_group(c.correlation_group))
        target = c.object if isinstance(c.object, Identifier) else c.subject
        h = holder_lookup(target)
        llr = claim_llr(c, h, universe, now)
        gkey = canonical_group(c.correlation_group)
        groups.setdefault(gkey, []).append(llr)
        group_collector_names.setdefault(gkey, c.collector)
        declared_name = getattr(c, "dependence_class", "") or ""
        if declared_name:
            try:
                declared = DependenceClass(declared_name)
            except ValueError:
                declared = None
            if declared is not None:
                existing = group_declared.get(gkey)
                if existing is not None and existing != declared:
                    raise ValueError(
                        f"correlation group {gkey!r} carries conflicting "
                        f"dependence classes: {existing} and {declared}. One "
                        "group is one observation; it cannot arise from two "
                        "different causes.")
                group_declared[gkey] = declared
        if c.predicate not in CORROBORATIVE_ONLY:
            substantive_groups.add(gkey)

    contributions: list[tuple[str, float]] = []
    per_group: dict[str, float] = {}
    group_collectors: dict[str, str] = {}

    for gname, llrs in groups.items():
        pos = [v for v in llrs if v > 0]
        neg = sum(v for v in llrs if v < 0)
        if pos:
            g = max(pos) + math.log(1 + len(pos) - 1) if len(pos) > 1 else max(pos)
            cap = DEFINITIONAL_GROUP_CAP if gname in definitional_groups else GROUP_CAP
            g = min(g, cap)
        else:
            g = 0.0
        total = g + neg
        per_group[gname] = total
        group_collectors[gname] = group_collector_names.get(gname, "")
        contributions.append((gname, total))

    # Correlation groups stop evidence stacking *within* a source. Summing
    # across groups then assumes they are conditionally independent, and they
    # frequently are not: one operator configuring one property emits analytics,
    # favicon and header artifacts together. Uncorrected, three artifacts of one
    # decision scored ATTRIBUTED at p=1.0.
    #
    # Groups sharing a dependence class aggregate sub-additively. The discount
    # factors are principled but unfitted, like every constant here.
    adjusted_total, dependence = adjust_for_dependence(per_group, group_collectors, group_declared)
    log_odds = math.log(prior_odds) + adjusted_total

    p = 1.0 / (1.0 + math.exp(-log_odds)) if log_odds > -700 else 0.0

    # Corroboration counts distinct dependence classes, not raw groups: two
    # groups from one class are one line of evidence observed twice, and the
    # requirement exists to demand two lines.
    substantive_llrs = {g: v for g, v in per_group.items() if g in substantive_groups}
    n_ind = effective_independent_groups(substantive_llrs, group_collectors,
                                            group_declared)
    b = band_for(p, n_ind, definitional)
    contributions.sort(key=lambda kv: -abs(kv[1]))

    return Assessment(
        log_odds=log_odds,
        probability=p,
        band=b,
        estimative=ESTIMATIVE[b],
        independent_groups=n_ind,
        dependence=dependence,
        top_evidence=contributions[:5],
        definitional=definitional,
    )
