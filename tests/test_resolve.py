

# ---- RB-02: inferential merges must actually happen ------------------------ #

def test_two_independent_strong_groups_merge():
    """The positive regression the audit asked for, corrected twice.

    Two separate defects had to be fixed before this could pass:

    1. `MERGE_LLR_THRESHOLD` was 13.71 nats compared against `a.probability`,
       which cannot exceed 1.0, so every inferential merge was disabled. The
       constant also double-counted the prior: `Assessment.log_odds` already
       includes it, so a posterior threshold is log(0.9/0.1) = 2.197.
    2. More seriously, the resolver only scored directly-claimed
       subject-object edges. Two domains sharing an identifier were never
       assessed as a pair at all, so the two-group evidence that existed was
       never pooled and no threshold could have helped.

    This constructs the toolkit's canonical case: two domains sharing two
    independent identifiers.
    """
    from attribution_graph import (
        AttributionGraph,
        Claim,
        EntityType,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
        resolve,
    )

    g = AttributionGraph(case_ref="MERGE")
    for dom in ("a.example", "b.example"):
        d = Identifier(IdKind.DOMAIN, dom)
        g.add_claim(Claim(
            subject=d, predicate=Predicate.SHARES_ANALYTICS_ID,
            object=Identifier(IdKind.ANALYTICS_ID, "ga4:G-SHARED1"),
            collector="analytics_ids", source_url=f"https://{dom}/",
            reliability=Reliability.AUTHORITATIVE,
            correlation_group=f"analytics|{dom}"))
        g.add_claim(Claim(
            subject=d, predicate=Predicate.SELLER_OF,
            object=Identifier(IdKind.SELLER_ID, "pubmatic.com/156423"),
            collector="ads_txt_owner", source_url=f"https://{dom}/ads.txt",
            reliability=Reliability.AUTHORITATIVE,
            correlation_group=f"ads_txt|{dom}"))

    r = resolve(g, {EntityType.COMPANY})
    pair = next((a for p_, a in r.assessments.items()
                 if p_[0].startswith("domain") and p_[1].startswith("domain")), None)
    assert pair is not None, "two domains sharing identifiers must be scored as a pair"
    assert pair.independent_groups >= 2
    assert not pair.definitional, "this must exercise the inferential path"

    clusters = [sorted(i.value for i in e.identifiers)
                for e in g.entities.values() if len(e.identifiers) > 1]
    assert ["a.example", "b.example"] in clusters


def test_one_shared_identifier_does_not_merge():
    """The corroboration rule must survive the co-reference change."""
    from attribution_graph import (
        AttributionGraph,
        Claim,
        EntityType,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
        resolve,
    )

    g = AttributionGraph(case_ref="NOMERGE")
    for dom in ("a.example", "b.example"):
        g.add_claim(Claim(
            subject=Identifier(IdKind.DOMAIN, dom),
            predicate=Predicate.SHARES_ANALYTICS_ID,
            object=Identifier(IdKind.ANALYTICS_ID, "ga4:G-ONLY"),
            collector="analytics_ids", source_url=f"https://{dom}/",
            reliability=Reliability.AUTHORITATIVE,
            correlation_group=f"analytics|{dom}"))
    resolve(g, {EntityType.COMPANY})
    assert not [e for e in g.entities.values() if len(e.identifiers) > 1]



def test_merge_threshold_is_expressed_in_the_quantity_it_compares():
    """log_odds already includes the prior, so the threshold is a posterior
    log-odds. Naming a posterior an evidence increment is what produced the
    unit mismatch."""
    import math

    from attribution_graph.resolve import MERGE_LOG_ODDS_THRESHOLD

    assert math.isclose(MERGE_LOG_ODDS_THRESHOLD, math.log(0.9 / 0.1), rel_tol=1e-3)


# ---- RB-03: constraints must survive transitive clustering ----------------- #

def _org(name):
    from attribution_graph import Identifier, IdKind
    return Identifier(IdKind.ORG_NAME, name)


def _same_as(a, b, group):
    from attribution_graph import Claim, Predicate, Reliability
    return Claim(subject=a, predicate=Predicate.SAME_AS, object=b,
                 collector="gleif", source_url="https://api.gleif.org/",
                 reliability=Reliability.AUTHORITATIVE, correlation_group=group)


