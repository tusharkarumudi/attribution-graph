"""Stage 5 filtering.

Generalization of the existing 796-domain infrastructure blacklist. Two verdicts,
not one:

    DROP   - the claim is noise; discard it
    DEMOTE - keep the edge in the graph as context, but zero its scoring weight

DEMOTE matters. Dropping shared-hosting edges outright makes an analyst reviewing
the graph believe the link was never observed. Demoting keeps it visible and
labelled as non-probative.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .model import Claim, Identifier, IdKind, Predicate

#: Identifier held by more than this many entities carries no evidential weight.
MAX_HOLDERS = 250


class Verdict(StrEnum):
    KEEP = "keep"
    DEMOTE = "demote"
    DROP = "drop"


ROLE_LOCALPARTS = frozenset({
    "admin", "administrator", "abuse", "postmaster", "hostmaster", "webmaster",
    "noreply", "no-reply", "donotreply", "support", "info", "contact", "sales",
    "billing", "help", "security", "dns", "root", "office", "mail", "legal",
    "privacy", "dpo", "gdpr", "team", "hello", "service", "notifications",
})

PRIVACY_PROXY = re.compile(
    r"whoisguard|domains?\s*by\s*proxy|privacyprotect|withheld\s*for\s*privacy|"
    r"redacted\s*for\s*privacy|contact\s*privacy|perfect\s*privacy|private\s*by\s*design|"
    r"identity\s*protect|super\s*privacy|domain\s*protection|namecheap\s*inc|"
    r"data\s*protected|gdpr\s*masked|not\s*disclosed|statutory\s*masking",
    re.I,
)

#: Registered-agent and formation-service names. Untreated, these build enormous
#: false clusters -- a single agent address links tens of thousands of unrelated
#: shells.
NOMINEE_AGENT = re.compile(
    r"\b(c\s*t\s*corporation|corporation\s*service\s*company|\bcsc\b|"
    r"registered\s*agents?\s*inc|northwest\s*registered\s*agent|incfile|legalzoom|"
    r"harvard\s*business\s*services|cogency\s*global|vistra|trident\s*trust|"
    r"company\s*secretar(?:y|ial)\s*(?:services|ltd)|nominee\s*director)\b",
    re.I,
)

#: Load the existing 796-entry infrastructure blacklist here.
SHARED_INFRA_DOMAINS: set[str] = set()

CDN_ORG = re.compile(
    r"cloudflare|akamai|fastly|amazon|aws|google\s*llc|microsoft|azure|"
    r"digitalocean|linode|ovh|hetzner|vercel|netlify|godaddy|namecheap|hostinger",
    re.I,
)


def load_blacklist(path: str) -> None:
    """Load the existing 796-domain infrastructure blacklist."""
    with open(path) as fh:
        for line in fh:
            d = line.split("#", 1)[0].strip().lower()
            if d:
                SHARED_INFRA_DOMAINS.add(d)


def evaluate(claim: Claim, holders: int) -> tuple[Verdict, str]:
    obj = claim.object
    val = obj.value if isinstance(obj, Identifier) else str(obj)

    if isinstance(obj, Identifier):
        if obj.kind is IdKind.EMAIL:
            local = val.split("@", 1)[0]
            if local in ROLE_LOCALPARTS or local.startswith("noreply"):
                return Verdict.DROP, "role account: identifies a registrar or platform, not an operator"  # noqa: E501

        if obj.kind in (IdKind.PERSON_NAME, IdKind.ORG_NAME):
            if PRIVACY_PROXY.search(val):
                return Verdict.DROP, "privacy proxy / redaction placeholder"
            if NOMINEE_AGENT.search(val):
                return Verdict.DEMOTE, "registered agent or nominee: links the agent, not the beneficial owner"  # noqa: E501
            if len(val) < 3:
                return Verdict.DROP, "name too short to discriminate"

        if obj.kind is IdKind.DOMAIN and val in SHARED_INFRA_DOMAINS:
            return Verdict.DEMOTE, "shared infrastructure blacklist"

        if obj.kind is IdKind.POSTAL_ADDRESS and NOMINEE_AGENT.search(val):
            return Verdict.DEMOTE, "mass-registration agent address"

    if claim.predicate is Predicate.REGISTRANT and CDN_ORG.search(val):
        return Verdict.DEMOTE, "hosting or registrar organization, not the registrant"

    if holders > MAX_HOLDERS:
        return Verdict.DEMOTE, f"low selectivity: {holders} holders observed"

    return Verdict.KEEP, ""
