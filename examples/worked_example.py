"""End-to-end worked example with synthetic identifiers.

Demonstrates the two behaviours that distinguish this from graph traversal:
a strong multi-source link merges, and a strong single-source link does not.

    python examples/worked_example.py
"""

from pathlib import Path

from attribution_graph import (
    AttributionGraph,
    CaseScope,
    Claim,
    EntityType,
    Identifier,
    IdKind,
    Predicate,
    Reliability,
    report,
    resolve,
)


def claim(subject, predicate, obj, collector, url, rel=Reliability.STRONG, group="g"):
    return Claim(subject=subject, predicate=predicate, object=obj,
                 collector=collector, source_url=url,
                 reliability=rel, correlation_group=group)


def main() -> None:
    Path("case.demo.yaml").write_text(
        "case_ref: DEMO-001\n"
        "authorization: 'synthetic example, no real subjects'\n"
        "seeds: [domain:scraper-site.example]\n"
        "entity_types_allowed: [Company]\n"
        "audit_path: /tmp/demo-audit.jsonl\n"
    )
    scope = CaseScope.load("case.demo.yaml")
    g = AttributionGraph(case_ref=scope.case_ref)

    domain = Identifier(IdKind.DOMAIN, "scraper-site.example")
    seller = Identifier(IdKind.SELLER_ID, "pubmatic.example/156423")
    org = Identifier(IdKind.ORG_NAME, "Example Media Holdings Ltd")
    lei = Identifier(IdKind.LEI, "5493001KJTIIGC8Y1R12")
    company_no = Identifier(IdKind.COMPANY_NUMBER, "gb/09876543")
    director = Identifier(IdKind.PERSON_NAME, "Jane Q Operator")
    cdn = Identifier(IdKind.DOMAIN, "cdn-provider.example")

    # Two independent groups tying the domain to a monetization account.
    g.add_claim(claim(domain, Predicate.SELLER_OF, seller, "ads_txt_owner",
                      "https://scraper-site.example/ads.txt", group="adstxt"))
    g.add_claim(claim(seller, Predicate.OPERATES, domain, "sellers_json",
                      "https://pubmatic.example/sellers.json", group="sellers"))

    # Single-source trading name: strong, but one group.
    g.add_claim(claim(seller, Predicate.LEGAL_NAME, org, "sellers_json",
                      "https://pubmatic.example/sellers.json", group="sellers"))

    # Registry assertions: definitional, exempt from the corroboration rule.
    g.add_claim(claim(org, Predicate.SAME_AS, lei, "gleif",
                      "https://api.gleif.org/...", Reliability.AUTHORITATIVE, "gleif"))
    g.add_claim(claim(lei, Predicate.SAME_AS, company_no, "gleif",
                      "https://api.gleif.org/...", Reliability.AUTHORITATIVE, "gleif"))

    # A director is NOT the company. Must-not-link blocks this merge.
    g.add_claim(claim(director, Predicate.OFFICER_OF, company_no, "companies_house_uk",
                      "https://api.ch/...", Reliability.AUTHORITATIVE, "ch"))

    # Shared CDN: true, and worth nothing.
    g.add_claim(claim(domain, Predicate.CO_HOSTED, cdn, "internetdb",
                      "https://internetdb.shodan.io/...", group="idb"))

    result = resolve(g, {EntityType.COMPANY, EntityType.PERSONA})
    print(report(g, result, scope))


if __name__ == "__main__":
    main()
