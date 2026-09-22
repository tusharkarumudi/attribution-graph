# Contributing

## Scope of this repository

This repository contains the inference core only: the evidence model, entity
resolution, filtering, scope enforcement and exporters. It performs no network
I/O by design.

**Collectors belong in downstream packages,** not here. A PR adding an HTTP
client, an API integration or a data source will be declined regardless of
quality — keeping the core I/O-free is what makes the scoring model auditable,
since every number in an assessment is a pure function of claims plus index
counts.

## What is most useful

1. **Calibration against labelled ground truth.** The single most valuable
   contribution available. The bands in `scoring.py` are principled but not
   empirically fitted. If you have a corpus with known entity labels, a
   reliability diagram would materially improve this project.
2. **Selectivity index implementations** against real corpora.
3. **Name normalization** — transliteration and romanization for non-Latin
   scripts, where recall is currently poor.
4. **Adversarial test cases.** Claim sets that produce a wrong merge are more
   valuable than ones that produce a right one.

## Changing the model

Changes to `PRIOR_ODDS`, `GROUP_CAP`, `HALF_LIFE_DAYS` or the band edges need
justification in the PR description, not just passing tests. In particular the
invariant `GROUP_CAP < |log(PRIOR_ODDS)|` is load-bearing: it is what makes
corroboration structural rather than advisory. A PR that breaks it needs to
argue for a different mechanism, not just adjust a constant.

## Development

```bash
git clone https://github.com/tusharkarumudi/attribution-graph
cd attribution-graph
pip install -e ".[dev]"
pytest -q
ruff check .
```

New scoring behaviour needs a test that would fail without it. Tests in
`tests/test_scoring.py` are written as *properties* — "correlated evidence does
not stack", "a registry assertion stands alone" — rather than assertions about
specific floats. Follow that style; it survives retuning.

## Reporting issues

Include the claim set (redacted as needed), the index counts, and the assessment
you got versus expected. A reproducer using synthetic identifiers is ideal.

## Code of conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
