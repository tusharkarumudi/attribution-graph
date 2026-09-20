# Selectivity from a corpus, not a weight table

**Status:** accepted · **Date:** 2026-08

## Context

Hand-tuned evidence weights encode the author's assumptions about the internet
at a point in time, go stale silently, and cannot explain themselves.

## Decision

Evidence weight derives from `log(1/selectivity)`, where selectivity is
estimated from observed holder counts with Laplace smoothing.

## Consequences

The model produces correct relative weights — a unique analytics ID at ~14 nats,
a CDN IP at ~0 — without anyone telling it what a CDN is. The corpus becomes the
critical dependency: a per-case index systematically overestimates uniqueness, so
confidence figures from a corpus-less run are upper bounds and every entry point
says so.
