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
    from .obfuscation import ObfuscationLog, normalize_value

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
    return "\n".join(out)


def deliberately_obfuscated(graph: AttributionGraph) -> int:
    """How many claims carry an identifier someone worked to obscure.

    Two signals exist for this and neither was read. `Claim._record_obfuscation`
    stores a per-claim verdict at construction, reachable through
    `Claim.deliberate_obfuscation`, but only when the collector recorded the
    pre-normalisation value in `_raw_subject`/`_raw_object`. The report section
    above instead recomputes from `observed_as`, which every identifier has.

    So this prefers the stored verdict and falls back to recomputing, and both
    the section and the executive summary go through it. Before this, the
    stored verdict had no reader at all and deliberate obfuscation -- a
    homoglyph or a zero-width character in an identifier, which nobody types by
    accident -- reached a subsection an analyst might scroll past and never the
    summary that gets quoted.
    """
    from .obfuscation import normalize_value

    count = 0
    for c in graph.claims:
        if c.deliberate_obfuscation:
            count += 1
            continue
        for ident in (c.subject, c.object):
            if not isinstance(ident, Identifier):
                continue
            r = normalize_value(ident.observed_as, case_fold=False,
                                collapse_space=True)
            if r.deliberate:
                count += 1
                break
    return count


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


#: Node styling for the map, by identifier kind. Shape carries meaning so the
#: picture is readable in black and white and by anyone who cannot distinguish
#: the colours: the subject and the parties who get paid are not the same kind
#: of thing as a fingerprint.
_MAP_SHAPES: dict[str, tuple[str, str, str]] = {
    "domain": ("[", "]", "site"),
    "seller_id": ("[/", "/]", "payee"),
    "org_name": ("[(", ")]", "org"),
    "lei": ("[(", ")]", "org"),
    "cik": ("[(", ")]", "org"),
    "company_number": ("[(", ")]", "org"),
    "person_name": ("((", "))", "person"),
    "email": ("((", "))", "person"),
    "gravatar_hash": ("((", "))", "person"),
    "handle": ("((", "))", "person"),
}
_MAP_DEFAULT = ("{{", "}}", "tech")

#: Edge count beyond which the picture stops being one. A map nobody can read
#: is not evidence of anything.
MAP_MAX_EDGES = 40


def _mermaid_label(key: str, limit: int = 44) -> str:
    """A node label that survives a Mermaid fence.

    `_md` escapes markdown, which is right everywhere else and wrong here:
    inside a fence `org\\_name` renders as `org\\_name`. The kind prefix is
    dropped because the node's shape already carries it, and a label is only
    readable if it is short.
    """
    value = str(key)
    if ":" in value:
        head, rest = value.split(":", 1)
        if head in {k.value for k in IdKind}:
            value = rest
    value = value.replace('"', "'").replace("\\", "/").replace("|", "/")
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def _map_node(kind: str, key: str, ids: dict) -> str:
    if key not in ids:
        ids[key] = f"n{len(ids)}"
    return ids[key]


def network_map(graph: AttributionGraph, result: ResolutionResult,
                limit: int = MAP_MAX_EDGES) -> list[str]:
    """A Mermaid flowchart of the pivots, strongest links first.

    Built from the assessments rather than from raw claims: an edge here is a
    link the model actually supports, labelled with its band, so the picture
    cannot imply a conclusion the text does not make. Bands are evidence
    strength, never probability, and the legend says so -- a diagram travels
    further than the paragraph under it, usually into a slide.
    """
    ranked = [((a, b), m) for (a, b), m in
              sorted(result.assessments.items(), key=lambda kv: -kv[1].log_odds)
              if m.band is not Band.UNSUPPORTED]
    if not ranked:
        return []

    shown, ids = ranked[:limit], {}
    lines = ["## Network map", "",
             "Nodes are identifiers, edges are supported links. Shapes: "
             "`[site]`, `[/payee/]`, `([organisation])`, `((person))`, "
             "`{other}`.", "",
             "```mermaid", "flowchart LR"]

    for (a, b), _asmt in shown:
        for key in (a, b):
            if key in ids:
                continue
            kind = str(key).split(":", 1)[0]
            open_, close_, cls = _MAP_SHAPES.get(kind, _MAP_DEFAULT)
            node = _map_node(kind, key, ids)
            label = _mermaid_label(key)
            lines.append(f'    {node}{open_}"{label}"{close_}:::{cls}')

    for (a, b), asmt in shown:
        style = "==>" if asmt.band is Band.STRONG_EVIDENCE else "-->"
        lines.append(f"    {ids[a]} {style}|{asmt.band.value}| {ids[b]}")

    lines += [
        "    classDef site fill:#e8f0fe,stroke:#1a73e8,color:#102a43;",
        "    classDef payee fill:#fff4e5,stroke:#b26a00,color:#3d2600;",
        "    classDef org fill:#e9f7ef,stroke:#1e7b45,color:#0b3d22;",
        "    classDef person fill:#fdecef,stroke:#b3261e,color:#410e0b;",
        "    classDef tech fill:#f2f2f7,stroke:#5f6368,color:#202124;",
        "```", "",
    ]
    if len(ranked) > limit:
        lines += [f"_{len(ranked) - limit} further supported link(s) are in the "
                  "assessments table rather than the map; a diagram stops being "
                  "readable long before the data runs out._", ""]
    lines += ["Thick edges are STRONG_EVIDENCE. A band orders evidence "
              "strength and is not a probability — the model is not "
              "calibrated.", ""]
    return lines


