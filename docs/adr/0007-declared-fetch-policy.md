# Fetch policy declared, not enforced

**Status:** accepted · **Date:** 2026-08

## Context

robots.txt is routinely bypassed in practice via headless browsers and
residential proxies. A library that silently enforced it would be enforcing a
norm the ecosystem abandoned; one that silently ignored it would leave a run
unable to describe its own conduct.

## Decision

`robots_policy` is a case-file field — `respect`, `record` (default), or
`ignore` — and whichever is chosen is written into the evidence manifest and the
declaration draft.

## Consequences

The question in review is never whether the tool obeyed robots.txt but whether
the operator can state what their policy was. In practice it rarely binds: nearly
every source here is published for machine consumption.
