# An evidence model for attribution investigations

**The model is not calibrated.** Bands order candidate links by evidence
strength; they are not measured frequencies. See CALIBRATION.md for the study
that would change that.

**Status:** methodology note accompanying the `attribution-graph` reference
implementation. Version 2.0.0.

---

## 1. Problem

Attribution investigations — determining which real-world entity operates a set
of observed online assets — are usually tooled as graph traversal. An analyst
seeds a domain, pivots through shared infrastructure, and reads the resulting
component as a finding. This works for infrastructure mapping and fails for
entity attribution, for a reason that is structural rather than incidental:

**Infrastructure identifiers are primary keys. Entity identifiers are evidence.**

An IP address either is or is not the IP a domain resolves to. A person either
is or is not the operator behind an account, and no observation settles it —
only an accumulation of observations, each of which is individually consistent
with the null hypothesis.

Three failure modes follow from treating the second case like the first.

**Unbounded transitivity.** Traversal has no stopping rule. Domain A shares a
Cloudflare IP with B; B shares a registrar contact with C; C shares an analytics
ID with D. The connected component grows until it contains most of the internet,
and every edge in it was individually true.

**Correlated evidence counted as independent.** Four hundred commits from one
repository, fifty subdomains on one host, and thirty profile pages found by one
username enumerator all present as many observations. They are one observation
each. Any additive scoring scheme drives the posterior to certainty on a single
underlying fact.

**Uniform edge weight.** A shared Google Analytics measurement ID and a shared
Cloudflare IP are the same edge in a graph database. They differ by roughly six
orders of magnitude in what they imply.

The consequences are asymmetric in a way that matters. A false positive in
infrastructure mapping wastes a lookup. A false positive in entity attribution
is a false accusation against a real person or company, and it is typically
delivered with the unearned authority of a machine-generated graph.

## 2. Model

Each linking observation contributes a log-likelihood ratio toward the
hypothesis that two identifiers denote the same entity:

```
llr(c) = reliability(c) · weight(c) · decay(predicate(c), age(c)) · log(1 / selectivity(o))
```

The posterior over a set of claims `C` is:

```
log_odds = log(prior_odds) + Σ_{g ∈ groups(C)} min(cap(g), aggregate(g))
p_same   = σ(log_odds)
```

### 2.1 Selectivity carries the weight

`selectivity(o)` is the probability that two unrelated entities both carry
identifier value `o`. It is estimated from an observation corpus with Laplace
smoothing:

```
selectivity(o) = (holders(o) − 1 + α) / (N + α·k)
```

where `holders(o)` is the count of distinct entities observed carrying `o` and
`N` is corpus size. This single term replaces what is otherwise a hand-tuned
weight table, and it produces the right behaviour without anyone encoding it:

| Identifier | Typical holders | llr |
|---|---:|---:|
| GA4 measurement ID, single site | 1 | ≈ +13.8 |
| Unusual legal name | 1–3 | ≈ +13 |
| ads.txt seller ID | 1 | ≈ +13.8 |
| Common legal name ("Media Ltd") | 10³ | ≈ +6.9 |
| Shared CDN IP | 10⁵–10⁶ | ≈ 0 |
| Common personal name | 10⁵+ | ≈ 0 |

Nothing in the model needs to know that Cloudflare is a CDN. The corpus knows.

This is also where the model is most easily wrong. Selectivity computed over a
single investigation systematically overestimates uniqueness, because the case
graph has only seen what the case has seen. The reference implementation makes
this explicit: `InMemoryIndex` is documented as producing upper bounds, and
`SelectivityIndex` is a protocol precisely so that a corpus-backed
implementation can be substituted. **The model is only as calibrated as its
counts.**

### 2.2 Correlation groups prevent stacking

Every claim declares a `correlation_group`: the unit of genuinely independent
observation. Within a group, claims aggregate as

```
aggregate(g) = max(llr) + log(1 + |{c ∈ g : llr(c) > 0}| − 1)
```

rather than summing. Four hundred commits from one repository contribute
`max + log(400)` ≈ one strong observation plus a modest multiplicity bonus,
not four hundred.

Choosing the group correctly is the highest-leverage decision a collector
author makes, and it is where the model is easiest to defeat by accident. All
SANs on one certificate are one group. All accounts found by enumerating one
username are one group — the observation is *"this person reuses a handle"*,
made once. All hostnames on one IP are one group.