def test_transitive_must_not_link_is_enforced():
    """A~B and B~C both merge-worthy, A!~C explicit: A and C must never share
    a cluster.

    Checking only the candidate pair let the contradiction be violated by
    transitive union. The check now compares whole clusters before joining, so
    the second merge is rejected rather than the constraint being silently
    broken.
    """
    from attribution_graph import (
        AttributionGraph,
        Claim,
        EntityType,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
        resolve,
    )

    g = AttributionGraph(case_ref="CONSTRAINT")

    def link(dom, value, kind, pred, group):
        g.add_claim(Claim(
            subject=Identifier(IdKind.DOMAIN, dom), predicate=pred,
            object=Identifier(kind, value), collector="c",
            source_url=f"https://{dom}/", reliability=Reliability.AUTHORITATIVE,
            correlation_group=f"{group}|{dom}"))

    for dom in ("a.example", "b.example"):
        link(dom, "ga4:G-AB", IdKind.ANALYTICS_ID,
             Predicate.SHARES_ANALYTICS_ID, "analytics")
        link(dom, "pubmatic.com/AB", IdKind.SELLER_ID,
             Predicate.SELLER_OF, "ads_txt")
    for dom in ("b.example", "c.example"):
        link(dom, "ga4:G-BC", IdKind.ANALYTICS_ID,
             Predicate.SHARES_ANALYTICS_ID, "analytics")
        link(dom, "pubmatic.com/BC", IdKind.SELLER_ID,
             Predicate.SELLER_OF, "ads_txt")

    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=Predicate.CONTRADICTS,
        object=Identifier(IdKind.DOMAIN, "c.example"),
        collector="negative", source_url="https://x",
        reliability=Reliability.AUTHORITATIVE, correlation_group="negative|ac"))

    r = resolve(g, {EntityType.COMPANY})
    clusters = [sorted(i.value for i in e.identifiers)
                for e in g.entities.values() if len(e.identifiers) > 1]
    assert not any("a.example" in c and "c.example" in c for c in clusters)
    assert r.rejected, "the union that would violate the constraint is recorded"



def test_constraint_holds_regardless_of_union_order():
    """Ordering must not decide whether a constraint is honoured."""
    from attribution_graph import (
        AttributionGraph,
        Claim,
        EntityType,
        Predicate,
        Reliability,
        resolve,
    )

    for first, second in (("g-ab", "g-bc"), ("g-bc", "g-ab")):
        A, B, C = _org("A Ltd"), _org("B Ltd"), _org("C Ltd")
        g = AttributionGraph(case_ref="ORD")
        g.add_claim(_same_as(A, B, first))
        g.add_claim(_same_as(B, C, second))
        g.add_claim(Claim(subject=A, predicate=Predicate.CONTRADICTS, object=C,
                          collector="analyst", source_url="https://x",
                          reliability=Reliability.AUTHORITATIVE,
                          correlation_group="neg"))
        resolve(g, {EntityType.COMPANY})
        for e in g.entities.values():
            values = {i.value for i in e.identifiers}
            assert not ({"A Ltd", "C Ltd"} <= values)


def test_transitive_person_company_conflict_is_blocked():
    """A person and a company are not the same entity, and that must hold over
    whole clusters.

    The check compared the candidate pair's two endpoints only. With
    A(person)~B and B~C(company) both merge-worthy, the endpoints were
    compatible at each step and the result was not: one cluster containing
    both. The same shape as the must-not-link violation, and the same fix.
    """
    from attribution_graph import (
        AttributionGraph,
        Claim,
        EntityType,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
        resolve,
    )

    g = AttributionGraph(case_ref="TYPES")

    def same_as(sk, sv, ok, ov, group):
        g.add_claim(Claim(
            subject=Identifier(sk, sv), predicate=Predicate.SAME_AS,
            object=Identifier(ok, ov), collector="gleif",
            source_url="https://api.gleif.org/",
            reliability=Reliability.AUTHORITATIVE, correlation_group=group))

    same_as(IdKind.PERSON_NAME, "Jane Doe", IdKind.HANDLE, "jdoe", "g1")
    same_as(IdKind.HANDLE, "jdoe", IdKind.ORG_NAME, "Acme Ltd", "g2")

    result = resolve(g, {EntityType.COMPANY, EntityType.PERSON})
    clusters = [sorted(i.value for i in e.identifiers)
                for e in g.entities.values() if len(e.identifiers) > 1]

    assert not any("Jane Doe" in c and "Acme Ltd" in c for c in clusters)
    assert any("type conflict" in r[2] for r in result.rejected)
    # the compatible half still merges
    assert ["Jane Doe", "jdoe"] in clusters
