# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [2.0.9] - 2026-10-30

**No change to the importable code.** Every `.py` file in this wheel is
byte-identical to the 2.0.8 wheel; the local build was verified against
the one PyPI was serving, file by file, before deciding to cut this at all.

It exists for the sdist. Tests ship in the source distribution, and 2.0.8's
copy of `test_screenshot.py` fails five times on a machine with no Chromium
instead of skipping -- so anyone building from source, a distribution packager
or a `--no-binary` CI job, met five failures caused by their environment rather
than by this package. PyPI does not accept a re-upload, so correcting a shipped
test file means a version number.

- The five browser-marked screenshot tests now **skip** rather than fail when
  the machine has no renderer. The `browser` marker is what keeps them out of
  the default suite; it does not make "there is no Chromium here" a defect in
  the code under test, and five failures from one missing browser bury any real
  one in the same output. Verified both ways: they skip without a renderer and
  all five pass against a real Chromium.
- `test_parent_body_hash_is_caller_declared_not_verified` is no longer
  browser-marked. Every assertion in it holds whether a renderer ran or not --
  that is its point, since the field is copied from the caller either way --
  and a test that documents a known limitation is the last one that should
  disappear on a machine without a browser.

## [2.0.8] - 2026-10-27

Everything else in this release was published as 2.0.7. These two changes
landed after that tag, so they need a number of their own -- PyPI does not
allow re-uploading a version, and quietly shipping different code under the
same number is how a bug report becomes unreproducible.

- **The executive summary no longer says "a site with no advertising
  declarations publishes no payee" when the declarations were read.** It said
  that whenever nothing reached a reportable band, including when the file had
  been parsed and every link was merely single-source -- directly above a
  network map showing a payee. It now distinguishes "nothing was collected at
  all" from "collected, but no link reached a band", and says what would raise
  one: a second independent source.
- `CaseScope.load` imports PyYAML lazily. A module-scope import for one
  classmethod made the whole package unimportable wherever PyYAML is absent,
  which includes a chat sandbox that cannot install anything. A CaseScope
  built in Python needs no YAML at all.

## [2.0.7] - 2026-10-27

### Removed -- code nothing called

A reachability census over all four repositories (`scripts/find_unreachable.py`
in attribution-suite, now run by `VERIFY.sh`) found fourteen definitions that
nothing names. The dangerous ones were not the dead accessors:

- **`_cluster_types_compatible` was a second, divergent copy of a live rule.**
  The person/company merge check runs inline in `resolve`, keyed on
  `TYPE_BY_KIND`. This copy hardcoded its own kind list, so the two would drift
  -- and a divergent duplicate of a correctness rule is worse than no
  duplicate, because the dead one is the one someone eventually "fixes".
- `_UnreachableBudgetError` and the comment above it were left from the
  import-time exception tuple that `_is_budget_exhaustion` replaced.
- `log_sum_within_group` was the superseded within-group rule, kept "for
  reference" beside `sub_additive`.
- `AttributionGraph.claims_for` had no caller.

### Wired up -- where the absence of a caller WAS the defect

- **`FetchDecision.manifest_entry` was written for the evidence manifest and
  never called.** The manifest recorded a list of disallowed URLs as bare
  strings, which drops the only distinction that matters when reading it back:
  a URL the site disallows and a URL the machine could not reach look
  identical. `PolicyEngine.summary` now emits the structured entry, with the
  directive and the reason, alongside the old list.
- **`Claim.deliberate_obfuscation` had no reader.** A homoglyph or zero-width
  character in an identifier -- which nobody applies by accident, and which the
  code's own comment calls "evidence in its own right" -- reached a subsection
  an analyst might scroll past and never the summary that gets quoted. New
  `deliberately_obfuscated()` prefers the stored per-claim verdict, falls back
  to recomputing, and feeds the executive summary. It also removed a `_ =
  DELIBERATE` no-op that was papering over the unused import.

### Report

- The executive summary carries the egress conditions: a proxy that failed
  open, TLS terminated by an intercepting proxy, and the vantage point when one
  was established. A capture taken through an intercepting proxy proves what
  the proxy served, which a reader relying on the report needs to know.