#: Observable kinds worth handing to a SIEM or a takedown notice, with the
#: label each one is usually called by outside this toolkit.
_IOC_KINDS: dict[str, str] = {
    "domain": "domain",
    "ip": "ipv4-addr",
    "email": "email-addr",
    "analytics_id": "analytics-id",
    "seller_id": "adtech-seller-id",
    "service_id": "service-id",
    "gravatar_hash": "gravatar-md5",
    "cert_sha256": "x509-sha256",
    "favicon_mmh3": "favicon-mmh3",
    "pgp_fpr": "pgp-fingerprint",
    "ssh_fpr": "ssh-fingerprint",
    "asn": "asn",
    "url": "url",
    "handle": "user-account",
}

#: Rows beyond which the table is a data dump rather than a table.
IOC_MAX_ROWS = 120


def ioc_table(graph: AttributionGraph) -> list[str]:
    """Observables, with where each came from and what it is worth.

    An IOC table without provenance is a list of strings somebody has to take
    on faith, so every row carries the collector and the retrieval it came
    from, and the ones a subject published about itself are marked -- those are
    the rows an operator can choose to be true.
    """
    rows: list[tuple[str, str, str, str, str, int]] = []
    for ident in graph.identifiers.values():
        label = _IOC_KINDS.get(ident.kind.value)
        if not label:
            continue
        supporting = [c for c in graph.claims
                      if ident.key in (getattr(c.subject, "key", ""),
                                       getattr(c.object, "key", ""))]
        if not supporting:
            continue
        collectors = sorted({c.collector for c in supporting if c.collector})
        first = min((c.retrieved_at for c in supporting), default=None)
        demoted = all(c.weight <= 0.0 for c in supporting)
        self_pub = all(
            str(c.correlation_group or "").split("|")[0]
            in ("imprint", "dns_txt", "ownerdomain", "surface")
            for c in supporting)
        note = []
        if demoted:
            note.append("DEMOTED: failed a planted-identifier check")
        if self_pub:
            note.append("self-published: the subject's own statement")
        rows.append((label, ident.value,
                     ", ".join(collectors[:3]) or "—",
                     first.date().isoformat() if first else "—",
                     "; ".join(note) or "—",
                     len(supporting)))

    if not rows:
        return []

    rows.sort(key=lambda r: (r[0], r[1]))
    lines = ["## Indicators", "",
             "| Type | Value | First retrieved | Claims | Collector(s) | Note |",
             "|---|---|---|---|---|---|"]
    for label, value, collectors, first, note, n in rows[:IOC_MAX_ROWS]:
        lines.append(f"| {label} | `{_md(value, 120)}` | {first} | {n} | "
                     f"{collectors} | {note} |")
    if len(rows) > IOC_MAX_ROWS:
        lines.append(f"| … | _{len(rows) - IOC_MAX_ROWS} further observable(s) "
                     "in `investigation_graph.json`_ | | | | |")
    lines += ["",
              "Every row is an observation with provenance, not a verdict. A "
              "DEMOTED row is kept deliberately: an identifier placed to "
              "manufacture a link is a fact about whoever placed it.", ""]
    return lines


