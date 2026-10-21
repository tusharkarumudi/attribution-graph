

# ---- an entity reached only through a reseller is not a party --------------- #

def test_reseller_only_entities_are_marked_in_the_report():
    """`openx.com,900000001,RESELLER` sits under Northwind Weather's `## Partner Network ##`
    block: OpenX reselling Partner Network's inventory. Its legal entity was listed
    flat under "Resolved entities" beside Northwind Weather, implying a connection the
    ads.txt explicitly denies. ads.txt records DIRECT vs RESELLER and nothing
    downstream used it."""
    from attribution_graph import (
        AttributionGraph,
        Claim,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
    )
    from attribution_graph.export import _account_relationships

    g = AttributionGraph(case_ref="T")
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "publisher-site.example"),
        predicate=Predicate.SELLER_OF,
        object=Identifier(IdKind.SELLER_ID, "openx.com/900000001"),
        collector="ads_txt", source_url="https://publisher-site.example/ads.txt",
        reliability=Reliability.AUTHORITATIVE,
        correlation_group="ads_txt|publisher-site.example",
        raw={"relationship": "RESELLER"}))
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "publisher-site.example"),
        predicate=Predicate.SELLER_OF,
        object=Identifier(IdKind.SELLER_ID, "themediagrid.com/4ouv6m"),
        collector="ads_txt", source_url="https://publisher-site.example/ads.txt",
        reliability=Reliability.AUTHORITATIVE,
        correlation_group="ads_txt|publisher-site.example",
        raw={"relationship": "DIRECT"}))

    rels = _account_relationships(g)
    assert rels["seller_id:openx.com/900000001"] == {"RESELLER"}
    assert rels["seller_id:themediagrid.com/4ouv6m"] == {"DIRECT"}
