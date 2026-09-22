# attribution-graph

[![CI](https://github.com/tusharkarumudi/attribution-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharkarumudi/attribution-graph/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/attribution-graph.svg)](https://pypi.org/project/attribution-graph/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Ranks whether observed identifiers denote the same real-world entity, by
**evidence strength**, with the provenance needed for independent review.

Not a calibrated probability: `log(1/selectivity)` informs the denominator of a
likelihood ratio and `P(evidence | same entity)` is not modelled. Output carries
`calibration_status: "unvalidated"`. See `METHODOLOGY_AUDIT.md`.

Pure inference. No network I/O. Bring your own collectors.

```bash
pip install attribution-graph
```

---

## What it does

| Input | Output |
|---|---|
| Claims about identifiers, with provenance | Entity clusters with an evidence-strength band |
| | Evidence-strength report, FollowTheMoney, Neo4j Cypher |
| | Hash-chained evidence package |
| | Timestamped verification trail |

## What it does not do

- Collect anything. Use [paytrace](https://github.com/tusharkarumudi/paytrace).
- Report meaningful probabilities without a corpus. See [Selectivity](#selectivity).
- Give you a validated error rate. See [Status](#status).

---

## Quickstart

```python
from attribution_graph import assess

a = assess(claims, holder_lookup=lambda ident: corpus.count(ident))
print(a.probability, a.band.value, a.estimative, a.independent_groups)
# 4.49 STRONG_EVIDENCE 'strongly supported by the evidence' 2
```

Full run with an engine:

```python
from attribution_graph import CaseScope, Engine, write_all

scope  = CaseScope.load("case.yaml")     # refuses to load without an authorization ref
engine = Engine(scope, collectors=[MyCollector()])
result = asyncio.run(engine.run())
write_all(engine.graph, result, scope, Path("./out"))
```

Runnable: `python examples/worked_example.py`

---

## The model

```
llr = reliability × weight × decay(age) × log(1 / selectivity)
```

| Rule | Effect |
|---|---|
| Selectivity is measured, not assigned | Unique analytics ID ≈ 14 nats; shared CDN IP ≈ 0 |
| Evidence aggregates by `correlation_group` | 400 commits from one repo count once |
| Group cap (8.0) < prior (11.5) | No single inferential source can carry a merge |
| Registry `SAME_AS` is exempt | GLEIF can resolve a company to its own LEI |
| Machine-generated assertions capped at UNCERTAIN | An LLM summary corroborates; it cannot establish |

Derivation: [METHOD.md](METHOD.md). Design rationale: [docs/adr/](docs/adr/).

---

## Selectivity

`InMemoryIndex` (the default) counts only the current case, so everything looks
unique.

> **Confidence figures from a corpus-less run are upper bounds, not
> assessments.**

Supply a real corpus:

```python
engine = Engine(scope, collectors,
                index=CompositeIndex(CorpusIndex(), InMemoryIndex(graph)))
```

---

## Further reading

The method in depth — The one thing to get right, Writing a collector, Outputs, Evidence packages, Calibration, Scope enforcement — is in
[docs/GUIDE.md](docs/GUIDE.md).

## Security

- SSRF, ReDoS and path-traversal regression tests in the companion packages
- `bandit` and `pip-audit` in CI
- Report vulnerabilities via GitHub Security Advisories ([SECURITY.md](SECURITY.md))

---

## Deploying

Release checklist and operating notes: [DEPLOYMENT.md](https://github.com/tusharkarumudi/attribution-suite/blob/main/DEPLOYMENT.md).

Investigation artifacts — case files, evidence packages, audit logs, corpus
indexes — must never enter git history. Three guards: `.gitignore`,
`scripts/pre-commit`, and an `artifact-guard` CI job.

```bash
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
```

Better: run investigations outside the repo.

```bash
mkdir -p ~/cases/CASE-001 && cd ~/cases/CASE-001
attribution run --case case.yaml --out .
```

---

## Screenshot evidence

> **Not wired into `attribution run`.** The flag exits 2 with an explanation.
> `ScreenshotCapturer` launches a browser that navigates the target directly,
> so its subresource requests do not pass through the URL policy that guards
> every other fetch — a second network stack outside the SSRF boundary. Do not
> point it at an untrusted target from a host with access to internal networks
> or cloud metadata.

```bash
# NOT available: `attribution run --screenshots` exits 2.
# Use attribution_graph.ScreenshotCapturer directly, understanding that it
# launches a browser OUTSIDE the SSRF boundary (see below).
```

Captures a rendering of each page alongside the wire bytes, records **where on
the page** each finding appeared, and writes a plain-text log.

```
[0001]
URL          https://viewer-site.example/
Captured at  2026-09-03T16:47:06+00:00
Status       captured
Image        screenshots/0001.png
SHA-256      6a8ede5f807e37a9ed9f57aa03d5808a...
Viewport     1440x900 (full page)
Renderer     playwright/chromium
Page title   ExampleViewer — Story Viewer
Renders      body sha256 9f3a2b1c...
Findings
  - in the footer, at (8, 140), matching `footer > div.contact > a`
    text: info@viewer-site.example
Chain        seq 1, prev (genesis)
Entry hash   4d8e1a...
```

**A screenshot is a rendering, not a capture.** The bytes on the wire are the
primary evidence and verify by hash; a screenshot is what one browser displayed
from them, once, at one viewport, possibly after JavaScript. It is recorded as a
derived artifact with its own hash chain and never offered as proof of what was
served. What it is good for: content that only exists after JavaScript runs,
*where* on the page a finding sat, and what a visitor would actually have seen.

Four locators per finding — CSS selector, DOM path, bounding box, surrounding
text — because each fails differently. Selectors break on reflow, coordinates
break on a viewport change, text survives both and says nothing about position.

Annotation is a **second image**. The unannotated capture is hashed first and is
the one to verify; a highlighted version is a separate derived file. An
annotated image with no original is an illustration, not evidence.

Needs a headless browser (`pip install playwright && playwright install
chromium`). Without one the run records that a screenshot was **not captured**,
with the reason — an absent screenshot is not a blank page.

Outputs: `SCREENSHOT_LOG.txt` (readable without this package) and
`screenshot_manifest.json`, both kept separate from `evidence_manifest.json` so
a rendering cannot be mistaken for a capture.

## Known limitations

`METHODOLOGY_AUDIT.md` is an adversarial read of this toolkit, written as if by
a reviewer with no stake in it. Read it before relying on a number.

The governing limitation: **calibration is unvalidated.** Every probability is a
defensible ordering, not a measured frequency. And of nine identified failure
modes, **four bias toward overconfidence and none bias low** — an asymmetry that
is a direct consequence of having nothing fitted to catch it.

## Status

See `CHANGELOG.md`. Pre-release; API unstable.

**Calibration is not validated against ground truth.** The bands are principled,
not fitted. Read probabilities as ordinal until the study in
[CALIBRATION.md](CALIBRATION.md) is done — it is issue #1 and the highest-value
open item.

---

## Companion packages

- [paytrace](https://github.com/tusharkarumudi/paytrace) — collectors, corpus index, agent
- [handle-correlation](https://github.com/tusharkarumudi/handle-correlation) — same-actor scoring for handles
- [attribution-suite](https://github.com/tusharkarumudi/attribution-suite) — one install, one CLI

---

## Author

**Tushar Karumudi** — [github.com/tusharkarumudi](https://github.com/tusharkarumudi)

## License

Copyright 2026 Tushar Karumudi.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

Cite via [CITATION.cff](CITATION.cff).
