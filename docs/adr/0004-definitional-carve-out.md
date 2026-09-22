# Registry assertions are definitional

**Status:** accepted · **Date:** 2026-08

## Context

Under ADR 0003, GLEIF stating that an LEI belongs to a legal name could never
resolve on its own. The system would be unable to link a company to its own LEI,
which is conservatism in the wrong direction.

## Decision

`SAME_AS` claims from `AUTHORITATIVE` sources are exempt from the group cap and
the two-group requirement. They are definitional rather than inferential: the
registry is not offering evidence about an identity, it *is* the identity.

## Consequences

Reintroduces a single-source path to a merge, deliberately and narrowly. The
distinction is between definitional and inferential sources, not trusted and
untrusted — a registry is definitional only about identifiers it issues, and
inferential about everything else.
