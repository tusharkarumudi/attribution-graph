"""Transliteration: the match must always state its transform chain."""

import pytest

from attribution_graph import name_match, rule_catalog, variants


@pytest.mark.parametrize("a,b,rule", [
    ("Lakshmi", "Laxmi", "indic_ksh"),
    ("Vemuganti", "Vemugunti", "indic_vowel_quality"),
    ("Мосэнерго", "Mosenergo", "cyrillic_bgn_pcgn"),
    ("Müller", "Mueller", "german_umlaut_expand"),
    ("Zhang Wei", "Chang Wei", "pinyin_wade_giles"),

    ("Ravi Kumar", "Kumar Ravi", "name_order_swap"),
    ("Tushar Karumudi", "T Karumudi", "initial_expand"),
])
def test_match_reports_the_exact_rule_used(a, b, rule):
    m = name_match(a, b)
    assert m.matched
    assert rule in (m.chain_a + m.chain_b), m.describe()
    assert rule in m.describe()


@pytest.mark.parametrize("a,b", [
    ("Kraken", "Phoenix"), ("Smith", "Jones"), ("Acme Ltd", "Zeta Corp"),
])
def test_unrelated_names_do_not_match(a, b):
    assert not name_match(a, b).matched


def test_overlapping_rules_resolve_to_some_valid_cjk_path():
    """b/p is encoded by both Wade-Giles and McCune-Reischauer; either is a
    correct explanation, so assert the family rather than one rule."""
    m = name_match("Busan", "Pusan")
    assert m.matched
    assert any(r in ("korean_rr_mr", "pinyin_wade_giles")
               for r in m.chain_a + m.chain_b), m.describe()


def test_degenerate_collapse_is_rejected():
    """Two rules that each shorten a name must not meet at a stub."""
    m = name_match("Alexander Petrov", "Alicia Popescu")
    assert not m.matched


def test_short_names_are_refused():
    m = name_match("Li", "Lee")
    assert not m.matched
    assert "too short" in m.note


def test_exact_match_reports_zero_transforms():
    m = name_match("Acme Holdings", "acme holdings")
    assert m.matched and m.total_depth == 0
    assert m.describe() == "exact match as written"


def test_provenance_is_serializable_for_reports():
    d = name_match("Lakshmi", "Laxmi").to_dict()
    assert d["matched"] and d["transform_chain_b"] == ["indic_ksh"]
    assert "provenance" in d


def test_variants_carry_their_chains():
    for v in variants("Lakshmi"):
        assert isinstance(v.chain, tuple)
        assert len(v.chain) == v.depth


def test_variant_count_is_bounded():
    from attribution_graph.translit import MAX_VARIANTS
    assert len(variants("Muhammad Abdullah Rahman")) <= MAX_VARIANTS


def test_rule_catalog_documents_every_rule():
    cat = rule_catalog()
    assert len(cat) >= 20
    assert all(r["name"] and r["script"] and r["description"] for r in cat)
