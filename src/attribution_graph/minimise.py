"""Canonical identifier forms, and what minimisation does and does not cover.

## The contract

`minimize: true` means: **every identifier the toolkit derives is salted-hashed
in every artifact the toolkit generates, in every form the toolkit itself
produces.**

It does *not* mean the preserved source evidence is redacted. That is
deliberate and is stated here because the previous wording implied otherwise.

| Artifact | Minimised | Why |
|---|---|---|
| `investigation_graph.json`, FTM, Cypher, reports | yes | derived output |
| audit log | yes | derived record |
| `evidence/captures/*.bin` | **no** | the preserved wire bytes ARE the evidence |
| `evidence_manifest.json` URLs | **no** | must describe what was actually fetched |
| HTTP cache | **no** | a copy of the wire bytes |

A forensic capture that redacted its own bytes would no longer verify against
its digest, and the digest is the entire point. So an evidence package is
**not** made safe to share by `minimize: true`, and the deployment guide now
says so rather than implying "no raw copy alongside".

## Why one generator

This module exists because export scrubbing and audit minimisation each grew
their own notion of "the forms a value can take", and drifted. `Jane Doe`
survived as `Jane%20Doe` in one and `Jane+Doe` in the other. A contract
enforced by two independent lists is enforced by neither.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, quote_plus

#: Minimum length to scrub. Zero: every length is covered.
#:
#: This was `len(value) > 3`, which silently exempted short identifiers --
#: `handle:abc` survived in the graph, the Cypher export and the audit log. A
#: three-character handle is not less sensitive than a four-character one, and
#: a threshold nobody documented is a hole nobody knows about.
#:
#: Very short values do cause collateral substitution in free text (a two-letter
#: string appears inside many words), so the caller decides whether a value is
#: worth scrubbing -- but length is not the criterion, and nothing is exempt by
#: default.
MIN_SCRUB_LENGTH = 0


def canonical_forms(value: str) -> set[str]:
    """Every spelling of ``value`` this toolkit can itself emit.

    Percent-encoding, form encoding, case folding and separator substitution
    are all applied by our own collectors and URL builders, so all of them have
    to be scrubbed. A minimiser that matches only the literal protects the
    spelling rather than the identifier.
    """
    if not value:
        return set()

    out = {value, value.lower(), value.upper()}
    for base in list(out):
        out.add(quote(base))              # Jane%20Doe
        out.add(quote(base, safe=""))     # https%3A%2F%2F...
        out.add(quote_plus(base))         # Jane+Doe
        out.add(base.replace(" ", "_"))
        out.add(base.replace(" ", "-"))
        out.add(base.replace(" ", ""))
    return {v for v in out if len(v) >= max(1, MIN_SCRUB_LENGTH)}


def digest(value: str, salt: bytes) -> str:
    """The stable salted hash written in place of an identifier."""
    return "min:" + hashlib.blake2b(salt + value.encode(),
                                    digest_size=12).hexdigest()


def scrub(text: str, values: set[str], salt: bytes | None) -> str:
    """Replace every canonical form of every value, longest first.

    Longest-first matters: replacing a short form before a longer one that
    contains it leaves the remainder legible.
    """
    if not salt or not text:
        return text

    expanded: dict[str, str] = {}
    for value in values:
        d = digest(value, salt)
        for form in canonical_forms(value):
            expanded[form] = d

    # Token boundaries, not bare substrings. A plain replace made a company
    # suffix eat every word containing it: "Inc" turned "INCOMPLETE RESULT" into
    # "min:7a31…OMPLETE RESULT" and "incorporated_in" into "min:7a31…orporated_in",
    # corrupting the report wherever a value happened to be a common substring.
    for form in sorted(expanded, key=len, reverse=True):
        pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(form)}(?![0-9A-Za-z])")
        text = pattern.sub(expanded[form], text)
    return text
