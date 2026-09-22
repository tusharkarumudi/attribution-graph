# Three-key opt-in for person-scoped collection

**Status:** accepted · **Date:** 2026-08

## Context

Person-attribution collectors wrap capabilities that already exist as popular
installable tools, so withholding them provides little protection. But assembling
them behind one command with automatic clustering is a materially different
artifact from the parts.

## Decision

Ship them as an optional extra requiring three independent keys: an entity type
in `entity_types_allowed`, the collector named in `persona_collectors`, and for
handle enumeration specifically a separate `allow_username_enumeration` flag.
Gated collectors are audit-logged rather than silently skipped.

## Consequences

Users retain the capability and the choice. Widening entity scope cannot
silently enable enumeration, and enabling one collector does not enable the rest.
Enumeration is separated because sweeping hundreds of sites for a bare handle
differs in kind from looking up an identifier already held — and by the scoring
model's own logic returns very little, since every hit shares one correlation
group.