### 2.3 Group cap and prior

`GROUP_CAP` is set to 8.0 nats and the prior to 10⁻⁵ (≈ −11.5 nats). The
inequality is deliberate:

```
GROUP_CAP < |log(prior_odds)|
```

No single inferential correlation group can cross the merge threshold on its
own, structurally, regardless of how strong the underlying observation is.
Corroboration is enforced by arithmetic rather than by analyst discipline.

### 2.4 Definitional carve-out

Requiring corroboration everywhere makes the system unable to accept that a
registry's own identity assertion is true. GLEIF stating that LEI
`5493001KJTIIGC8Y1R12` belongs to a given legal name is not evidence *about* an
identity; it is the identity. Such claims — `SAME_AS` from an authoritative
registry — are exempt from `GROUP_CAP` and from the two-group requirement.

The distinction is between *definitional* and *inferential* sources, not between
trusted and untrusted ones. A registry is definitional about identifiers it
issues and merely inferential about everything else.

### 2.5 Temporal decay

Evidence half-lives are per-predicate. A 2016 WHOIS registrant record is not
2026 evidence about a current operator; an incorporation record does not decay
at all.

| Predicate | Half-life (days) |
|---|---:|
| `INCORPORATED_IN`, `LEGAL_NAME`, `SAME_AS` | ∞ |
| `OFFICER_OF`, `BENEFICIAL_OWNER_OF` | 1825 |
| `COMMIT_EMAIL`, `SELLER_OF` | 1095 |
| `REGISTRANT` | 730 |
| `CO_HOSTED` | 180 |

### 2.6 Machine-generated assertions

A newer class of source requires its own treatment: tooling that uses a language
model to read collected material and state conclusions about it. Robin, for
dark-web investigation, is a representative example, and the pattern is
spreading through OSINT tooling quickly.

An LLM asserting that two handles belong to one actor is neither an observation
nor a registry statement. It is inference over text, produced by a process whose
error rate on this task is unmeasured, unstable across model versions, and
sensitive to prompt phrasing that the downstream consumer never sees. Treating
it as evidence of the same kind as a certificate or a filing is a category
error, and one that is easy to make because the output is fluent and arrives in
the same JSON as everything else.

Three rules follow.

**A reliability ceiling.** Machine-generated assertions enter at the lowest
reliability tier and are flagged in provenance. They can raise a link that
already has independent support; they cannot establish one.

**One generation is one correlation group.** A summary naming eight identifiers
is one act of inference, not eight observations. Splitting it would let a single
model output accumulate to certainty — the failure this model exists to prevent,
arriving through a new door.

**The distinction between reading and concluding.** Identifiers extracted from
the underlying text by deterministic means — a fingerprint matched by a regex, an
address matched by a pattern — are observations, because they were on the page.
What the model *concluded about* the page is not. Tooling that mixes the two in
one output field forces the consumer to separate them, and if it cannot, the
whole output must be treated as inference.

This is a live gap in the literature. Record linkage predates the problem, and
the calibration literature assumes a scored classifier rather than a generative
one. Absent measured error rates for this class of assertion, a floor is the
defensible position.

### 2.7 Corroborative-only predicates

Some evidence can raise a score that already has independent support but must
never establish a link alone: timezone inference from activity histograms,
co-hosting, favicon reuse. These are excluded from the independent-group count.
A cluster supported only by corroborative evidence reports as WEAK no matter how
much of it accumulates.

### 2.9 Conditional dependence between groups

Correlation groups stop evidence stacking *within* a source. Summing across
groups then assumes they are conditionally independent given the hypothesis, and
that assumption frequently fails.

An operator installing one stack emits, in a single action:

    an analytics tag
    a favicon
    a theme fingerprint
    response headers

Four correlation groups. One decision. Measured before the correction existed,
three such artifacts on one page scored at the top of the scale — the model
reading one configuration choice as three independent confirmations. That is the
same failure §2.2 was built to prevent, arriving one level up, and it biases
toward overconfidence on exactly the evidence an analyst is most likely to have.

