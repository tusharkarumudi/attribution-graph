"""Conditional dependence between correlation groups.

The flaw: correlation groups stop evidence stacking within a source, then the
model sums across groups — which assumes conditional independence. An operator
configuring one property emits analytics, favicon and header artifacts in one
action. Measured before the fix: three such artifacts scored ATTRIBUTED at
p = 1.0000, reading one decision as three confirmations.
"""

import pytest

from attribution_graph import (
    Claim,
    Identifier,
    IdKind,
    Predicate,
    Reliability,
    assess,
)
from attribution_graph.dependence import (
    WITHIN_CLASS_DISCOUNT,
    DependenceClass,
    adjust_for_dependence,
    classify_group,
    effective_independent_groups,
    sub_additive,
)
from attribution_graph.negative import COVERAGE, CoverageBasis


def _claim(group, pred, obj, kind, collector=None):
    return Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"), predicate=pred,
        object=Identifier(kind, obj), collector=collector or group.split("|")[0],
        source_url="https://a.example", reliability=Reliability.AUTHORITATIVE,
        correlation_group=group)


# ---- classification --------------------------------------------------------- #

@pytest.mark.parametrize("group,expected", [
    ("analytics|a.example", DependenceClass.SITE_SETUP),
    ("favicon|a.example", DependenceClass.SITE_SETUP),
    ("ads_txt|a.example", DependenceClass.MONETIZATION_SETUP),
    ("sellers_json|pubmatic|1", DependenceClass.MONETIZATION_SETUP),
    ("dns|a.example", DependenceClass.DNS_HOSTING),
    ("crtsh|a.example", DependenceClass.DNS_HOSTING),
    ("gleif|X", DependenceClass.CORPORATE_FILING),
    ("github|owner", DependenceClass.CODE_PUBLICATION),
    ("handle_root|kraken", DependenceClass.PERSONA_NAMING),
])
def test_groups_are_classified_by_common_cause(group, expected):
    assert classify_group(group) is expected


def test_unrecognised_evidence_fails_conservative_not_permissive():
    """This defaulted to INDEPENDENT, on the argument that a wrong discount is
    harder to notice than a missing one. The better
    point: for a model whose dominant failure mode is overconfidence,
    unrecognised evidence should fail conservative."""
    assert classify_group("something_novel|x") is DependenceClass.UNKNOWN
    assert WITHIN_CLASS_DISCOUNT[DependenceClass.UNKNOWN] < 1.0


def test_independence_must_be_declared_not_inferred():
    """A collector that wants full aggregation takes responsibility for the
    claim, rather than getting it by not matching a regex."""
    assert classify_group(
        "anything", declared=DependenceClass.INDEPENDENT
    ) is DependenceClass.INDEPENDENT
    assert WITHIN_CLASS_DISCOUNT[DependenceClass.INDEPENDENT] == 1.0


def test_declared_class_overrides_name_matching():
    """Dependence is a semantic property of what was observed, not of how the
    group happened to be labelled."""
    assert classify_group(
        "analytics|a.example", declared=DependenceClass.CORPORATE_FILING
    ) is DependenceClass.CORPORATE_FILING


def test_claims_can_carry_a_declared_dependence_class():
    import dataclasses

    from attribution_graph import Claim

    fields = {f.name for f in dataclasses.fields(Claim)}
    assert "dependence_class" in fields


def test_collector_name_is_a_fallback_for_uninformative_labels():
    assert classify_group("g1", collector="analytics_ids") is DependenceClass.SITE_SETUP


# ---- the correction --------------------------------------------------------- #

def test_one_setup_decision_is_not_three_confirmations():
    """The headline case. Uncorrected this reached p=1.0000 / ATTRIBUTED."""
    claims = [
        _claim("analytics|a", Predicate.SHARES_ANALYTICS_ID, "ga4:G-X",
               IdKind.ANALYTICS_ID),
        _claim("favicon|a", Predicate.SHARES_FAVICON, "mmh3:1", IdKind.FAVICON_MMH3),
        _claim("header|a", Predicate.PROFILE_BINDING, "header:x=y", IdKind.URL),
    ]
    a = assess(claims, lambda i: 2)
    assert a.dependence is not None
    assert a.dependence.discounted_groups >= 1
    assert a.dependence.reduction > 0
    # all three are one line of evidence
    assert a.independent_groups == 1


