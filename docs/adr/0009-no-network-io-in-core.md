# No network I/O in the inference core

**Status:** accepted · **Date:** 2026-08

## Context

A scoring model with embedded HTTP calls cannot be audited: its output depends
on hidden network state and reproducing a score requires reproducing the
internet.

## Decision

`attribution-graph` performs no network I/O. Collectors satisfy a protocol and
live in downstream packages; selectivity counts arrive through a
`SelectivityIndex` supplied by the caller.

## Consequences

Every number in an assessment is a pure function of claims plus index counts.
Tests need no mocking. The cost is a second package for anyone who wants working
collectors, and a small amount of protocol plumbing.