Groups are therefore assigned a **dependence class** — site setup, monetization
setup, DNS/hosting, registration, corporate filing, code publication, persona
naming. Within a class the strongest group contributes in full and the rest at a
discount; across classes, full addition. The corroboration requirement counts
distinct classes rather than raw groups, so one decision can no longer satisfy
a two-group rule.

Two things this does not do. The discount factors are **unfitted**, like every
constant here. And the class boundaries are judgement calls: whether enrolling
with an ad system is the same decision as installing analytics depends on the
operator. The correction bounds the worst case without producing a correct
magnitude, and a calibration study is the only thing that can supply one.

Unmatched groups default to *independent*, which applies no discount. That is
deliberate: a wrong discount is harder to notice than a missing one, and
defaulting to a class would silently suppress genuinely independent evidence.

### 2.8 Negative evidence and must-not-link

Contradictions contribute negative LLR. Separately, certain relations are hard
constraints rather than weak evidence: `OFFICER_OF`, `BENEFICIAL_OWNER_OF`, and
`PARENT_OF` connect *distinct* entities, and merging across them is blocked
outright. A director is not the company they direct — a merge that graph
traversal makes almost inevitable.

## 3. Resolution

Pairwise assessments feed a weighted union-find, processed **highest-confidence
first**. Union-find is order-dependent and a premature weak merge is
unrecoverable: it silently contaminates every subsequent decision. Confidence
ordering bounds that damage. Type conflicts (Person ↔ Company) and must-not-link
constraints are checked before each union.

## 4. Filtering: DROP versus DEMOTE

Two verdicts, not one.

- **DROP** — the claim is noise (role accounts, privacy-proxy placeholders).
- **DEMOTE** — the edge stays in the graph with zero scoring weight.

The distinction matters for review. Dropping shared-hosting edges outright makes
an analyst reading the graph believe the relationship was never observed.
Demoting keeps it visible and labelled non-probative, which is the honest
representation and the one that survives cross-examination.

## 5. Reporting

Bands describe evidence strength, and findings below MODERATE_EVIDENCE are
labelled leads rather than conclusions. Every claim carries collector, source
URL, retrieval timestamp, and correlation group; cached response bodies are the
evidentiary artifact and the graph is derived from them.

| p(same) | Band | Language |
|---|---|---|
| ≥ 0.95 | STRONG_EVIDENCE | strongly supported by the evidence |
| 0.80–0.95 | MODERATE_EVIDENCE | moderately supported by the evidence |
| 0.55–0.80 | LIMITED_EVIDENCE | weakly supported by the evidence |
| 0.20–0.55 | WEAK | unlikely |
| < 0.20 | UNSUPPORTED | very unlikely |

## 6. Scope as a runtime constraint

Attribution tooling generalizes across targets by construction: the machinery
that identifies a scraper operator identifies anyone. The reference
implementation therefore treats scope as executable rather than documentary — a
mandatory authorization reference, a hard pivot-radius cap from authorized
seeds, entity-type restrictions enforced at collector dispatch, a source-class
deny list enforced at load time, and an append-only audit log. A pipeline that
*can* reach a source eventually will, so the constraint belongs in the loader.

## 7. Limitations

- Selectivity counts from a single case are upper bounds on confidence (§2.1).
- No transliteration or romanization normalization; recall on non-Latin entity
  names is materially degraded. OpenSanctions' `rigour` is the natural remedy.
- The model assumes conditional independence across correlation groups. Groups
  are usually but not always independent — an operator's registrar choice and
  hosting choice may share a common cause.
- Calibration has not been validated against a labelled ground-truth corpus.
  The bands are principled but not empirically fitted, and reported
  probabilities should be read as ordinal until that work is done. This is the
  most important open item.

## 8. Relation to existing work

Collection frameworks (SpiderFoot, recon-ng, Amass, theHarvester) solve
acquisition and treat correlation heuristically. Knowledge platforms (OpenCTI,
Maltego) store and visualize graphs where confidence is an analyst-entered
field. Screening systems (OpenSanctions/yente, FollowTheMoney) score
query-against-list similarity with tunable algorithms, answering "is this entity
on a list" rather than "do these forty identifiers resolve to one operator."

This library occupies the gap between them: it consumes claims from any of the
first group, produces entities consumable by the second and third, and supplies
the explicit, auditable inference step that none of them performs.

## Citation

See `CITATION.cff`.