- **The report now answers the question whoever reads it actually has.** Three
  sections were added, in front of the analyst-facing detail rather than
  instead of it:
  - **Executive summary** -- subject, what the evidence supports, how strong,
    and every condition that changes the reading (INCOMPLETE, selective
    serving, hosts unreachable from this machine, demoted claims, a gravatar
    binding). It restates the governing caveat, that bands are evidence
    strength and the model is not calibrated, because a summary is the part
    that gets quoted and the part that travels without its footnotes. A run
    that found nothing says so and says plainly that it is not a failure.
  - **Network map** -- a `mermaid` `flowchart LR` of the pivots. Built from the
    assessed pairs and not the raw claims, so the picture cannot imply a link
    the text declines to make; the node's shape carries its kind, the edge
    label carries the band, and the fence carries the line that a band is not
    a probability, because a diagram outlives the paragraph under it. Bounded
    at 40 edges -- a map nobody can read is not evidence of anything -- and
    written out as `network_map.mmd` as well.
  - **Indicators** -- the observables as a table, one row each, with the
    collector that produced it and the timestamp of first retrieval. Rows
    whose claims were demoted are **kept** and marked: an identifier planted
    to manufacture a link is a fact about whoever planted it. Self-published
    values are marked too, since a value and its own assertion of provenance
    are one source.
- The HTML report carries all three and still loads nothing external: the
  diagram ships as source in a `<pre>`, not as a CDN script, because the
  report is meant to survive being emailed to someone with no network.
- Minimisation reaches the new sections, which was worth a test of its own --
  three sections that name identifiers were added to a document that has a
  mode promising none are shown.
- Mermaid labels bypass the markdown escaper. Inside a fence, `org\_name`
  renders as the backslash, so `_mermaid_label` escapes for the diagram
  instead and drops the kind prefix the node shape already states.

- **A robots.txt that failed because of THIS machine no longer stops the
  crawl.** RFC 9309 §2.3.1.4 requires complete disallow for "server or network
  errors" affecting robots.txt — an origin that answered 5xx, or a path to it
  that failed. It does not address the crawler's own connectivity, and the
  rationale does not reach it: a request that never leaves this machine
  disregards no published rule and places no load on the origin. One failed
  robots.txt in a sandbox was suppressing 33 retrievals and reporting them as
  the site's policy. 5xx from the origin is unchanged and still disallows.
- A robots.txt that could not be fetched **because of this machine** -- the
  host did not resolve, a proxy refused the tunnel, there was no route -- is
  no longer reported as the site's policy. RFC 9309's complete-disallow rule
  is for a server that answered 5xx; a local network condition is not a rule
  published by anyone. The outcome stays conservative and the reason now says
  which it was, because a sandbox with an egress allowlist produced 34
  "fetch policy" blocks per run and sent analysts looking for a block the
  site had never applied.

## [2.0.6] - 2026-10-21

- `Engine.run` takes an optional `before_resolve` hook, which runs after
  collection and before scoring with the graph in hand. That window is the
  only place an adversarial check can act: scoring consumes claim weights, so
  an identifier demoted afterwards has already counted in full. It is a hook
  rather than a fixed step because the checks need a network fetcher, which
  this package does not have and must not acquire.

## [2.0.5] - 2026-10-20

- Test fixtures, examples and changelog entries no longer carry the names,
  domains, registry identifiers or account numbers of real third parties.
  They were pasted in from live runs because a real case is the most
  convenient realistic fixture there is. Replaced with fictional equivalents
  that preserve what each test is about: token length and letter case for
  opaque identifiers, word structure and legal-form suffixes for company
  names.
- Install instructions use a virtualenv, and name the PEP 668 refusal
  (`error: externally-managed-environment`) that stops a system-wide
  `pip install` on Ubuntu, Debian, Fedora and Homebrew Python. The README's
  own first line was a command that cannot work on a current Ubuntu.

## [2.0.4] - 2026-09-27

- README points first-time readers at the suite.

## [2.0.3] - 2026-09-24

- Resolved entities carry the DIRECT/RESELLER label of the accounts that
  reached them, and an entity reached ONLY through RESELLER declarations says
  so. `openx.com,900000001,RESELLER` under a publisher's `## Partner Network ##` block
  is OpenX reselling Partner Network's inventory; its legal entity was listed flat
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
