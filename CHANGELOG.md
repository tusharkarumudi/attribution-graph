# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [2.0.3] - 2026-09-24

- Resolved entities carry the DIRECT/RESELLER label of the accounts that
  reached them, and an entity reached ONLY through RESELLER declarations says
  so. `openx.com,537153564,RESELLER` under a publisher's `## Undertone ##` block
  is OpenX reselling Undertone's inventory; its legal entity was listed flat
  beside the subject, implying a connection the ads.txt explicitly denies.

## [2.0.2] - 2026-09-24

- Minimisation replaces values at token boundaries, not as bare substrings.
  A company suffix ate every word containing it: "Inc" turned
  "INCOMPLETE RESULT" into "min:7a31...OMPLETE RESULT" and
  "incorporated_in" into "min:7a31...orporated_in", corrupting the report
  wherever a value happened to be a common substring.

## [2.0.1] - 2026-09-21

- Documentation, examples and test fixtures now use only placeholder data.
  2.0.0 has been withdrawn; upgrade to 2.0.1.
- Dependencies between the four packages now require `>=2.0.1,<2.1`.

## [0.1.0] - 2026-08-17

Initial public release.

### Added
- LLR evidence model with selectivity-derived weights (`scoring.py`)
- Correlation-group aggregation preventing correlated evidence from stacking
- Per-predicate temporal decay with configurable half-lives
- Definitional carve-out for authoritative registry identity assertions
- Confidence-ordered weighted union-find with must-not-link constraints (`resolve.py`)
- DROP/DEMOTE filter verdicts (`filters.py`)
- Executable case scope: authorization requirement, pivot radius, entity-type
  gating, source-class deny list, salted-hash minimization, audit log (`scope.py`)
- Collector and SelectivityIndex protocols; no network I/O in the core
- FollowTheMoney, Neo4j Cypher and ICD 203 report exporters

### Added (0.2.0)
- Self-contained HTML report exporter (`to_html`), no external assets or JS
- Non-probative (demoted) edges shown in reports rather than silently dropped

### Added (0.3.0)
- `fetchpolicy`: declared robots policy (respect/record/ignore) written into the
  evidence manifest and declaration draft
- `CaseScope.allows_persona_collector()` two-key gate and
  `allow_username_enumeration` third key

### Added (0.4.0)
- `calibration` module: reliability diagrams, ECE/MCE, Brier decomposition with
  reliability/resolution split, per-band precision, Platt and isotonic
  recalibration, case-level splitting, prevalence correction
- `python -m attribution_graph.calibrate` CLI
- `CALIBRATION.md` protocol

### Added (0.6.0)
- METHOD.md §2.6: machine-generated assertions as an evidence class
- Ten ADRs in docs/adr/
- Invariant tests for GROUP_CAP < |log(PRIOR_ODDS)| and the definitional cap

### Added (0.7.1)
- Artifact hygiene: .gitignore covering every investigation artifact class,
  scripts/pre-commit blocking them by filename and content, and an
  artifact-guard CI job failing the build if any are tracked

### Security (0.8.0)
- SSRF guard: URLs built from collected data are validated before connecting —
  scheme, userinfo, port, and DNS resolution against private/reserved ranges.
  Redirects re-validated per hop. Previously an ads.txt line reading
  `169.254.169.254, 1, DIRECT` would fetch cloud metadata.
- ReDoS: bounded the email regexes (170ms on a crafted 8KB payload)
- Path traversal: evidence body_path is confined to the package directory
- Response size cap; bandit and pip-audit in CI
- Corrected stale dependency pins (attribution-graph>=0.1.0 -> current)

### Changed (0.8.0)
- README rewritten: reference tables and commands, rationale moved to METHOD.md
  and docs/adr/

### Changed (0.9.0)
- adtx-attribution renamed to **paytrace**. Module `paytrace`, CLI `paytrace`
  and `paytrace-index`. The old name was clumsy and no longer accurate — the
  package covers registries, land records, code hosts and dark web ingest.

