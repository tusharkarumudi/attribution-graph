# attribution-graph — method guide

In-depth reference for the method. The [README](../README.md) covers
what the package does and how to start.

## The one thing to get right

`correlation_group`. It defines what counts as one observation.

- All SANs on one certificate → one group
- All profiles from one username enumeration → one group
- All officers in one filing → one group

Get it wrong and the model produces confident wrong answers. Nothing else
catches it.

---

## Writing a collector

```python
class MyCollector:
    name = "my_registry"
    accepts = (IdKind.ORG_NAME,)
    source_class = SourceClass.PUBLIC_REGISTRY   # denied classes raise at load
    priority = 2

    async def collect(self, ident):
        return [Claim(
            subject=ident,
            predicate=Predicate.SAME_AS,
            object=Identifier(IdKind.LEI, "5493001KJTIIGC8Y1R12"),
            collector=self.name,
            source_url="https://...",
            reliability=Reliability.AUTHORITATIVE,
            correlation_group="gleif|5493001KJTIIGC8Y1R12",
        )]
```

Annotated template: `paytrace/examples/reference_collector.py`.

---

## Outputs

| File | Contents |
|---|---|
| `attribution_report.md` / `.html` | Findings, evidence-strength bands, source terms |
| `verification_trail.md` | Ordered timestamped steps, numbered citations |
| `investigation_graph.json` | Every claim with provenance |
| `entities.ftm.json` | FollowTheMoney — loads into yente / Aleph |
| `graph.cypher` | Neo4j |
| `evidence/verify.py` | Standalone integrity checker, no dependencies |
| `evidence/DECLARATION_DRAFT.md` | Qualified-person certification skeleton |

Scores are optional: `write_all(..., show_scores=False)`.

---

## Evidence packages

```python
log = EvidenceLog(scope, Path("./out/evidence"))
log.record(url, status, body, collector="gleif")
log.record_negative(url, "companies_house_uk", "no match on 2026-08-19")
write_evidence_package(log, findings_summary)
```

Content-addressed captures, hash chain (removing any capture breaks every
subsequent entry), RFC 3161 timestamping instructions, declaration skeleton.

> Live web findings are **not reproducible**. Re-running next year returns
> different content. The preserved bodies are the artifact; the trail explains
> the reasoning.

---

## Calibration

```bash
python -m attribution_graph.calibrate report --corpus pairs.json \
    --operational-prevalence 1e-5
python -m attribution_graph.calibrate fit --corpus pairs.json
```

Two things it enforces that calibration studies routinely get wrong:

- **Prevalence correction.** You label a set that is 30–50% positive; the
  operational base rate is ~1e-5. Uncorrected precision overstates field
  performance by orders of magnitude.
- **Case-level splitting.** Pairs from one investigation share evidence.
  Splitting on pairs leaks.

Protocol: [CALIBRATION.md](../CALIBRATION.md).

---

## Scope enforcement

Runtime, not documentation.

- No authorization reference → won't start
- Denied source class → raises at collector load
- `pivot_radius` caps hops from an authorized seed
- Case scoped to `Company` won't instantiate `Person`
- `minimize: true` → salted-hash identifiers at rest
- Every collector call → append-only audit log

---
