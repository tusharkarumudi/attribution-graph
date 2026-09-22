"""Willful obfuscation: normalization, group-inflation defense, and tracking.

Every test here corresponds to a technique an adversary uses to avoid being
correlated. The group-inflation ones are the important set: they are attacks on
the scoring model itself rather than on the data.
"""

import pytest

from attribution_graph import (
    Claim,
    Identifier,
    IdKind,
    Obfuscation,
    ObfuscationLog,
    Predicate,
    Reliability,
    assess,
    canonical,
    normalize_value,
    scan_text,
)
from attribution_graph.scoring import canonical_group


def _claim(group: str, value: str) -> Claim:
    return Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=Predicate.SHARES_ANALYTICS_ID,
        object=Identifier(IdKind.ANALYTICS_ID, value),
        collector="t", source_url="https://a.example",
        reliability=Reliability.AUTHORITATIVE, correlation_group=group)


# ---- correlation-group inflation ------------------------------------------ #
#
# The attack: publish cosmetic variants of one identifier so one observation
# scores as several independent ones. Measured before the fix: 4 groups,
# ATTRIBUTED, from a single fact.

@pytest.mark.parametrize("variants,labels", [
    (["ga4:G-ABC123", "ga4:g-abc123", "ga4:G-ABC123\u200e", "ga4: G-ABC123"],
     ["analytics|a.example|0", "analytics|a.example|1",
      "analytics|a.example|2", "analytics|a.example|3"]),
    (["ga4:G-ABC123", "ga4:g-abc123"],
     ["Analytics|A.Example", "analytics|a.example"]),
    (["ga4:G-ABC1", "ga4:G-ABC1"],
     ["analytics|a.example", "analytics|a.exam\u200bple"]),
])
def test_group_inflation_is_blocked(variants, labels):
    claims = [_claim(g, v) for g, v in zip(labels, variants, strict=True)]
    a = assess(claims, lambda i: 1)
    assert a.independent_groups == 1, "cosmetic variants must not split a group"
    assert a.band.value in ("WEAK", "UNSUPPORTED")


def test_genuinely_independent_groups_still_count():
    """The defense must not collapse real corroboration."""
    claims = [_claim("analytics|a.example", "ga4:G-ABC123"),
              _claim("sellers_json|pubmatic|156423", "adsense:1234567890123456")]
    assert assess(claims, lambda i: 1).independent_groups == 2


@pytest.mark.parametrize("a,b", [
    ("analytics|a.example|0", "analytics|a.example|1"),
    ("Analytics|A.Example", "analytics|a.example"),
    ("analytics|a.exam\u200bple", "analytics|a.example"),
    ("analytics|a.example ", "analytics|a.example"),
])
def test_canonical_group_collapses_variants(a, b):
    assert canonical_group(a) == canonical_group(b)


def test_canonical_group_keeps_distinct_labels_distinct():
    assert canonical_group("analytics|a.example") != canonical_group("gleif|X")


# ---- identifier normalization --------------------------------------------- #

@pytest.mark.parametrize("kind,a,b,desc", [
    (IdKind.ORG_NAME, "Example Media Ltd", "Ex\u0430mple Media Ltd", "Cyrillic a"),
    (IdKind.DOMAIN, "example.com", "exam\u200bple.com", "zero-width space"),
    (IdKind.ANALYTICS_ID, "adsense:123", "adsense:123\u200e", "LTR mark"),
    (IdKind.ANALYTICS_ID, "ga4:G-ABC123", "ga4:g-abc123", "case"),
    (IdKind.ORG_NAME, "Acme Ltd", "Acme\u00a0Ltd", "non-breaking space"),
    (IdKind.ORG_NAME, "Acme Ltd", "Acme  Ltd", "double space"),
    (IdKind.EMAIL, "ops@example.com", "OPS@Example.com", "case"),
    (IdKind.ORG_NAME, "ACME", "\uff21\uff23\uff2d\uff25", "fullwidth"),
])
def test_obfuscated_identifiers_normalize_together(kind, a, b, desc):
    assert Identifier(kind, a).key == Identifier(kind, b).key, desc


def test_genuinely_different_identifiers_stay_apart():
    """A confusable hyphen folds to ASCII, but 'exam-ple' is not 'example'."""
    assert Identifier(IdKind.DOMAIN, "example.com").key != \
        Identifier(IdKind.DOMAIN, "exam\u2010ple.com").key
    assert Identifier(IdKind.ORG_NAME, "Acme Ltd").key != \
        Identifier(IdKind.ORG_NAME, "Acme Holdings Ltd").key


def test_case_sensitive_kinds_are_not_folded():
    """Person and org names keep case; it carries information."""
    assert Identifier(IdKind.PERSON_NAME, "Jane Doe").value == "Jane Doe"


