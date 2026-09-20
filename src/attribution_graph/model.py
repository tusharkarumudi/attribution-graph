"""Core data model for the Entity Attribution Engine.

Three-layer separation is load-bearing:

    Identifier  - directly observed string. Always factual.
    Claim       - an assertion about an identifier, with full provenance.
    Entity      - an *inferred* cluster of identifiers. Never observed.

Collapsing Entity into Identifier is the single most common design error in
attribution tooling: it makes an inference indistinguishable from an observation
once it is written to the graph.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #

class IdKind(StrEnum):
    DOMAIN = "domain"
    IP = "ip"
    ASN = "asn"
    EMAIL = "email"
    HANDLE = "handle"                 # platform-qualified: "github:foo"
    PERSON_NAME = "person_name"
    ORG_NAME = "org_name"
    SELLER_ID = "seller_id"           # "{adsystem}/{seller_id}"
    ANALYTICS_ID = "analytics_id"     # GA4 / GTM / AdSense / Sentry DSN
    LEI = "lei"
    CIK = "cik"
    COMPANY_NUMBER = "company_number"  # "{jurisdiction}/{number}"
    GRAVATAR_HASH = "gravatar_hash"
    SERVICE_ID = "service_id"    # Disqus/Intercom/Crisp/Sentry etc., shared across a portfolio
    PGP_FPR = "pgp_fpr"
    SSH_FPR = "ssh_fpr"
    CERT_SHA256 = "cert_sha256"
    FAVICON_MMH3 = "favicon_mmh3"
    POSTAL_ADDRESS = "postal_address"
    PHONE = "phone"
    URL = "url"


@dataclass(frozen=True)
class Identifier:
    kind: IdKind
    value: str

    def __post_init__(self) -> None:
        raw = self.value
        object.__setattr__(self, "value", self.normalize(self.kind, raw))
        # Kept off __eq__/__hash__ (frozen dataclass compares declared fields
        # only) so it never affects keying, but available for reporting.
        object.__setattr__(self, "_observed_as", raw)

    #: Kinds whose values are case-insensitive by specification. Case-folding
    #: these is what stops `G-ABC123` and `g-abc123` counting as two
    #: identifiers -- and therefore two correlation groups -- from one fact.
    CASE_INSENSITIVE = frozenset({
        IdKind.DOMAIN, IdKind.EMAIL, IdKind.HANDLE, IdKind.SELLER_ID,
        IdKind.ANALYTICS_ID, IdKind.URL, IdKind.GRAVATAR_HASH,
        IdKind.CERT_SHA256, IdKind.PGP_FPR, IdKind.SSH_FPR,
    })

    @staticmethod
    def normalize(kind: IdKind, value: str) -> str:
        """Canonicalize for keying.

        Strips zero-width and bidi control characters, folds homoglyphs and
        confusable punctuation, applies NFKC, and case-folds the kinds that are
        case-insensitive by spec.

        This is a security control, not tidiness. Without it an adversary
        publishing four cosmetic variants of one analytics ID gets four
        correlation groups from one observation, which turns a WEAK link into
        an ATTRIBUTED one. Use ``obfuscation.normalize_value`` directly when you
        need to know *what* was transformed -- Identifier discards that detail
        by design, since a key that varies with provenance is not a key.
        """
        from .obfuscation import normalize_value

        v = normalize_value(
            value,
            case_fold=kind in Identifier.CASE_INSENSITIVE,
            collapse_space=True,
        ).normalized

        if kind in (IdKind.DOMAIN, IdKind.EMAIL, IdKind.HANDLE, IdKind.SELLER_ID):
            v = v.rstrip(".")
        if kind is IdKind.LEI:
            v = v.upper()
        if kind is IdKind.CIK:
            v = v.lstrip("0").zfill(10)
        return v

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value}"

    @property
    def observed_as(self) -> str:
        """The value as written by the source, before normalization."""
        return getattr(self, "_observed_as", self.value)

    @property
    def was_obfuscated(self) -> bool:
        return self.observed_as != self.value

    def masked(self, salt: bytes) -> str:
        """Salted hash for minimized at-rest storage."""
        h = hashlib.blake2b(salt + self.key.encode(), digest_size=16).hexdigest()
        return f"{self.kind.value}:#{h}"

    def __str__(self) -> str:
        return self.key


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #

class Predicate(StrEnum):
    """Edge semantics. Half-lives are declared in scoring.HALF_LIFE_DAYS."""
    # identifier -> identifier, same-entity evidence
    SAME_AS = "same_as"
    SHARES_ANALYTICS_ID = "shares_analytics_id"
    SHARES_GRAVATAR = "shares_gravatar"
    SHARES_CERT = "shares_cert"
    SHARES_FAVICON = "shares_favicon"
    CO_HOSTED = "co_hosted"
    COMMIT_EMAIL = "commit_email"
    KEY_BINDING = "key_binding"          # PGP/SSH fingerprint -> email
    PROFILE_BINDING = "profile_binding"  # handle -> display name / email

    # identifier -> entity attribute
    REGISTRANT = "registrant"
    OWNER_DOMAIN = "owner_domain"
    MANAGER_DOMAIN = "manager_domain"
    SELLER_OF = "seller_of"
    LEGAL_NAME = "legal_name"
    INCORPORATED_IN = "incorporated_in"
    REGISTERED_ADDRESS = "registered_address"
    OFFICER_OF = "officer_of"
    BENEFICIAL_OWNER_OF = "beneficial_owner_of"
    PARENT_OF = "parent_of"
    EMPLOYED_BY = "employed_by"
    OPERATES = "operates"
    TIMEZONE_HINT = "timezone_hint"

    # negative
    CONTRADICTS = "contradicts"


class Reliability(float, Enum):
    """Source reliability multiplier, loosely Admiralty-code shaped."""
    AUTHORITATIVE = 1.00   # statutory registry, signed cert, registry API
    STRONG = 0.85          # self-declared but contractually binding (sellers.json)
    MODERATE = 0.65        # third-party aggregator, self-published imprint
    WEAK = 0.40            # heuristic inference (timezone, stylometry)
    UNCERTAIN = 0.20       # scraped free-text, OCR, machine translation


@dataclass
class Claim:
    subject: Identifier
    predicate: Predicate
    object: Identifier | str
    collector: str
    source_url: str
    reliability: Reliability = Reliability.MODERATE
    weight: float = 1.0            # 0.0 = demoted: visible in graph, non-probative
    observed_at: datetime | None = None          # when the fact was true
    retrieved_at: datetime = field(default_factory=_utcnow)
    correlation_group: str = ""
    #: Which common cause this evidence arises from, declared by the collector.
    #:
    #: Typed metadata rather than a name the scorer parses later: dependence is
    #: a semantic property of what was observed, and recovering it from a string
    #: means a collector whose label happens not to match a pattern is silently
    #: treated as independent evidence.
    dependence_class: str = ""                  # dedup unit for scoring
    raw: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.observed_at is None:
            self.observed_at = self.retrieved_at

        # Obfuscation must be captured here, at the point of observation.
        # Identifier normalizes on construction, so by the time a value reaches
        # the graph there is nothing left to detect -- and a homoglyph in a
        # registered company name is a finding, not a data-quality note.
        self._record_obfuscation()

        if not self.correlation_group:
            # Default: one group per (collector, source_url). Collectors that
            # emit many claims from one page MUST set this explicitly.
            self.correlation_group = f"{self.collector}|{self.source_url}"

    def _record_obfuscation(self) -> None:
        from .obfuscation import normalize_value

        found: dict[str, Any] = {}
        for role, raw in (("subject", self.raw.get("_raw_subject")),
                          ("object", self.raw.get("_raw_object"))):
            if not isinstance(raw, str):
                continue
            r = normalize_value(raw, case_fold=False, collapse_space=True)
            if r.findings:
                found[role] = r.to_dict()
        if found:
            self.raw["obfuscation"] = found

    @property
    def obfuscation(self) -> dict[str, Any]:
        return self.raw.get("obfuscation", {})

    @property
    def deliberate_obfuscation(self) -> bool:
        return any(v.get("deliberate") for v in self.obfuscation.values())

    @property
    def object_key(self) -> str:
        return self.object.key if isinstance(self.object, Identifier) else str(self.object)

    def to_dict(self, salt: bytes | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "subject": self.subject.masked(salt) if salt else self.subject.key,
            "predicate": self.predicate.value,
            "object": (
                self.object.masked(salt) if (salt and isinstance(self.object, Identifier))
                else self.object_key
            ),
            "collector": self.collector,
            "source_url": self.source_url,
            "reliability": float(self.reliability),
            "weight": self.weight,
            "observed_at": self.observed_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "correlation_group": self.correlation_group,
            **({"dependence_class": self.dependence_class}
               if self.dependence_class else {}),
        }
        if not salt:
            d["raw"] = self.raw
        return d


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #

class EntityType(StrEnum):
    PERSON = "Person"
    COMPANY = "Company"
    PERSONA = "Persona"      # an online identity not yet tied to a natural person
    UNKNOWN = "Unknown"


@dataclass
class Entity:
    type: EntityType
    identifiers: set[Identifier] = field(default_factory=set)
    labels: dict[str, float] = field(default_factory=dict)   # candidate name -> weight
    attributes: dict[str, Any] = field(default_factory=dict)
    log_odds: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def best_label(self) -> str:
        if not self.labels:
            return next(iter(sorted(i.key for i in self.identifiers)), self.id[:8])
        return max(self.labels.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #

@dataclass
class AttributionGraph:
    case_ref: str
    identifiers: dict[str, Identifier] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    entities: dict[str, Entity] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def add_identifier(self, ident: Identifier) -> Identifier:
        return self.identifiers.setdefault(ident.key, ident)

    def add_claim(self, claim: Claim) -> None:
        self.add_identifier(claim.subject)
        if isinstance(claim.object, Identifier):
            self.add_identifier(claim.object)
        self.claims.append(claim)

    def claims_for(self, ident: Identifier) -> Iterable[Claim]:
        for c in self.claims:
            if c.subject.key == ident.key or c.object_key == ident.key:
                yield c

    def holders(self, ident: Identifier) -> int:
        """Distinct subjects observed carrying this identifier value.

        Feeds the selectivity term in scoring. Backed by the in-memory graph
        here; swap for a persistent corpus counter in production, where the
        counts should come from the full historical observation store rather
        than the current case.
        """
        subs = {
            c.subject.key for c in self.claims
            if c.object_key == ident.key
        }
        subs.add(ident.key)
        return len(subs)

    def to_json(self, salt: bytes | None = None) -> str:
        return json.dumps(
            {
                "case_ref": self.case_ref,
                "created_at": self.created_at.isoformat(),
                "identifiers": [
                    (i.masked(salt) if salt else i.key) for i in self.identifiers.values()
                ],
                "claims": [c.to_dict(salt) for c in self.claims],
                "entities": [
                    {
                        "id": e.id,
                        "type": e.type.value,
                        "label": e.best_label if not salt else e.id[:8],
                        "identifiers": [
                            (i.masked(salt) if salt else i.key) for i in e.identifiers
                        ],
                        "log_odds": round(e.log_odds, 4),
                        "attributes": e.attributes,
                    }
                    for e in self.entities.values()
                ],
            },
            indent=2,
            default=str,
        )
