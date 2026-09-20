"""Tests for the scoring model.

These encode the properties that matter more than any individual number:
selectivity dominates, correlated evidence cannot stack, stale evidence decays,
and one loud source cannot produce a finding on its own.
"""

from datetime import datetime, timedelta, timezone

import pytest

from attribution_graph.model import Claim, Identifier, IdKind, Predicate, Reliability
from attribution_graph.scoring import Band, assess, claim_llr, decay, selectivity

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def _claim(pred, obj_kind, obj_val, rel=Reliability.STRONG, group="g",
           age_days=0, collector="test"):
    return Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=pred,
        object=Identifier(obj_kind, obj_val),
        collector=collector,
        source_url="https://test",
        reliability=rel,
        correlation_group=group,
        observed_at=NOW - timedelta(days=age_days),
    )


def test_selectivity_monotonic():
    assert selectivity(1) < selectivity(10) < selectivity(10_000)


def test_unique_identifier_beats_common_one():
    c = _claim(Predicate.SHARES_ANALYTICS_ID, IdKind.ANALYTICS_ID, "G-XYZ")
    rare = claim_llr(c, holders=1, now=NOW)
    common = claim_llr(c, holders=400_000, now=NOW)
    assert rare > 10.0
    assert common < 1.5


def test_decay_reduces_stale_evidence():
    fresh = decay(Predicate.REGISTRANT, NOW, NOW)
    stale = decay(Predicate.REGISTRANT, NOW - timedelta(days=730), NOW)
    assert fresh == 1.0
    assert abs(stale - 0.5) < 1e-6
    assert decay(Predicate.INCORPORATED_IN, NOW - timedelta(days=9999), NOW) == 1.0


def test_correlated_evidence_does_not_stack():
    """400 commits from one repo must not certify an attribution."""
    many = [
        _claim(Predicate.COMMIT_EMAIL, IdKind.EMAIL, f"dev{i}@x.example", group="repo1")
        for i in range(400)
    ]
    a = assess(many, holder_lookup=lambda i: 1, now=NOW)
    assert a.independent_groups == 1
    assert a.band in (Band.WEAK, Band.UNSUPPORTED), "single group must not exceed WEAK"


def test_two_independent_groups_can_reach_moderate_evidence():
    claims = [
        _claim(Predicate.SHARES_ANALYTICS_ID, IdKind.ANALYTICS_ID, "G-XYZ", group="analytics"),
        _claim(Predicate.SELLER_OF, IdKind.SELLER_ID, "pubmatic.com/1", group="sellers_json"),
    ]
    a = assess(claims, holder_lookup=lambda i: 1, now=NOW)
    assert a.independent_groups == 2
    assert a.band in (Band.MODERATE_EVIDENCE, Band.STRONG_EVIDENCE)


def test_corroborative_only_predicates_do_not_count_as_independent():
    claims = [
        _claim(Predicate.SHARES_ANALYTICS_ID, IdKind.ANALYTICS_ID, "G-XYZ", group="analytics"),
        _claim(Predicate.TIMEZONE_HINT, IdKind.PERSON_NAME, "UTC+5", group="tz"),
        _claim(Predicate.CO_HOSTED, IdKind.DOMAIN, "b.example", group="host"),
    ]
    a = assess(claims, holder_lookup=lambda i: 1, now=NOW)
    assert a.independent_groups == 1


def test_demoted_claim_contributes_nothing():
    c = _claim(Predicate.CO_HOSTED, IdKind.DOMAIN, "b.example")
    c.weight = 0.0
    assert claim_llr(c, holders=1, now=NOW) == 0.0


def test_contradiction_pushes_score_down():
    good = _claim(Predicate.SHARES_ANALYTICS_ID, IdKind.ANALYTICS_ID, "G-XYZ", group="a")
    bad = _claim(Predicate.CONTRADICTS, IdKind.ORG_NAME, "Other Ltd", group="b")
    with_bad = assess([good, bad], lambda i: 1, now=NOW)
    without = assess([good], lambda i: 1, now=NOW)
    assert with_bad.log_odds < without.log_odds


def test_authoritative_registry_assertion_stands_alone():
    """GLEIF saying an LEI belongs to a legal name is definitional, not
    inferential -- requiring corroboration would make the system unable to
    resolve a company to its own LEI."""
    # The collector must be an engine-recognised registry: the carve-out is
    # granted by the engine, never claimed by the claim.
    c = _claim(Predicate.SAME_AS, IdKind.LEI, "5493001KJTIIGC8Y1R12",
               rel=Reliability.AUTHORITATIVE, group="gleif", collector="gleif")
    a = assess([c], holder_lookup=lambda i: 1, now=NOW)
    assert a.definitional is True
    assert a.independent_groups == 1
    assert a.band in (Band.MODERATE_EVIDENCE, Band.STRONG_EVIDENCE)


def test_non_authoritative_same_as_gets_no_carve_out():
    c = _claim(Predicate.SAME_AS, IdKind.ORG_NAME, "Example Ltd",
               rel=Reliability.MODERATE, group="scraped")
    a = assess([c], holder_lookup=lambda i: 1, now=NOW)
    assert a.definitional is False
    assert a.band in (Band.WEAK, Band.UNSUPPORTED)