### Added (0.9.0)
- `attribution ask` / `attribution_suite.Investigator`: LLM orchestrator across
  the whole toolchain. Routes by subject type, runs the right packages, writes
  the report. Falls back to a deterministic planner without an API key.
- Name-keyed searches on private individuals refused before any I/O

### Security (0.10.0)
- **Correlation-group inflation fixed.** Cosmetic variants of one identifier
  (case, zero-width marks, homoglyphs, confusable punctuation) produced separate
  correlation groups, turning one observation into several. Measured: 4 groups
  and an ATTRIBUTED band from a single fact. Identifiers now normalize on
  construction and group labels canonicalize in scoring, so the defense is
  structural rather than dependent on each collector.
- Person-search refusal is now case-insensitive and normalizes its input — a
  capitalized "Who Is" previously walked past it.
- Subject routing normalizes the question; a zero-width space in a domain no
  longer defeats it.

### Added (0.10.0)
- `obfuscation` module: detects and folds zero-width, bidi, homoglyph,
  confusable-punctuation, compatibility and control-character techniques
- Obfuscation is recorded, never silently repaired. `Identifier.observed_as`
  keeps the source form without affecting keys or equality.
- Reports gain an Obfuscation section separating deliberate techniques from
  incidental whitespace
- 111 adversarial tests across the four packages, run as their own CI job

### Fixed (0.11.1) — the corroboration cap was inert
`band_for` used `min` over a rank where ATTRIBUTED is 0, so it returned the
*stronger* band. The single-source cap therefore did nothing above p=0.55 —
precisely the range it exists for. A link with one correlation group and p=1.0
reported ATTRIBUTED.

Every synthetic test passed because their probabilities were already low enough
that both branches agreed. Surfaced by a live run against a real domain.

### Changed (1.0.0) — cross-package consolidation
Reverification across all four packages found three architectural faults that
only appear at the seams:

- **Duplicated name variation.** `attribution_graph.translit` produced 400
  matching forms; `paytrace.enrich` had its own producing 7, overlapping on 1.
  A lookup silently using the weaker set missed records. paytrace now delegates
  to the core, which gained `lookup_variants()` — bounded, ordered by transform
  depth, and restricted to lookup-safe rules (7 of 23). Matching still casts the
  wide net; querying spends a request per form.
- **Handle generation lived in the ad-tech package.** Moved to
  handle-correlation, where handle formation is the subject. paytrace
  soft-imports it.
- **Two incompatible fetcher protocols.** Collectors use async `get()`;
  agent tools use sync `get_text()`. Assuming either raised AttributeError
  mid-run and lost the investigation. Both are now supported and
  capability-checked; unifying them is tracked as an open item.
- Index capability is checked, not assumed — any object may be passed as one.
- Name expansion wired into the suite orchestrator.

### Changed (1.1.0) — undeclared sources contribute nothing to an absence
`DEFAULT_COVERAGE` was 0.50, described as a coin flip. That gave every
unregistered source's empty result ~0.26 nats of negative weight. A coin flip is
not neutrality: an absence in a source whose coverage was never measured
supports nothing, and treating ignorance as 50% confidence let a lookup service
with no published coverage argue against a link. Now 0.0, which forces
declaration.

### Added (1.1.0)
- `absence_claim(note=...)` carries why an absence means what it means
- Declared coverage for seven further sources

### Added (1.2.0)
- `IdKind.SERVICE_ID` for third-party service identifiers (Disqus, Intercom,
  Crisp, Sentry) shared across an operator's properties

### Added (1.3.0) — conditional dependence, and honest provenance
An independent methodological audit (METHODOLOGY_AUDIT.md) found two real flaws:

- **Conditional independence across groups was assumed and false.** Correlation
  groups prevent stacking within a source; summing across groups assumed
  independence. One operator configuring one property emits analytics, favicon
  and header artifacts together — three groups, one decision, scoring
  ATTRIBUTED at p=1.0000. `dependence.py` assigns dependence classes,
  aggregates sub-additively within a class, and counts classes rather than raw
  groups for corroboration. Discounts are unfitted and documented as such.
