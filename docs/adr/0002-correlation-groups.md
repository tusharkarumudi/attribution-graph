# Correlation groups as the unit of independence

**Status:** accepted · **Date:** 2026-08

## Context

Four hundred commits from one repository, fifty SANs on one certificate, and
thirty profiles from one username enumeration all present as many observations.
Each is one. Any additive scoring drives the posterior to certainty on a single
underlying fact.

## Decision

Every claim declares a `correlation_group`. Within a group, evidence aggregates
as `max + log(1+n)` rather than summing.

## Consequences

This is the highest-leverage decision a collector author makes and the easiest
to get wrong, so it is documented in CONTRIBUTING, the reference collector, and
the PR review bar. A collector that emits N claims from one fact in N groups
produces confident wrong answers, and no other mechanism catches it.
