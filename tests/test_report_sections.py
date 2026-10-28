"""The executive summary, the network map and the indicator table.

Added because the report answered the analyst's questions and not the ones
asked by whoever has to decide what happens next, and because the pivot data
that makes the method worth anything was only ever a table of pairs.
"""

import pathlib
import tempfile

import pytest

from attribution_graph import (
    AttributionGraph,
    CaseScope,
    Claim,
    EntityType,
    Identifier,
    IdKind,
    Predicate,
    Reliability,
    resolve,
)
from attribution_graph.export import (
    executive_summary,
    ioc_table,
    network_map,
    report,
    to_html,
    write_all,
)

SELLER = "adrelay.example/7kQmZ2bXr4TnWp8Ls"


def _case(tmp, minimize="false"):
    (tmp / "c.yaml").write_text(
        f"case_ref: REP\nauthorization: ticket 4567\n"
        f"seeds: [domain:viewer-site.example]\nminimize: {minimize}\n"
        f"audit_path: {tmp}/a.jsonl\n")
    return CaseScope.load(str(tmp / "c.yaml"))


def _claim(g, s, sk, o, ok, coll, grp, pred=Predicate.SHARES_ANALYTICS_ID,
           weight=1.0):
    c = Claim(subject=Identifier(sk, s), predicate=pred,
              object=Identifier(ok, o), collector=coll,
              source_url=f"https://{coll}.example/x",
              reliability=Reliability.AUTHORITATIVE, correlation_group=grp)
    c.weight = weight
    g.add_claim(c)


@pytest.fixture
def world():
    tmp = pathlib.Path(tempfile.mkdtemp())
    scope = _case(tmp)
    g = AttributionGraph(case_ref="REP")
    _claim(g, "viewer-site.example", IdKind.DOMAIN, SELLER, IdKind.SELLER_ID,
           "ads_txt", "ads_txt|v")
    _claim(g, "viewer-site.example", IdKind.DOMAIN, SELLER, IdKind.SELLER_ID,
           "wayback_ads", "wayback|v")
    _claim(g, SELLER, IdKind.SELLER_ID, "Example Medya Ltd.", IdKind.ORG_NAME,
           "sellers_json", "sellers|a", Predicate.SAME_AS)
    _claim(g, SELLER, IdKind.SELLER_ID, "Example Medya Ltd.", IdKind.ORG_NAME,
           "gleif", "gleif|x", Predicate.SAME_AS)
    _claim(g, "viewer-site.example", IdKind.DOMAIN, "jane@operator.example",
           IdKind.EMAIL, "imprint", "imprint|v", Predicate.PROFILE_BINDING)
    _claim(g, "viewer-site.example", IdKind.DOMAIN, "ga4:G-PLANTED01",
           IdKind.ANALYTICS_ID, "analytics_ids", "analytics|v", weight=0.0)
    res = resolve(g, {EntityType.COMPANY, EntityType.PERSONA})
    return tmp, scope, g, res


# ---- executive summary ----------------------------------------------------- #

def test_the_summary_leads_with_the_subject_and_the_finding(world):
    _tmp, scope, g, res = world
    text = "\n".join(executive_summary(g, res, scope, {"result_complete": True}))
    assert "**Subject.**" in text
    assert "viewer-site.example" in text
    assert "**What the evidence supports.**" in text
    assert "**How strong.**" in text
    assert "not calibrated" in text, "the governing caveat must be in the summary"


def test_the_summary_carries_the_conditions_that_change_the_reading(world):
    _tmp, scope, g, res = world
    text = "\n".join(executive_summary(g, res, scope, {
        "result_complete": False,
        "ads_txt_consistency": "DIFFERENT_ACCOUNTS",
        "hosts_unreachable": 3,
        "adversarial_demoted": 2,
        "gravatar_links": 1,
    }))
    assert "INCOMPLETE" in text
    assert "not the same for every retrieval" in text
    assert "could not be reached" in text
    assert "demoted" in text
    assert "displays the address's avatar" in text


def test_an_invalid_result_says_do_not_use_it(world):
    _tmp, scope, g, res = world
    text = "\n".join(executive_summary(g, res, scope, {"result_valid": False}))
    assert "INVALID" in text
    assert "should not be used" in text


def test_a_run_that_collected_nothing_says_so_without_apologising(world):
    _tmp, scope, _g, _res = world
    empty = AttributionGraph(case_ref="REP")
    text = "\n".join(executive_summary(empty, resolve(empty, {EntityType.COMPANY}),
                                       scope, {}))
    assert "Nothing was collected at all" in text
    assert "rather than a failure" in text
    assert "blocked" in text, "an empty run is usually blocked retrievals"


