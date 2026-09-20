"""Negative evidence weighting."""

from attribution_graph import (
    COVERAGE,
    AbsenceKind,
    Identifier,
    IdKind,
    absence_claim,
    absence_llr,
    common_control_expectations,
    expectation_claims,
    render_expectations,
)


def test_not_checked_contributes_nothing():
    assert absence_llr("companies_house_uk", AbsenceKind.NOT_CHECKED) == 0.0


def test_complete_source_absence_outweighs_incomplete_source_absence():
    """Absent from Companies House means far more than absent from Wayback."""
    strong = absence_llr("companies_house_uk", AbsenceKind.CHECKED_ABSENT)
    weak = absence_llr("wayback", AbsenceKind.CHECKED_ABSENT)
    assert strong < weak < 0


def test_expected_absence_weighs_more_than_incidental_absence():
    a = absence_llr("rdap", AbsenceKind.CHECKED_ABSENT)
    b = absence_llr("rdap", AbsenceKind.EXPECTED_ABSENT)
    assert b < a


def test_absence_is_bounded():
    for src in COVERAGE:
        for k in (AbsenceKind.CHECKED_ABSENT, AbsenceKind.EXPECTED_ABSENT):
            assert absence_llr(src, k) >= -6.0


def test_undeclared_source_contributes_nothing_to_an_absence():
    """Was a 0.50 "coin flip", which gave every unregistered source's empty
    result real weight (~0.26 nats). A coin flip is not neutrality: you cannot
    infer anything from an absence in a source whose coverage you never
    measured, and treating ignorance as 50% confidence lets a lookup service
    with no published coverage argue against a link.

    Zero forces declaration.
    """
    c = absence_claim(
        Identifier(IdKind.ORG_NAME, "X"), "never_declared_anywhere",
        AbsenceKind.CHECKED_ABSENT, query_url="https://x",
        what_was_sought="a record")
    assert c.weight == 0.0


def test_declared_coverage_makes_an_absence_count():
    strong = absence_claim(
        Identifier(IdKind.ORG_NAME, "X"), "companies_house_uk",
        AbsenceKind.CHECKED_ABSENT, query_url="https://x",
        what_was_sought="a UK company")
    weak = absence_claim(
        Identifier(IdKind.ORG_NAME, "X"), "reverse_publisher_id",
        AbsenceKind.CHECKED_ABSENT, query_url="https://x",
        what_was_sought="other domains")
    assert strong.weight > 0.5
    assert weak.weight == 0.0, "a source with unpublished coverage says nothing"


def test_absence_can_carry_a_coverage_note():
    """'No record found' reads as a finding by default. For a source that does
    not publish coverage it is nothing of the sort, and the distinction has to
    travel with the claim."""
    c = absence_claim(
        Identifier(IdKind.ORG_NAME, "X"), "reverse_publisher_id",
        AbsenceKind.EXPECTED_ABSENT, query_url="https://x",
        what_was_sought="other domains",
        note="services publish no coverage; absence is uninformative")
    assert "uninformative" in c.raw["note"]

def test_absence_claim_carries_interpretation():
    c = absence_claim(
        Identifier(IdKind.ORG_NAME, "Acme Ltd"), "companies_house_uk",
        AbsenceKind.CHECKED_ABSENT,
        query_url="https://api.ch/search", what_was_sought="UK registration")
    assert c.weight > 0
    assert "strong" in c.raw["interpretation"]
    assert c.raw["source_completeness"] == 0.98


def test_not_checked_claim_carries_no_weight():
    c = absence_claim(
        Identifier(IdKind.ORG_NAME, "Acme Ltd"), "sec_edgar",
        AbsenceKind.NOT_CHECKED, query_url="", what_was_sought="filings")
    assert c.weight == 0.0
    assert "not a negative result" in c.raw["interpretation"]


def test_expectations_render_all_three_states():
    exps = common_control_expectations("a.example", "b.example")
    exps[0].satisfied = True
    exps[1].satisfied = False
    out = render_expectations(exps)
    assert "confirmed" in out and "not found" in out and "not checked" in out
    assert "gaps in the investigation, not negative findings" in out


def test_only_failed_expectations_emit_claims():
    exps = common_control_expectations("a.example", "b.example")
    exps[0].satisfied = True
    exps[1].satisfied = False
    claims = expectation_claims(Identifier(IdKind.DOMAIN, "a.example"), exps)
    assert len(claims) == 1
    assert claims[0].raw["absence_kind"] == "expected_absent"
