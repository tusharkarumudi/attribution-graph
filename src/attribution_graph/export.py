"""Exporters: FollowTheMoney, Neo4j, and an analyst-readable report.

FollowTheMoney is the export target because it is the schema yente and Aleph
already speak -- the same graph can be screened against sanctions data or loaded
into an investigation UI without a translation layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .model import AttributionGraph, EntityType, Identifier, IdKind, Predicate
from .resolve import ResolutionResult
from .scope import CaseScope
from .scoring import Band

FTM_SCHEMA = {
    EntityType.COMPANY: "Company",
    EntityType.PERSON: "Person",
    EntityType.PERSONA: "LegalEntity",
    EntityType.UNKNOWN: "LegalEntity",
}

FTM_PROP = {
    IdKind.ORG_NAME: "name",
    IdKind.PERSON_NAME: "name",
    IdKind.EMAIL: "email",
    IdKind.DOMAIN: "website",
    IdKind.LEI: "leiCode",
    IdKind.COMPANY_NUMBER: "registrationNumber",
    IdKind.POSTAL_ADDRESS: "address",
    IdKind.PHONE: "phone",
}


def _account_relationships(graph) -> dict[str, set[str]]:
    """seller_id key -> the DIRECT/RESELLER labels it was declared under.

    ads.txt records this and nothing downstream used it, so an entity reached
    only through a RESELLER line -- an ad system reselling somebody else's
    inventory -- was listed beside the subject as though it were a party to it.
    """
    out: dict[str, set[str]] = {}
    for c in getattr(graph, "claims", ()) or ():
        rel = str((getattr(c, "raw", None) or {}).get("relationship") or "").upper()
        if rel not in ("DIRECT", "RESELLER"):
            continue
        for side in (getattr(c, "subject", None), getattr(c, "object", None)):
            key = getattr(side, "key", None) or getattr(side, "value", None)
            if key and str(key).startswith("seller_id:"):
                out.setdefault(str(key), set()).add(rel)
    return out


def _mask_attributes(attrs: dict, salt: bytes | None) -> dict:
    """Recursively mask entity attributes.

    `Entity.attributes` carried raw emails, names and addresses straight into
    the minimized export. Masking the keys while leaving the attributes intact
    protects the index and publishes the contents.
    """
    if not salt or not isinstance(attrs, dict):
        return attrs
    import hashlib

    def m(v, depth=0):
        if depth > 6:
            return "min:<depth>"
        if isinstance(v, dict):
            return {k: m(x, depth + 1) for k, x in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [m(x, depth + 1) for x in v]
        if isinstance(v, str) and v:
            return "min:" + hashlib.blake2b(
                salt + v.encode(), digest_size=12).hexdigest()
        return v

    return m(attrs)


def _scrub(text: str, graph: AttributionGraph, salt: bytes | None) -> str:
    """Mask identifier values wherever they appear in a serialised artifact.

    Delegates to `minimise.scrub`, which owns the canonical-form list. This
    function previously kept its own, and it drifted from the audit
    minimiser's: one covered `Jane%20Doe`, the other `Jane+Doe`, neither
    covered both, and both exempted values of three characters or fewer.
    """
    from .minimise import scrub

    if not salt:
        return text

    values: set[str] = set()
    for ident in graph.identifiers.values():
        values.add(ident.value)
        values.add(ident.key)
    return scrub(text, {v for v in values if v}, salt)


def _label(entity, salt: bytes | None) -> str:
    """An entity label, masked when minimising.

    A label is frequently the very identifier the minimisation exists to
    protect -- an org name, a person name, a domain -- so leaving it raw in an
    export defeats the point of hashing the keys beside it.
    """
    label = getattr(entity, "best_label", None) or "unnamed"
    if not salt:
        return label
    import hashlib
    return "min:" + hashlib.blake2b(
        salt + label.encode(), digest_size=12).hexdigest()


def to_ftm(graph: AttributionGraph, salt: bytes | None = None) -> list[dict]:
    out: list[dict] = []
    for e in graph.entities.values():
        props: dict[str, list[str]] = {}
        for ident in e.identifiers:
            prop = FTM_PROP.get(ident.kind)
            if prop:
                props.setdefault(prop, [])
                if ident.value not in props[prop]:
                    props[prop].append(ident.value)
        for k, vals in _mask_attributes(e.attributes, salt).items():
            props.setdefault(k, list(vals))
        out.append({
            "id": f"eae-{e.id}",
            "schema": FTM_SCHEMA[e.type],
            "properties": props,
            "datasets": [graph.case_ref],
        })

    for c in graph.claims:
        if c.predicate is Predicate.OFFICER_OF:
            out.append(_ftm_rel("Directorship", c, "director", "organization"))
        elif c.predicate is Predicate.BENEFICIAL_OWNER_OF or c.predicate is Predicate.PARENT_OF:
            out.append(_ftm_rel("Ownership", c, "owner", "asset"))
    return out


def _ftm_rel(schema: str, claim, a_prop: str, b_prop: str) -> dict:
    return {
        "id": f"eae-rel-{claim.id}",
        "schema": schema,
        "properties": {
            a_prop: [claim.subject.key],
            b_prop: [claim.object_key],
            "sourceUrl": [claim.source_url],
            "startDate": [claim.observed_at.date().isoformat()],
        },
    }


def to_cypher(graph: AttributionGraph, salt: bytes | None = None) -> str:
    lines = ["// Entity Attribution Engine export", f"// case: {graph.case_ref}"]
    lines.append("CREATE CONSTRAINT eae_ident IF NOT EXISTS "
                 "FOR (i:Identifier) REQUIRE i.key IS UNIQUE;")
    for i in graph.identifiers.values():
        lines.append(
            f"MERGE (:Identifier {{key: {json.dumps(i.masked(salt) if salt else i.key)}, "
            f"kind: {json.dumps(i.kind.value)}, value: {json.dumps(i.value)}}});"
        )
    for e in graph.entities.values():
        lines.append(
            f"MERGE (e:Entity {{id: {json.dumps(e.id)}}}) "
            f"SET e.type = {json.dumps(e.type.value)}, "
            f"e.label = {json.dumps(_label(e, salt))}, e.log_odds = {e.log_odds:.4f};"
        )
        for i in e.identifiers:
            lines.append(
                f"MATCH (e:Entity {{id: {json.dumps(e.id)}}}), "
                f"(i:Identifier {{key: {json.dumps(i.masked(salt) if salt else i.key)}}}) "
                f"MERGE (e)-[:RESOLVES_TO]->(i);"
            )
    for c in graph.claims:
        if c.subject.key in graph.identifiers and c.object_key in graph.identifiers:
            lines.append(
                f"MATCH (a:Identifier {{key: {json.dumps(c.subject.key)}}}), "
                f"(b:Identifier {{key: {json.dumps(c.object_key)}}}) "
                f"MERGE (a)-[r:{c.predicate.name}]->(b) "
                f"SET r.collector = {json.dumps(c.collector)}, "
                f"r.source = {json.dumps(c.source_url)}, "
                f"r.weight = {c.weight}, "
                f"r.observed_at = {json.dumps(c.observed_at.isoformat())};"
            )
    return "\n".join(lines)


#: Per-source attribution and terms. Rendered at the end of every report.
SOURCE_TERMS: dict[str, tuple[str, str]] = {
    "gleif": ("GLEIF Global LEI Index", "CC0 / GLEIF terms — free reuse with attribution"),
    "sec_edgar": ("U.S. SEC EDGAR", "US government work, public domain; fair-access UA policy applies"),  # noqa: E501
    "companies_house_uk": ("UK Companies House", "Open Government Licence v3.0 — attribution required"),  # noqa: E501
    "opencorporates": ("OpenCorporates", "Free tier is non-commercial; attribution required. "
                                         "Verify current terms before republishing."),
    "opensanctions_yente": ("OpenSanctions", "Software MIT; data licence required for commercial use"),  # noqa: E501
    "crtsh": ("crt.sh / Certificate Transparency", "Public CT logs"),
    "rdap": ("RDAP (IANA bootstrap)", "Registry data; redistribution limited by registry policy"),
    "sellers_json": ("IAB Tech Lab sellers.json", "Publisher-declared, published for public inspection"),  # noqa: E501
    "ads_txt_owner": ("IAB Tech Lab ads.txt", "Publisher-declared, published for public inspection"),  # noqa: E501
    "wayback": ("Internet Archive Wayback Machine", "Archived third-party content; original rights persist"),  # noqa: E501
    "nyc_acris": ("NYC ACRIS via NYC Open Data", "NYC Open Data terms of use"),
    "uk_overseas_property": ("HM Land Registry", "Open Government Licence; HMLR attribution required"),  # noqa: E501
    "uspto_trademark": ("USPTO", "US government work, public domain"),
}

CONFIDENCE_DISCLAIMER = (
    "**On the confidence figures.** Where probabilities or confidence bands appear "
    "above, they are the output of a documented statistical model applied to the "
    "cited observations. They are analytical judgment, not measured fact. The model "
    "has not been calibrated against a labelled ground-truth corpus, so the figures "
    "should be read as ordering the strength of findings relative to one another "
    "rather than as literal probabilities. The observations themselves — what each "
    "cited source contained at the stated time — are matters of record; the "
    "conclusions drawn from them are not."
)


def _obfuscation_section(graph: AttributionGraph) -> str:
    """Identifiers that required normalization, and what kind.

    Deliberate techniques -- homoglyphs, zero-width characters, bidi overrides --
    are reported separately because they do not occur by accident. An entity
    whose identifiers needed them is one someone worked to keep uncorrelated,
    and that is a finding rather than a data-quality note.
    """
    from .obfuscation import DELIBERATE, ObfuscationLog, normalize_value

    log = ObfuscationLog()
    for c in graph.claims:
        for role, ident in (("subject", c.subject), ("object", c.object)):
            if not isinstance(ident, Identifier):
                continue
            r = normalize_value(ident.observed_as, case_fold=False,
                                collapse_space=True)
            if r.findings:
                log.record(f"{c.collector} ({role})", ident.kind.value, r)

    if not log.entries:
        return ""
    out = [log.render()]
    if log.deliberate:
        out += ["", "Deliberate obfuscation is evidence in its own right: "
                    "these techniques require effort and are used to avoid "
                    "correlation, not by mistake."]
    _ = DELIBERATE
    return "\n".join(out)


#: Characters that let a subject-controlled label restructure a Markdown table
#: or inject a link. The HTML exporter escapes; the Markdown one did not, so an
#: entity name is presentation-controlled output in a document an analyst
#: forwards.
_MD_UNSAFE = str.maketrans({
    "|": "\\|", "`": "\\`", "*": "\\*", "_": "\\_", "[": "\\[", "]": "\\]",
    "<": "&lt;", ">": "&gt;", "\n": " ", "\r": " ",
})


def _md(value: object, limit: int = 200) -> str:
    """Render a value safely inside a Markdown table cell."""
    text = str(value).translate(_MD_UNSAFE).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def source_attribution(graph: AttributionGraph) -> str:
    """Attribution and terms for every source actually used."""
    used = sorted({c.collector.split(":")[0] for c in graph.claims})
    L = ["| Source | Terms |", "|---|---|"]
    unknown = []
    for c in used:
        entry = SOURCE_TERMS.get(c)
        if entry:
            L.append(f"| {entry[0]} | {entry[1]} |")
        else:
            unknown.append(c)
    if unknown:
        L.append(f"| {', '.join(unknown)} | Terms not declared — verify before republishing |")
    L += ["", "_Data from the sources above is reproduced here for investigative "
              "purposes. Redistribution of this report may be subject to the terms "
              "listed. Confirm current terms with each provider before publishing._"]
    return "\n".join(L)


def _status_banner(status: dict | None) -> list[str]:
    """A prominent banner when a run is invalid or incomplete.

    Reports were written unconditionally, so a run with `result_valid=false`
    still produced an ordinary "Attribution assessment". A reader holding only
    the report file has no way to know, and exit codes do not travel with a
    document once it has been shared.
    """
    if not status:
        return []
    if status.get("result_valid") is False:
        return [
            "> ## :: INVALID RESULT — DO NOT RELY ON THIS ASSESSMENT ::",
            ">",
            "> Internal defects occurred during collection: collectors failed "
            "for reasons that are bugs in the toolkit, not source "
            "unavailability. The evidence set is incomplete in an unknown way, "
            "so the conclusions below are not supported.",
            "",
        ]
    if status.get("result_complete") is False:
        reason = ("the request budget" if status.get("budget_exhausted")
                  else "the wall-clock budget" if status.get("runtime_exhausted")
                  else "the node budget" if status.get("nodes_truncated")
                  else "a configured limit")
        return [
            "> ## :: INCOMPLETE RESULT ::",
            ">",
            f"> {reason} stopped the search before it finished. Everything "
            "collected is sound; there is less of it than an unbounded run "
            "would have produced, and an absence below may mean 'not reached' "
            "rather than 'not present'.",
            "",
        ]
    if status.get("collection_blocked"):
        return [
            "> ## :: COLLECTION LIMITED ::",
            ">",
            f"> {status['collection_blocked']} retrieval(s) were blocked by "
            "policy or URL safety. An absence below may mean 'could not be "
            "checked' rather than 'checked and not found'.",
            "",
        ]
    return []


def report(
    graph: AttributionGraph,
    result: ResolutionResult,
    scope: CaseScope,
    *,
    show_scores: bool = True,
    trail=None,
    salt: bytes | None = None,
    status: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    L = [
        *_status_banner(status),
        f"# Attribution assessment - {graph.case_ref}",
        "",
        f"- Authorization: {scope.authorization}",
        f"- Generated: {now}",
        f"- Retention expiry: {scope.expiry().date().isoformat()}",
        f"- Identifiers: {len(graph.identifiers)} | Claims: {len(graph.claims)} "
        f"| Entities: {len(graph.entities)}",
        "",
        "Bands describe evidence strength, not measured probability: the model "
        "has not been calibrated. Findings below MODERATE_EVIDENCE are leads, "
        "not conclusions, and single-source links are capped at WEAK by design.",
        "",
        "## Resolved entities",
        "",
    ]

    rels = _account_relationships(graph)
    for e in sorted(graph.entities.values(), key=lambda x: -x.log_odds):
        if len(e.identifiers) < 2:
            continue
        L.append(f"### {_md(e.best_label)}  ({e.type.value})")
        L.append("")
        labels = {r for i in e.identifiers for r in rels.get(str(i.key), set())}
        if labels and "DIRECT" not in labels:
            L.append("> Reached only through RESELLER declarations: an ad system "
                     "reselling\n> inventory sold by someone else. Not a declared "
                     "party to the subject.")
            L.append("")
        for i in sorted(e.identifiers, key=lambda x: x.key):
            tag = "/".join(sorted(rels.get(str(i.key), ()))) if rels.get(str(i.key)) else ""
            L.append(f"- `{i.key}`" + (f"  — {tag}" if tag else ""))
        if _mask_attributes(e.attributes, salt):
            L.append("")
            for k, v in _mask_attributes(e.attributes, salt).items():
                L.append(f"- **{k}**: {', '.join(map(str, v))}")
        L.append("")

    ranked = sorted(result.assessments.items(), key=lambda kv: -kv[1].log_odds)
    if show_scores:
        L += ["## Pairwise assessments", "",
              "| A | B | evidence score (nats) | Band | Assessment | Indep. groups |",
              "|---|---|---|---|---|---|"]
        for (a, b), asmt in ranked[:50]:
            if asmt.band is Band.UNSUPPORTED:
                continue
            L.append(
                f"| `{_md(a)}` | `{_md(b)}` | {asmt.log_odds:.3f} | {asmt.band.value} | "
                f"{asmt.estimative} | {asmt.independent_groups} |"
            )
    else:
        L += ["## Links found", "",
              "| A | B | Supporting evidence groups |", "|---|---|---|"]
        for (a, b), asmt in ranked[:50]:
            if asmt.band is Band.UNSUPPORTED:
                continue
            L.append(f"| `{_md(a)}` | `{_md(b)}` | {asmt.independent_groups} |")

    if result.rejected:
        L += ["", "## Blocked merges", ""]
        for a, b, why in result.rejected[:50]:
            L.append(f"- `{a}` <-> `{b}` - {why}")

    L += ["", "## Provenance", "",
          "Every claim carries collector, source URL, retrieval time and "
          "correlation group in `investigation_graph.json`. Preserved response "
          "bodies are the evidentiary artifact; the graph is derived from them."]

    if trail is not None:
        L += ["", "See `verification_trail.md` for the ordered, timestamped "
                  "sequence of steps and the numbered source list."]

    obf = _obfuscation_section(graph)
    if obf:
        L += ["", "## Obfuscation", "", obf]

    L += ["", "## Sources and terms", "", source_attribution(graph)]

    if show_scores:
        L += ["", "---", "", CONFIDENCE_DISCLAIMER]

    return "\n".join(L)


def write_all(
    graph: AttributionGraph, result: ResolutionResult, scope: CaseScope, outdir: Path,
    *, show_scores: bool = True, trail=None, status: dict | None = None,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []

    # Under `minimize: true` the CANONICAL exports are the minimized ones.
    #
    # This wrote a raw investigation_graph.json unconditionally and added a
    # minimized copy alongside, so the public promise -- salted-hash identifiers
    # at rest -- was defeated by the file sitting next to the one that kept it.
    # These artifacts are meant to be shared; a raw export beside a minimized
    # one is a raw export.
    salt = scope.store_salt if scope.minimize else None

    def emit(name: str, text: str) -> None:
        target = outdir / name
        target.write_text(_scrub(text, graph, salt))
        paths.append(target)

    emit("investigation_graph.json", graph.to_json(salt=salt))

    emit("entities.ftm.json",
         "\n".join(json.dumps(e) for e in to_ftm(graph, salt=salt)))

    emit("graph.cypher", to_cypher(graph, salt=salt))

    emit("attribution_report.md",
         report(graph, result, scope, show_scores=show_scores, trail=trail,
                salt=salt, status=status))

    emit("attribution_report.html",
         to_html(graph, result, scope, show_scores=show_scores, salt=salt,
                 status=status))

    if trail is not None:
        paths.extend(trail.write(outdir))

    return paths


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

_ALERT_CSS = (
    '.alert{background:#7f1d1d;color:#fff;padding:14px 18px;'
    'border-radius:8px;margin:0 0 18px;font-size:15px}'
)

_CSS = _ALERT_CSS + """  # noqa: E501
:root{--ink:#16161a;--mute:#6b6b76;--line:#e3e3e8;--bg:#fbfbfc;
--attr:#0f7b4f;--prob:#1a6fb4;--poss:#8a6d1f;--weak:#8a8a94}
*{box-sizing:border-box}
body{font:15px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif;
color:var(--ink);background:var(--bg);margin:0;padding:2.5rem 1.25rem}
main{max-width:60rem;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 .25rem}
h2{font-size:1.1rem;margin:2.5rem 0 .75rem;padding-bottom:.35rem;
border-bottom:1px solid var(--line)}
h3{font-size:.95rem;margin:1.5rem 0 .4rem}
.meta{color:var(--mute);font-size:.85rem;margin-bottom:1.5rem}
.meta code{background:#fff;padding:.1rem .35rem;border:1px solid var(--line);border-radius:3px}
table{width:100%;border-collapse:collapse;font-size:.86rem;background:#fff}
th{text-align:left;font-weight:600;color:var(--mute);font-size:.75rem;
text-transform:uppercase;letter-spacing:.04em;padding:.5rem .6rem;
border-bottom:1px solid var(--line)}
td{padding:.5rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
code,.id{font:.82em ui-monospace,SFMono-Regular,Menlo,monospace;
background:#f4f4f6;padding:.1rem .3rem;border-radius:3px}
.band{font-weight:600;font-size:.75rem;letter-spacing:.04em}
.STRONG_EVIDENCE{color:var(--attr)}
.MODERATE_EVIDENCE{color:var(--prob)}
.LIMITED_EVIDENCE{color:var(--poss)}.WEAK,.UNSUPPORTED{color:var(--weak)}
.bar{height:4px;background:var(--line);border-radius:2px;overflow:hidden;
margin-top:.3rem;max-width:7rem}
.bar>i{display:block;height:100%;background:currentColor}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;
padding:.9rem 1rem;margin-bottom:.75rem}
.card ul{margin:.4rem 0 0;padding-left:1.1rem}
.note{background:#fff8e6;border-left:3px solid #d9a520;padding:.7rem .9rem;
font-size:.85rem;margin:1rem 0}
.blocked{color:var(--mute);font-size:.85rem}
footer{color:var(--mute);font-size:.78rem;margin-top:3rem;
border-top:1px solid var(--line);padding-top:1rem}
"""


#: Display ceiling for the evidence-score bar, in nats. Arbitrary and labelled
#: as such: the bar shows relative strength on a fixed scale, not a percentage
#: of anything.
_NATS_CEILING = 20.0


def _bar(log_odds: float) -> float:
    return min(100.0, max(0.0, log_odds / _NATS_CEILING * 100.0))


def to_html(graph: AttributionGraph, result: ResolutionResult, scope: CaseScope,
            *, show_scores: bool = True,
    salt: bytes | None = None,
    status: dict | None = None,
) -> str:
    """Self-contained HTML report. No external assets, no JS, safe to email."""
    from html import escape

    # Status has to travel with the document. A report shared as a file carries
    # no exit code, so an invalid run looked like an ordinary assessment to
    # anyone holding only the artifact.
    banner = ""
    if status and status.get("result_valid") is False:
        banner = ("<div class=alert><strong>INVALID RESULT &mdash; DO NOT RELY "
                  "ON THIS ASSESSMENT.</strong> Internal defects occurred "
                  "during collection; the evidence set is incomplete in an "
                  "unknown way.</div>")
    elif status and status.get("result_complete") is False:
        banner = ("<div class=alert><strong>INCOMPLETE RESULT.</strong> A "
                  "configured limit stopped the search. An absence below may "
                  "mean &lsquo;not reached&rsquo; rather than &lsquo;not "
                  "present&rsquo;.</div>")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    p: list[str] = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        f"<title>Attribution assessment — {escape(graph.case_ref)}</title>",
        f"<style>{_CSS}</style><main>",
        f"{banner}" + "<h1>Attribution assessment</h1>",
        f"<p class=meta><code>{escape(graph.case_ref)}</code> &middot; "
        f"authorization: {escape(scope.authorization)} &middot; generated {now} &middot; "
        f"retention expires {scope.expiry().date()}</p>",
        f"<p class=meta>{len(graph.identifiers)} identifiers &middot; "
        f"{len(graph.claims)} claims &middot; {len(graph.entities)} entities</p>",
        "<div class=note><strong>Reading this report.</strong> Confidence language "
        "describes evidence strength, not measured probability: the model is "
        "uncalibrated. Anything below <span class='band MODERATE_EVIDENCE'>"
        "MODERATE_EVIDENCE</span> "
        "is a lead, not a finding. Links supported by a single evidence group are "
        "capped at <span class='band WEAK'>WEAK</span> by design, however strong the "
        "underlying observation. Selectivity counts drive every score — if this run "
        "used a per-case index rather than a corpus, the figures are upper bounds.</div>",
        "<h2>Resolved entities</h2>",
    ]

    multi = [e for e in graph.entities.values() if len(e.identifiers) > 1]
    if not multi:
        p.append("<p class=blocked>No identifiers resolved into multi-identifier "
                 "entities at the configured threshold.</p>")
    for e in sorted(multi, key=lambda x: -x.log_odds):
        p.append(f"<div class=card><h3>{escape(e.best_label)} "
                 f"<span class=meta>({e.type.value})</span></h3><ul>")
        for i in sorted(e.identifiers, key=lambda x: x.key):
            p.append(f"<li><span class=id>{escape(i.key)}</span></li>")
        p.append("</ul>")
        for k, v in (_mask_attributes(e.attributes, salt) or {}).items():
            p.append(f"<p class=meta><strong>{escape(k)}</strong>: "
                     f"{escape(', '.join(map(str, v)))}</p>")
        p.append("</div>")

    p += ["<h2>Pairwise assessments</h2><table><tr><th>A</th><th>B</th>"
          "<th>evidence score (nats)</th><th>Assessment</th><th>Groups</th></tr>"]
    ranked = sorted(result.assessments.items(), key=lambda kv: -kv[1].log_odds)
    shown = 0
    for (a, b), asmt in ranked:
        if asmt.band.value == "UNSUPPORTED" or shown >= 60:
            continue
        shown += 1
        d = " &middot; registry-definitional" if asmt.definitional else ""
        p.append(
            f"<tr><td><span class=id>{escape(a)}</span></td>"
            f"<td><span class=id>{escape(b)}</span></td>"
            # Bar width is a fraction of a nats ceiling, not a percentage.
            # It previously drew probability*100, which read as a confidence
            # meter for a quantity the project explicitly does not calibrate.
            f"<td class={asmt.band.value}>{asmt.log_odds:.3f}"
            f"<span class=bar title='{_bar(asmt.log_odds):.0f}% of "
            f"{_NATS_CEILING:.0f} nats'>"
            f"<i style='width:{_bar(asmt.log_odds):.0f}%'></i></span></td>"
            f"<td><span class='band {asmt.band.value}'>{asmt.band.value}</span> "
            f"<span class=meta>{escape(asmt.estimative)}{d}</span></td>"
            f"<td>{asmt.independent_groups}</td></tr>"
        )
    p.append("</table>")

    if result.rejected:
        p.append("<h2>Blocked merges</h2><table><tr><th>A</th><th>B</th><th>Reason</th></tr>")
        for a, b, why in result.rejected[:50]:
            p.append(f"<tr><td><span class=id>{escape(a)}</span></td>"
                     f"<td><span class=id>{escape(b)}</span></td>"
                     f"<td class=blocked>{escape(why)}</td></tr>")
        p.append("</table>")

    demoted = [c for c in graph.claims if c.weight == 0.0]
    if demoted:
        p.append(f"<h2>Non-probative edges ({len(demoted)})</h2>"
                 "<p class=meta>Observed and retained for context, contributing zero "
                 "weight. Shown rather than discarded so their absence from the scoring "
                 "is visible.</p><table><tr><th>Edge</th><th>Reason</th></tr>")
        for c in demoted[:30]:
            p.append(f"<tr><td><span class=id>{escape(c.subject.key)}</span> → "
                     f"<span class=id>{escape(c.object_key)}</span></td>"
                     f"<td class=blocked>{escape(str(c.raw.get('demoted','')))}</td></tr>")
        p.append("</table>")

    p.append("<h2>Sources and terms</h2><p class=meta>"
             + escape(source_attribution(graph)).replace("|", " ").replace("\n", "<br>")
             + "</p>")
    if show_scores:
        p.append("<div class=note>" + escape(CONFIDENCE_DISCLAIMER)
                 .replace("**", "") + "</div>")
    p.append(
        "<footer>Every claim carries collector, source URL, retrieval time and "
        "correlation group in <code>investigation_graph.json</code>. Cached response "
        "bodies are the evidentiary artifact; this report is derived from them. "
        "Generated by attribution-graph.</footer></main></html>"
    )
    return "".join(p)
