# Two filter verdicts, not one

**Status:** accepted · **Date:** 2026-08

## Context

Filtering non-probative edges out of the graph makes an analyst reviewing it
believe the relationship was never observed. That is a false negative introduced
by the presentation layer.

## Decision

Filters return DROP (noise: role accounts, privacy placeholders) or DEMOTE (keep
the edge, zero its scoring weight, record the reason).

## Consequences

Reports show demoted edges in their own section with reasons. Slightly noisier
output; considerably more honest, and it survives cross-examination in a way that
silent deletion does not.