def test_corroboration_invariant_holds():
    """GROUP_CAP must sit below |log(PRIOR_ODDS)|.

    This inequality is what makes corroboration structural rather than advisory:
    it guarantees no single inferential group can cross the merge threshold,
    whatever its strength. Nothing else asserts it directly, so raising the cap
    would break three behaviour tests confusingly instead of this one clearly.
    """
    import math

    from attribution_graph.scoring import GROUP_CAP, PRIOR_ODDS
    assert abs(math.log(PRIOR_ODDS)) > GROUP_CAP, (
        "a single inferential correlation group can now carry a merge on its own"
    )


def test_definitional_cap_can_exceed_the_prior():
    """The carve-out only works if the definitional cap clears the prior --
    otherwise a registry assertion still cannot resolve on its own."""
    import math

    from attribution_graph.scoring import DEFINITIONAL_GROUP_CAP, PRIOR_ODDS
    assert abs(math.log(PRIOR_ODDS)) < DEFINITIONAL_GROUP_CAP


@pytest.mark.parametrize("p", [1.0, 0.999, 0.99, 0.96, 0.9, 0.8])
def test_single_source_cap_holds_at_high_probability(p):
    """The cap has to bite precisely when the score is high.

    band_for used `min` over a rank where ATTRIBUTED is 0, so it returned the
    *stronger* band and the cap did nothing above p=0.55. Every synthetic test
    passed because their probabilities were already low enough that both
    branches agreed; a real run against live data is what surfaced it.
    """
    from attribution_graph.scoring import Band, band_for

    assert band_for(p, 1) is Band.WEAK, "one group must never exceed WEAK"
    assert band_for(p, 2) is not Band.WEAK, "two groups must not be capped"


def test_cap_does_not_promote_a_weak_score():
    from attribution_graph.scoring import Band, band_for

    assert band_for(0.01, 1) is Band.UNSUPPORTED
    assert band_for(0.3, 1) is Band.WEAK


def test_definitional_privilege_cannot_be_self_granted():
    """A subject-controlled claim marked AUTHORITATIVE used to merge on its own.

    An audit built one SAME_AS claim sourced from the subject's own page, set
    Reliability.AUTHORITATIVE, and resolve() merged evil.example into
    Victim Corp. Reliability is a scalar the claim author supplies, so keying
    the strongest privilege in the model on it made the carve-out self-service.
    """
    from attribution_graph import (
        AttributionGraph,
        EntityType,
        assess,
        is_definitional,
        resolve,
    )

    hostile = Claim(
        subject=Identifier(IdKind.DOMAIN, "evil.example"),
        predicate=Predicate.SAME_AS,
        object=Identifier(IdKind.ORG_NAME, "Victim Corp"),
        collector="subject_html", source_url="https://evil.example/",
        reliability=Reliability.AUTHORITATIVE, correlation_group="x")

    assert not is_definitional(hostile)
    assert not assess([hostile], lambda i: 1).definitional

    g = AttributionGraph(case_ref="T")
    g.add_claim(hostile)
    resolve(g, {EntityType.COMPANY})
    assert not [e for e in g.entities.values() if len(e.identifiers) > 1]


def test_registry_collectors_retain_the_carve_out():
    """The exemption still works for its intended case: a registry binding an
    entity to an identifier it issues."""
    from attribution_graph import is_definitional

    registry = Claim(
        subject=Identifier(IdKind.ORG_NAME, "Example Media Ltd"),
        predicate=Predicate.SAME_AS,
        object=Identifier(IdKind.LEI, "5493001KJTIIGC8Y1R12"),
        collector="gleif", source_url="https://api.gleif.org/",
        reliability=Reliability.AUTHORITATIVE, correlation_group="g")
    assert is_definitional(registry)


def test_only_identity_predicates_can_be_definitional():
    """Found by mutation testing: deleting the predicate check in
    is_definitional() left the whole suite green.

    The check works, but nothing exercised it — so a refactor could have
    removed it silently and handed the merge carve-out to any AUTHORITATIVE
    claim from a trusted collector, including infrastructure observations like
    CO_HOSTED that assert co-location rather than identity.
    """
    from attribution_graph import is_definitional
    from attribution_graph.scoring import DEFINITIONAL_PREDICATES

    assert {Predicate.SAME_AS} == DEFINITIONAL_PREDICATES

    for pred in (Predicate.CO_HOSTED, Predicate.SHARES_ANALYTICS_ID,
                 Predicate.SELLER_OF, Predicate.REGISTRANT):
        c = Claim(
            subject=Identifier(IdKind.DOMAIN, "a.example"), predicate=pred,
            object=Identifier(IdKind.DOMAIN, "b.example"),
            collector="rdap",                      # a trusted collector
            source_url="https://rdap.org/",
            reliability=Reliability.AUTHORITATIVE, # and the top tier
            correlation_group="dns|a")
        assert not is_definitional(c), (
            f"{pred.value} asserts a relationship, not an identity, and must "
            "not bypass corroboration")


def test_definitional_requires_positive_weight():
    """A demoted claim is not definitional, whatever else it satisfies."""
    from attribution_graph import is_definitional

    c = Claim(
        subject=Identifier(IdKind.ORG_NAME, "Example Ltd"),
        predicate=Predicate.SAME_AS,
        object=Identifier(IdKind.LEI, "5493001KJTIIGC8Y1R12"),
        collector="gleif", source_url="https://api.gleif.org/",
        reliability=Reliability.AUTHORITATIVE, correlation_group="g")
    assert is_definitional(c)
    c.weight = 0.0
    assert not is_definitional(c)
