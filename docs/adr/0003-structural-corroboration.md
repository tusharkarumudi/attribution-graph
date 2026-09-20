# Group cap below the prior

**Status:** accepted · **Date:** 2026-08

## Context

Requiring corroboration by analyst convention does not survive contact with
deadlines. Someone will act on a single strong signal, and the model will let
them.

## Decision

`GROUP_CAP` (8.0 nats) is set deliberately below `|log(PRIOR_ODDS)|` (11.5
nats), so no single inferential correlation group can cross the merge threshold
regardless of how strong the underlying observation is.

## Consequences

Corroboration becomes arithmetic rather than discipline. The cost is that
genuinely conclusive single sources are held back — which is why ADR 0004 exists.
The inequality is load-bearing: changing either constant without preserving it
silently removes the guarantee, and a test should assert it directly.
