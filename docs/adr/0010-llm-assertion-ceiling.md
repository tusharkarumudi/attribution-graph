# Reliability ceiling on machine-generated assertions

**Status:** accepted · **Date:** 2026-08

## Context

OSINT tooling increasingly uses language models to read collected material and
state conclusions about it. Such assertions are fluent, arrive in the same JSON
as observations, and have unmeasured error rates that shift with model version
and prompt phrasing the consumer never sees.

## Decision

Machine-generated assertions enter at the lowest reliability tier, are flagged
in provenance, and the entire generation occupies one correlation group.
Identifiers extracted from the underlying text by deterministic means are treated
as observations; what a model concluded *about* the text is not.

## Consequences

Such assertions can corroborate a link with independent support but cannot
establish one. Where a tool mixes model conclusions and extracted observations in
one field and the consumer cannot separate them, the whole output is treated as
inference. Revisit if measured error rates for this class become available.
