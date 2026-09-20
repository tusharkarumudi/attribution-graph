# Separate Identifier, Claim and Entity

**Status:** accepted · **Date:** 2026-08

## Context

Attribution tooling commonly represents everything as graph nodes. A domain, an
email and "the person behind them" become the same kind of object, connected by
edges. Once written to the graph, an inference is then indistinguishable from an
observation, and every consumer downstream inherits that confusion.

## Decision

Three distinct types. `Identifier` is a directly observed string and is always
factual. `Claim` is an assertion about an identifier carrying full provenance.
`Entity` is an *inferred* cluster and is never directly observed.

## Consequences

Costs some verbosity. Buys the property that no consumer can mistake a
conclusion for a fact, and makes the resolution step explicit rather than
emergent. Exports must map three types onto FollowTheMoney's two, which is
handled in `export.py`.