- **Coverage figures were guesses formatted as measurements.** DEFAULT_COVERAGE
  was corrected from 0.50 to 0.0 because a guessed figure gives an absence
  unearned weight — and shipped beside fourteen equally guessed figures.
  `CoverageBasis` now records provenance: 4 checkable, 10 author estimates.

### Added (1.4.0) — screenshot evidence
- `ScreenshotCapturer`: renderings with their own SHA-256 hash chain, separate
  from the wire-capture manifest so a rendering cannot be mistaken for a capture
- Element location per finding: CSS selector, DOM path, bounding box, region
  and surrounding text — four locators because each fails differently
- Capture timestamp, viewport, renderer and page title recorded
- `SCREENSHOT_LOG.txt`: plain-text export readable without this package
- Annotated images are separate derived files referencing an unmodified parent
- Graceful degradation: no browser records *not captured* with a reason, since
  an absent screenshot is not a blank page
- Path traversal refused on `image_path` when verifying a foreign package

### Changed (1.6.0) — independent review
Twenty-five findings from an independent review, all addressed.

- **Bands renamed** from probability to evidence-strength language:
  ATTRIBUTED/PROBABLE/POSSIBLE became STRONG_EVIDENCE/MODERATE_EVIDENCE/
  LIMITED_EVIDENCE. ICD 203 estimative phrasing replaced. Downstream users
  quote the strongest surface, and a disclaimer elsewhere does not travel with
  a word pasted into a report.
- Output carries `calibration_status` and a note stating what the number is:
  log(1/selectivity) informs the denominator of a likelihood ratio;
  P(evidence | same entity) is not modelled.
- **Dependence is typed metadata.** Claim.dependence_class is declared by the
  collector and wins over name matching. Unrecognised evidence is now UNKNOWN
  and discounted, not assumed independent — for a model whose dominant failure
  mode is overconfidence, unrecognised evidence should fail conservative.
- Version reads source truth rather than installation metadata.
- CI fails closed on integration tests, examples and dependency CVEs.
- Test markers: network, browser, integration, adversarial, slow.
- SECURITY.md documents the DNS-rebinding gap in the SSRF guard rather than
  implying complete protection.

### Fixed (1.7.0) — re-audit criticals
- **Evidence entry hash now covers full provenance** (request/response headers,
  collector, note, body_path, egress) in a versioned canonical envelope. An
  audit rewrote four of those on a real package and the verifier said PASSED.
- **Suffix truncation detected.** The verifier recomputes the chain head and
  compares it to the manifest, and reconciles capture_count. Deleting the last
  capture previously verified clean.
- **Definitional merge cannot be self-granted.** is_definitional() requires an
  engine-recognised registry collector; a SAME_AS claim from the subject own
  page marked AUTHORITATIVE used to merge on its own.
- Merge policy moved to evidence-score space (MERGE_LLR_THRESHOLD in nats).
- Declared dependence classes gathered and passed in assess(); conflicts within
  a group raise; serialized in Claim.to_dict().
- Screenshot tests use the browser marker; renderer detection stats the
  executable instead of trusting importability.

### Fixed (1.8.0) — self-audit and release-audit follow-ups
- **Capture collector identity.** Every capture recorded collector="fetcher":
  _record_evidence read an attribute no production code set. Now an async-safe
  contextvars.ContextVar, wired through Engine, so concurrent collectors are
  attributed correctly. attribution-graph imports it lazily so the
  no-network-IO boundary is preserved.
- Public surfaces no longer promise calibrated probability; METHOD.md band
  table and handle-correlation sample output updated.
- README version strings point at CHANGELOG rather than being hand-maintained.
- Release workflows validate that the tag matches the packaged version.
-  states that it is collector-only and provides none of the
  evidence, policy or egress guarantees of .

### Known limitations
- Calibration not validated against labelled ground truth; bands are ordinal
- No transliteration normalization for non-Latin entity names
- Default in-memory selectivity index produces upper bounds on confidence