def test_genuinely_independent_classes_are_not_discounted():
    claims = [
        _claim("analytics|a", Predicate.SHARES_ANALYTICS_ID, "ga4:G-X",
               IdKind.ANALYTICS_ID),
        _claim("gleif|x", Predicate.SAME_AS, "5493001KJTIIGC8Y1R12", IdKind.LEI),
    ]
    a = assess(claims, lambda i: 2)
    assert a.dependence.discounted_groups == 0
    assert a.dependence.reduction == 0.0
    assert a.independent_groups == 2


def test_corroboration_counts_classes_not_raw_groups():
    """Two groups from one class are one line observed twice. Counting raw
    groups let a single decision satisfy the two-group requirement."""
    same_class = {"analytics|a": 5.0, "favicon|a": 5.0}
    two_classes = {"analytics|a": 5.0, "gleif|x": 5.0}
    assert effective_independent_groups(same_class) == 1
    assert effective_independent_groups(two_classes) == 2


def test_zero_weight_groups_do_not_count_toward_corroboration():
    assert effective_independent_groups({"analytics|a": 0.0, "gleif|x": 5.0}) == 1


def test_adjustment_keeps_the_strongest_group_whole():
    total, adj = adjust_for_dependence({"analytics|a": 10.0, "favicon|a": 4.0})
    d = WITHIN_CLASS_DISCOUNT[DependenceClass.SITE_SETUP]
    assert total == pytest.approx(10.0 + d * 4.0)
    assert adj.raw_total == 14.0


def test_sub_additive_orders_by_magnitude():
    assert sub_additive([3.0, 9.0, 1.0], 0.5) == pytest.approx(9.0 + 0.5 * 4.0)
    assert sub_additive([], 0.5) == 0.0
    assert sub_additive([7.0], 0.5) == 7.0


def test_negative_evidence_is_not_suppressed_by_the_discount():
    """A contradiction must survive; ordering is by magnitude, not sign."""
    total, _ = adjust_for_dependence({"dns|a": -6.0, "crtsh|a": 2.0})
    assert total < 0


def test_adjustment_renders_its_reasoning():
    _, adj = adjust_for_dependence({"analytics|a": 8.0, "favicon|a": 6.0})
    out = adj.render()
    assert "one decision" in out
    assert "unfitted" in out


def test_no_dependent_groups_renders_cleanly():
    _, adj = adjust_for_dependence({"gleif|x": 8.0})
    assert "all evidence classes distinct" in adj.render()


def test_dependence_appears_in_the_serialised_assessment():
    a = assess([_claim("analytics|a", Predicate.SHARES_ANALYTICS_ID, "ga4:G-X",
                       IdKind.ANALYTICS_ID),
                _claim("favicon|a", Predicate.SHARES_FAVICON, "mmh3:1",
                       IdKind.FAVICON_MMH3)], lambda i: 2)
    d = a.to_dict()
    assert "dependence_reduction" in d and "dependence_classes" in d


# ---- coverage provenance ---------------------------------------------------- #

def test_coverage_figures_declare_their_basis():
    """A review found the inconsistency: DEFAULT_COVERAGE was corrected from
    0.50 to 0.0 because a guessed figure gives an absence unearned weight — and
    that fix shipped beside a dozen equally guessed figures."""
    assert all(v.basis is not None for v in COVERAGE.values())


def test_most_coverage_figures_are_author_estimates():
    """Stated so a reader can discount them, rather than implied to be measured."""
    est = [k for k, v in COVERAGE.items()
           if v.basis is CoverageBasis.ESTIMATED and v.completeness > 0]
    assert est, "if this empties, someone measured them — update the docs"
    assert not any(COVERAGE[k].is_checkable for k in est)


def test_statutory_registers_have_a_defensible_basis():
    for src in ("companies_house_uk", "sec_edgar"):
        assert COVERAGE[src].basis is CoverageBasis.STATUTORY
        assert COVERAGE[src].is_checkable


def test_nothing_claims_to_be_measured_yet():
    """MEASURED is reserved for the calibration study output."""
    assert not [k for k, v in COVERAGE.items()
                if v.basis is CoverageBasis.MEASURED]