def executive_summary(graph: AttributionGraph, result: ResolutionResult,
                      scope: CaseScope, status: dict | None) -> list[str]:
    """What was asked, what came back, and how far it can be relied on.

    First section, plain sentences, no table. Written because the people who
    decide what to do next read one section and the detail below it answers
    questions they have not asked yet.
    """
    status = status or {}
    seeds = ", ".join(str(s) for s in (getattr(scope, "seeds", []) or [])) or "—"

    ranked = [((a, b), m) for (a, b), m in
              sorted(result.assessments.items(), key=lambda kv: -kv[1].log_odds)
              if m.band in (Band.STRONG_EVIDENCE, Band.MODERATE_EVIDENCE)]
    named = [e for e in sorted(graph.entities.values(), key=lambda x: -x.log_odds)
             if len(e.identifiers) > 1]

    lines = ["## Executive summary", ""]
    lines.append(f"**Subject.** {_md(seeds, 160)}")
    lines.append("")

    if named:
        top = named[0]
        lines.append(
            f"**What the evidence supports.** {_md(top.best_label)} "
            f"({top.type.value}), resolved from {len(top.identifiers)} "
            f"identifiers. {len(named) - 1} further entit"
            f"{'y was' if len(named) == 2 else 'ies were'} resolved."
            if len(named) > 1 else
            f"**What the evidence supports.** {_md(top.best_label)} "
            f"({top.type.value}), resolved from {len(top.identifiers)} "
            f"identifiers.")
    elif ranked:
        (a, b), asmt = ranked[0]
        lines.append(
            f"**What the evidence supports.** The strongest supported link is "
            f"`{_md(a)}` to `{_md(b)}` at {asmt.band.value}. No entity "
            "resolved above the merge threshold, so there is no named party "
            "to report.")
    elif graph.claims:
        # Claims were collected; none reached a reportable band. Saying "a
        # site with no advertising declarations publishes no payee" here was
        # wrong and misleading: the declarations were read, and what stopped
        # the finding was corroboration, not absence. A reader seeing a payee
        # on the network map above that sentence has every reason to distrust
        # the whole report.
        best = max((m for _p, m in result.assessments.items()),
                   key=lambda m: m.log_odds, default=None)
        band = f" The strongest is {best.band.value}." if best is not None else ""
        lines.append(
            f"**What the evidence supports.** Declarations were read and "
            f"{len(graph.claims)} claim(s) collected, but no link reached a "
            f"reportable band.{band} That usually means every link so far "
            "rests on a single source — most often the site's own ads.txt, "
            "which is self-published — and a second independent source is "
            "what would raise it. It does not mean nothing is there.")
    else:
        lines.append(
            "**What the evidence supports.** Nothing was collected at all. If "
            "retrievals were blocked, that is why; otherwise a site with no "
            "advertising declarations publishes no payee, which is a result "
            "rather than a failure.")
    lines.append("")

    strong = sum(1 for _, m in ranked if m.band is Band.STRONG_EVIDENCE)
    lines.append(
        f"**How strong.** {strong} link(s) at STRONG_EVIDENCE and "
        f"{len(ranked) - strong} at MODERATE_EVIDENCE, from "
        f"{len(graph.claims)} claims over {len(graph.identifiers)} "
        "identifiers. Bands order evidence strength; they are not "
        "probabilities, and the model is not calibrated.")
    lines.append("")

    caveats: list[str] = []
    if status.get("result_valid") is False:
        caveats.append("the result is **INVALID** — collectors failed in ways "
                       "that are defects, so the evidence set is missing "
                       "pieces in an unknown way and the findings should not "
                       "be used")
    if status.get("result_complete") is False:
        caveats.append("the result is **INCOMPLETE**: an absence below may "
                       "mean 'could not be checked' rather than 'checked and "
                       "not found'")
    if status.get("ads_txt_consistency", "").startswith("DIFFERENT"):
        caveats.append("**the ads.txt declaration was not the same for every "
                       "retrieval**, so the payee below is the copy this tool "
                       "was shown")
    if status.get("hosts_unreachable"):
        caveats.append(f"{status['hosts_unreachable']} host(s) could not be "
                       "reached from the machine that ran this, which is an "
                       "environment condition rather than anything about "
                       "those sites")
    if status.get("adversarial_demoted"):
        caveats.append(f"{status['adversarial_demoted']} claim(s) were demoted "
                       "to weight 0 by the planted-identifier checks and kept "
                       "in the record")
    if status.get("gravatar_links"):
        caveats.append(f"{status['gravatar_links']} email(s) were bound to an "
                       "avatar hash by computation; that shows the page "
                       "displays the address's avatar, not that its operator "
                       "controls the address")
    if status.get("egress_failed_open"):
        caveats.append("**the configured proxy was not carrying this run's "
                       "traffic** — the preflight found a direct request "
                       "reporting the same address, so every capture here was "
                       "taken from this machine, not the declared exit")
    elif status.get("egress_vantage"):
        caveats.append(f"captures were taken through a declared egress, "
                       f"apparent source `{_md(str(status['egress_vantage']), 48)}` "
                       "— not this machine's own connection")
    if status.get("tls_intercepted"):
        caveats.append("**TLS was intercepted** by the proxy this ran behind, "
                       "so a capture here proves what that proxy served rather "
                       "than what the origin served")
    obscured = deliberately_obfuscated(graph)
    if obscured:
        caveats.append(f"{obscured} claim(s) carry an identifier that had to be "
                       "normalised by a **deliberate** technique — a homoglyph, "
                       "a zero-width character, a bidi override — which nobody "
                       "applies by accident; see Obfuscation below")
    if any(len(e.identifiers) > 1 and e.type.value.lower() in ("person", "persona")
           for e in graph.entities.values()):
        caveats.append("a conclusion about a natural person is a **lead** "
                       "until a statutory register corroborates it")

    lines.append("**What to read with care.**")
    lines.append("")
    lines += [f"- {c}" for c in (caveats or
                                 ["nothing beyond the calibration caveat above"])]
    lines.append("")
    return lines


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
    ]
    L += executive_summary(graph, result, scope, status)
    L += network_map(graph, result)
    L += ["## Resolved entities", ""]

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

    L += ioc_table(graph)
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

    # The diagram as its own file. The HTML report is deliberately JS-free and
    # emailable, so it cannot render Mermaid without a CDN script -- and a
    # report that phones out to a CDN is not one a security team will forward.
    # The markdown fence renders in GitHub, GitLab, VS Code and Obsidian; this
    # file covers everything else.
    fence = network_map(graph, result)
    if fence:
        body = "\n".join(fence)
        src = body.split("```mermaid", 1)[1].split("```", 1)[0].strip()
        emit("network_map.mmd", src + "\n")

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