# ---- obfuscation is recorded, not silently repaired ----------------------- #

def test_normalization_reports_what_it_changed():
    r = normalize_value("Ex\u0430mple\u200b Ltd")
    assert r.changed
    assert Obfuscation.HOMOGLYPH in r.findings
    assert Obfuscation.ZERO_WIDTH in r.findings
    assert r.deliberate


def test_incidental_changes_are_not_flagged_deliberate():
    r = normalize_value("  Acme   Ltd  ")
    assert r.changed
    assert not r.deliberate
    assert r.findings == {Obfuscation.WHITESPACE}


def test_clean_values_report_nothing():
    r = normalize_value("Example Media Holdings Ltd")
    assert not r.changed and not r.findings


def test_scan_text_detects_without_altering():
    findings = scan_text("Cont\u200bact us at \u202eevil\u202c")
    assert Obfuscation.ZERO_WIDTH in findings
    assert Obfuscation.BIDI_CONTROL in findings


def test_bidi_override_is_caught():
    """RTL overrides can make a displayed name differ from its bytes."""
    r = normalize_value("Acme\u202e dtL")
    assert Obfuscation.BIDI_CONTROL in r.findings
    assert r.deliberate


# ---- case-level tracking --------------------------------------------------- #

def test_log_separates_deliberate_from_incidental():
    log = ObfuscationLog()
    log.record("sellers.json", "org_name", normalize_value("Ex\u0430mple Ltd"))
    log.record("ads.txt", "domain", normalize_value("  a.example  "))
    log.record("gleif", "org_name", normalize_value("Clean Ltd"))
    assert len(log.entries) == 2          # clean value not recorded
    assert len(log.deliberate) == 1
    assert log.counts()["homoglyph"] == 1


def test_log_render_surfaces_deliberate_findings():
    log = ObfuscationLog()
    log.record("sellers.json", "org_name", normalize_value("Ex\u0430mple Ltd"))
    out = log.render()
    assert "deliberate" in out.lower()
    assert "homoglyph" in out


def test_empty_log_renders_cleanly():
    assert "No obfuscation" in ObfuscationLog().render()


def test_canonical_is_stable_under_repetition():
    v = "Ex\u0430mple\u200b  Ltd"
    assert canonical(canonical(v)) == canonical(v)


# ---- tracked end to end ---------------------------------------------------- #

def test_identifier_retains_its_observed_form():
    i = Identifier(IdKind.ORG_NAME, "Ex\u0430mple\u200b Media Ltd")
    assert i.value == "Example Media Ltd"
    assert i.observed_as == "Ex\u0430mple\u200b Media Ltd"
    assert i.was_obfuscated


def test_observed_form_does_not_affect_keying_or_equality():
    """It must be reportable without becoming part of the key."""
    a = Identifier(IdKind.ORG_NAME, "Ex\u0430mple Media Ltd")
    b = Identifier(IdKind.ORG_NAME, "Example Media Ltd")
    assert a == b and hash(a) == hash(b) and a.key == b.key


def test_report_surfaces_deliberate_obfuscation():
    import tempfile
    from pathlib import Path

    from attribution_graph import (
        AttributionGraph,
        CaseScope,
        Claim,
        EntityType,
        report,
        resolve,
    )

    d = Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {d / 'a.jsonl'}\n")
    scope = CaseScope.load(str(d / "c.yaml"))

    g = AttributionGraph(case_ref="T")
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=Predicate.LEGAL_NAME,
        object=Identifier(IdKind.ORG_NAME, "Ex\u0430mple\u200b Media Ltd"),
        collector="sellers_json", source_url="https://x",
        reliability=Reliability.STRONG, correlation_group="g1"))

    out = report(g, resolve(g, {EntityType.COMPANY}), scope)
    assert "## Obfuscation" in out
    assert "homoglyph" in out and "zero_width" in out
    assert "evidence in its own right" in out


def test_clean_graph_omits_the_obfuscation_section():
    import tempfile
    from pathlib import Path

    from attribution_graph import (
        AttributionGraph,
        CaseScope,
        Claim,
        EntityType,
        report,
        resolve,
    )

    d = Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {d / 'a.jsonl'}\n")
    scope = CaseScope.load(str(d / "c.yaml"))

    g = AttributionGraph(case_ref="T")
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=Predicate.LEGAL_NAME,
        object=Identifier(IdKind.ORG_NAME, "Clean Ltd"),
        collector="x", source_url="https://x",
        reliability=Reliability.STRONG, correlation_group="g"))

    assert "## Obfuscation" not in report(g, resolve(g, {EntityType.COMPANY}), scope)