def test_claims_below_the_band_are_not_described_as_no_declarations(world):
    """The summary used to say "a site with no advertising declarations
    publishes no payee" whenever nothing reached a band -- including when the
    declarations had been read and the links were merely single-source. A
    reader seeing a payee on the network map directly above that sentence has
    every reason to distrust the rest of the report."""
    _tmp, scope, _g, _res = world
    g = AttributionGraph(case_ref="REP")
    _claim(g, "viewer-site.example", IdKind.DOMAIN, SELLER, IdKind.SELLER_ID,
           "ads_txt", "ads_txt|v")          # one source only
    res = resolve(g, {EntityType.COMPANY})
    text = "\n".join(executive_summary(g, res, scope, {}))
    assert "no advertising declarations" not in text
    assert "Declarations were read" in text
    assert "single source" in text
    assert "does not mean nothing is there" in text


# ---- network map ----------------------------------------------------------- #

def test_the_map_is_mermaid_and_only_shows_supported_links(world):
    _tmp, _scope, g, res = world
    lines = network_map(g, res)
    body = "\n".join(lines)
    assert "```mermaid" in body and "flowchart LR" in body
    assert "UNSUPPORTED" not in body, "an unsupported pair is not a link"
    assert "classDef" in body, "shapes and classes carry the kind"


def test_map_labels_survive_the_fence(world):
    """`_md` escapes markdown, which inside a fence renders as a literal
    backslash: `org\\_name`. And the kind prefix is dropped because the node's
    shape already carries it."""
    _tmp, _scope, g, res = world
    body = "\n".join(network_map(g, res))
    assert "\\_" not in body
    assert '"seller_id:' not in body and '"org_name:' not in body


def test_the_map_is_bounded(world):
    """A map nobody can read is not evidence of anything."""
    _tmp, _scope, g, res = world
    lines = network_map(g, res, limit=1)
    edges = [ln for ln in lines if "==>" in ln or "-->" in ln]
    assert len(edges) == 1
    assert any("further supported link" in ln for ln in lines)


def test_the_map_says_a_band_is_not_a_probability(world):
    _tmp, _scope, g, res = world
    body = "\n".join(network_map(g, res))
    assert "not a probability" in body, \
        "a diagram travels further than the paragraph under it"


def test_no_map_when_there_is_nothing_to_draw():
    g = AttributionGraph(case_ref="REP")
    assert network_map(g, resolve(g, {EntityType.COMPANY})) == []


# ---- indicators ------------------------------------------------------------ #

def test_every_indicator_row_carries_provenance(world):
    _tmp, _scope, g, _res = world
    lines = ioc_table(g)
    body = "\n".join(lines)
    assert "| Type | Value | First retrieved | Claims | Collector(s) | Note |" in body
    assert "adtech-seller-id" in body and "email-addr" in body
    assert "ads_txt" in body, "a row without its collector is a string to take on faith"


def test_a_demoted_indicator_is_kept_and_marked(world):
    """An identifier placed to manufacture a link is a fact about whoever
    placed it, so it stays in the table."""
    _tmp, _scope, g, _res = world
    body = "\n".join(ioc_table(g))
    assert "g-planted01" in body.lower()
    assert "DEMOTED" in body


def test_self_published_rows_are_marked(world):
    _tmp, _scope, g, _res = world
    body = "\n".join(ioc_table(g))
    assert "self-published" in body


def test_indicators_are_bounded(world):
    _tmp, _scope, g, _res = world
    import attribution_graph.export as ex

    original = ex.IOC_MAX_ROWS
    ex.IOC_MAX_ROWS = 1
    try:
        body = "\n".join(ioc_table(g))
    finally:
        ex.IOC_MAX_ROWS = original
    assert "further observable" in body


# ---- both documents -------------------------------------------------------- #

def test_the_report_carries_all_three_sections(world):
    tmp, scope, g, res = world
    text = report(g, res, scope, status={"result_complete": True})
    for section in ("## Executive summary", "## Network map", "```mermaid",
                    "## Indicators"):
        assert section in text, section
    assert text.index("## Executive summary") < text.index("## Resolved entities")


def test_the_html_carries_them_and_still_loads_nothing(world):
    """The HTML report is deliberately JS-free and emailable. A CDN script to
    draw a diagram is not a trade worth making, so the source ships instead."""
    tmp, scope, g, res = world
    html = to_html(g, res, scope, status={"result_complete": True})
    for section in ("Executive summary", "Network map", "Indicators"):
        assert section in html, section
    assert "flowchart LR" in html
    assert "<script" not in html.lower()
    assert "http://" not in html and "https://cdn" not in html


def test_the_diagram_is_written_as_its_own_file(world):
    tmp, scope, g, res = world
    paths = write_all(g, res, scope, tmp / "out")
    names = {p.name for p in paths}
    assert "network_map.mmd" in names
    src = (tmp / "out" / "network_map.mmd").read_text()
    assert src.startswith("flowchart LR")
    assert "```" not in src, "the standalone file must not carry the fence"


def test_minimisation_reaches_the_new_sections(world):
    """These sections name identifiers, so a minimised report must mask them
    like every other section."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    scope = _case(tmp, minimize="true")
    _t, _s, g, res = world
    write_all(g, res, scope, tmp / "out")
    text = (tmp / "out" / "attribution_report.md").read_text()
    assert "viewer-site.example" not in text
    assert "jane@operator.example" not in text
    assert "min:" in text