def _map_source(graph: AttributionGraph, result: ResolutionResult) -> str:
    """Just the Mermaid, without the markdown fence around it."""
    fence = network_map(graph, result)
    if not fence:
        return ""
    body = "\n".join(fence)
    if "```mermaid" not in body:
        return ""
    return body.split("```mermaid", 1)[1].split("```", 1)[0].strip()


def _html_inline(text: str) -> str:
    """Markdown bold and code spans to HTML, everything else escaped."""
    import re
    from html import escape

    out = escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


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
    ]

    # The summary a decision-maker reads, and the observables an analyst
    # copies out. Rendered from the same functions as the markdown so the two
    # documents cannot disagree.
    from html import escape as _esc

    summary_md = executive_summary(graph, result, scope, status)
    if summary_md:
        p.append("<h2>Executive summary</h2>")
        for line in summary_md[2:]:
            if not line.strip():
                continue
            if line.startswith("- "):
                p.append(f"<p class=meta>&bull; {_html_inline(line[2:])}</p>")
            else:
                p.append(f"<p>{_html_inline(line)}</p>")

    if fence_src := _map_source(graph, result):
        p.append("<h2>Network map</h2>")
        p.append("<p class=meta>Mermaid source, also written as "
                 "<code>network_map.mmd</code>. This report carries no "
                 "scripts and loads nothing, so it cannot draw the diagram "
                 "itself; paste the block into any Mermaid renderer.</p>")
        p.append(f"<pre><code>{_esc(fence_src)}</code></pre>")

    rows = ioc_table(graph)
    if rows:
        p.append("<h2>Indicators</h2>")
        p.append("<table><thead><tr><th>Type<th>Value<th>First retrieved"
                 "<th>Claims<th>Collector(s)<th>Note</tr></thead><tbody>")
        for line in rows[4:]:
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 6:
                continue
            p.append("<tr>" + "".join(f"<td>{_html_inline(c)}"
                                      for c in cells) + "</tr>")
        p.append("</tbody></table>")
        p.append("<p class=meta>Every row is an observation with provenance, "
                 "not a verdict. A DEMOTED row is kept deliberately: an "
                 "identifier placed to manufacture a link is a fact about "
                 "whoever placed it.</p>")

    p.append("<h2>Resolved entities</h2>")

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
