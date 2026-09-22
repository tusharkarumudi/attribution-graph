"""Case scoping and source-class enforcement.

Fail-closed by construction: the engine will not start without a case file, and
the collector loader raises on any collector declaring a denied source class.
The constraint lives in the loader rather than the runbook because a pipeline
that *can* reach a source will eventually reach it.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from .model import EntityType


class SourceClass(StrEnum):
    # permitted
    PUBLIC_REGISTRY = "public_registry"          # RDAP, GLEIF, EDGAR, CH, MCA
    PUBLIC_PROTOCOL = "public_protocol"          # DNS, CT logs, TLS, RDAP bootstrap
    SELF_PUBLISHED = "self_published"            # ads.txt, sellers.json, imprint, robots
    PLATFORM_PUBLIC_API = "platform_public_api"  # GitHub REST, Gravatar profile JSON
    OPEN_DATASET = "open_dataset"                # OpenSanctions, OffshoreLeaks, Tranco
    OWN_TELEMETRY = "own_telemetry"              # first-party abuse logs

    # denied
    DATA_BROKER = "data_broker"
    BREACH_CORPUS = "breach_corpus"
    AUTHENTICATED_SCRAPE = "authenticated_scrape"
    BIOMETRIC = "biometric"
    LOCATION_BROKER = "location_broker"


#: Hard deny list. Not configurable from the case file on purpose -- a case file
#: is an analyst artifact, and this is not an analyst-level decision.
DENIED_SOURCE_CLASSES: frozenset[SourceClass] = frozenset({
    SourceClass.DATA_BROKER,
    SourceClass.BREACH_CORPUS,
    SourceClass.AUTHENTICATED_SCRAPE,
    SourceClass.BIOMETRIC,
    SourceClass.LOCATION_BROKER,
})


class PolicyError(RuntimeError):
    pass


#: Top-level case-file keys the loader understands. Anything else is rejected.
#:
#: Silent acceptance of unknown keys is how a misspelled safety option becomes
#: a no-op: `egres:` instead of `egress:` would have loaded cleanly and run
#: direct, with the manifest recording a vantage point that was never used.
_KNOWN_KEYS = frozenset({
    "case_ref", "authorization", "contact_email", "seeds", "pivot_radius",
    "entity_types_allowed", "robots_policy", "minimize", "retention_days",
    "max_requests", "audit_path", "denied_source_classes", "persona_collectors",
    "allow_username_enumeration", "egress", "salt", "blacklist",
    "budget", "jurisdictions",
})


#: Budget fields that must be >= 1. A zero or negative value is a
#: configuration mistake, and each fails differently and badly: concurrency 0
#: builds a zero-permit semaphore and hangs; max_nodes 0 truncates the search
#: to nothing while the result still looks complete.
POSITIVE_BUDGET_KEYS = ("max_requests", "max_nodes", "max_runtime_s",
                        "concurrency", "pivot_radius")


def _positive(key: str, value):
    n = int(value)
    if n < 1:
        raise ValueError(
            f"{key} must be at least 1, got {n}. A non-positive budget is a "
            "configuration mistake, not a way to disable the limit: "
            "concurrency 0 hangs, and max_nodes 0 silently returns nothing.")
    return n


#: Keys that gate behaviour and must never be inferred from a truthy string.
STRICT_BOOL_KEYS = ("minimize", "allow_username_enumeration",
                    "allow_authenticated_scrape")


def _strict_bool(key: str, value, default: bool) -> bool:
    """A YAML boolean, or an error. Never a coercion.

    `bool("false")` is True in Python, so `allow_username_enumeration: "false"`
    ENABLED the capability the operator was explicitly disabling. A quoting
    choice must not switch on a sensitive collection mode, and the failure was
    silent: nothing in the run said the setting had been inverted.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"`{key}` must be a YAML boolean (true or false), got {value!r}. "
        "Quoted strings are not accepted: \"false\" is truthy in Python and "
        "would enable the setting it appears to disable.")


def _budget_value(raw: dict, key: str, default):
    """Read a budget field from either the top level or the `budget:` block.

    Both spellings were validated, only the nested one was read: a case file
    with `max_requests: 1` loaded cleanly and ran with 5000. A safety budget
    that is accepted and ignored is worse than an unknown key, because the
    operator reasonably believes it is enforced.

    Top level wins when both are present, and a conflict is reported rather
    than silently resolved.
    """
    top = raw.get(key)
    nested = (raw.get("budget") or {}).get(key)
    if top is not None and nested is not None and top != nested:
        raise ValueError(
            f"case file sets {key} twice with different values "
            f"(top-level {top!r}, budget.{key} {nested!r}). Pick one.")
    for candidate in (top, nested):
        if candidate is not None:
            return _positive(key, candidate) if key in POSITIVE_BUDGET_KEYS \
                else candidate
    return default



def _secure_append(path, text: str) -> None:
    """Append to a file that is created 0600, with a 0700 parent.

    O_APPEND|O_CREAT with an explicit mode: `Path.open("a")` inherits the
    ambient umask, so the first write decided the permissions and nothing
    tightened them afterwards.
    """
    import contextlib
    import os
    from pathlib import Path as _P

    p = _P(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a") as fh:
            fh.write(text)
    finally:
        # Tighten a pre-existing file too: the mode argument only applies at
        # creation, and a trail begun under a looser umask stays loose.
        with contextlib.suppress(OSError):
            p.chmod(0o600)


@dataclass
class CaseScope:
    case_ref: str
    authorization: str
    seeds: list[str]
    pivot_radius: int = 3
    entity_types_allowed: set[EntityType] = field(
        default_factory=lambda: {EntityType.COMPANY, EntityType.PERSONA}
    )
    jurisdictions: list[str] = field(default_factory=list)
    minimize: bool = True
    retention_days: int = 180
    #: Vantage points for this run. run_case() read scope.egress while CaseScope
    #: had no such field, so a case file egress block was silently discarded and
    #: every run used the direct fallback.
    egress: list = field(default_factory=list)
    max_requests: int = 5000
    max_nodes: int = 20000
    max_runtime_s: int = 1800
    contact_email: str = ""
    robots_policy: str = "record"   # respect | record | ignore
    #: Second key for person-scoped collectors. entity_types_allowed opens the
    #: first; a collector must also be named here. Two keys rather than one so
    #: that widening entity scope does not silently enable enumeration.
    persona_collectors: set[str] = field(default_factory=set)
    #: Third key, for handle enumeration specifically. Sweeping hundreds of
    #: sites for a bare handle differs in kind from looking up an identifier
    #: already held, and by the scoring model's own logic returns very little:
    #: every hit shares one correlation group.
    allow_username_enumeration: bool = False
    salt: bytes = field(default_factory=lambda: secrets.token_bytes(16))
    audit_path: Path = Path("audit.jsonl")

    # ---- loading ---------------------------------------------------------- #

    @staticmethod
    def _validate(raw: dict) -> None:
        """Reject malformed case files at load time.

        `seeds: example.com` used to load as a list of thirteen single
        characters, `max_requests: -1` was accepted, and `robots_policy: typo`
        passed through unrecognised. A safety-sensitive option that is silently
        accepted in a broken form is worse than one that does not exist.
        """
        if not isinstance(raw.get("seeds", []), list):
            raise ValueError(
                "`seeds` must be a list. A bare string is iterated character by "
                "character, which produced a seed per letter rather than an error.")

        budget = raw.get("budget") or {}
        for key in ("max_requests", "max_depth", "retention_days"):
            for holder in (raw, budget):
                if key in holder:
                    v = holder[key]
                    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                        raise ValueError(f"`{key}` must be a positive integer, got {v!r}")

        policy = raw.get("robots_policy")
        if policy is not None and policy not in ("respect", "record", "ignore"):
            raise ValueError(
                f"`robots_policy` must be respect, record or ignore, got {policy!r}")

        radius = raw.get("pivot_radius")
        if radius is not None and (not isinstance(radius, int)
                                   or isinstance(radius, bool) or radius < 0):
            raise ValueError(f"`pivot_radius` must be a non-negative integer, got {radius!r}")

        egress = raw.get("egress")
        if egress is not None and not isinstance(egress, list):
            raise ValueError("`egress` must be a list of vantage-point mappings")

    @classmethod
    def load(cls, path: str | Path) -> CaseScope:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        cls._validate(raw or {})
        if not isinstance(raw, dict):
            raise PolicyError(f"case file {path} is not a YAML mapping")
        required = ("case_ref", "authorization", "seeds")
        missing = [k for k in required if not raw.get(k)]
        if missing:
            raise PolicyError(
                f"case file missing required field(s): {', '.join(missing)}. "
                "An investigation without a recorded authorization reference is "
                "not runnable."
            )

        types = {
            EntityType(t) for t in raw.get("entity_types_allowed", ["Company", "Persona"])
        }
        salt_hex = raw.get("salt") or os.environ.get("EAE_SALT")
        # Validate every budget field, not only the ones the constructor
        # happens to read. `budget.max_nodes: 0` loaded cleanly because nothing
        # consulted it at load time -- and then silently truncated the search
        # while the result still looked complete.
        budget_block = raw.get("budget") or {}
        if budget_block and not isinstance(budget_block, dict):
            raise ValueError("`budget:` must be a mapping")
        for key in POSITIVE_BUDGET_KEYS:
            for source, where in ((raw, "top-level"), (budget_block, "budget")):
                if source.get(key) is not None:
                    try:
                        _positive(key, source[key])
                    except (TypeError, ValueError) as e:
                        raise ValueError(f"{where} {e}") from None

        unknown = set(raw) - _KNOWN_KEYS
        if unknown:
            raise ValueError(
                f"unknown case-file key(s): {', '.join(sorted(unknown))}. "
                f"Known keys: {', '.join(sorted(_KNOWN_KEYS))}. Rejected rather "
                "than ignored, because a misspelled safety option that loads "
                "cleanly is worse than one that fails.")

        return cls(
            case_ref=raw["case_ref"],
            authorization=raw["authorization"],
            seeds=list(raw["seeds"]),
            pivot_radius=int(raw.get("pivot_radius", 3)),
            entity_types_allowed=types,
            jurisdictions=list(raw.get("jurisdictions", [])),
            minimize=_strict_bool("minimize", raw.get("minimize"), True),
            retention_days=int(raw.get("retention_days", 180)),
            egress=list(raw.get("egress", []) or []),
            max_requests=int(_budget_value(raw, "max_requests", 5000)),
            max_nodes=int(raw.get("budget", {}).get("max_nodes", 20000)),
            max_runtime_s=int(raw.get("budget", {}).get("max_runtime_s", 1800)),
            contact_email=raw.get("contact_email", ""),
            robots_policy=str(raw.get("robots_policy", "record")).lower(),
            persona_collectors=set(raw.get("persona_collectors", []) or []),
            allow_username_enumeration=_strict_bool(
                "allow_username_enumeration",
                raw.get("allow_username_enumeration"), False),
            salt=bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16),
            audit_path=Path(raw.get("audit_path", "audit.jsonl")),
        )

    # ---- gates ------------------------------------------------------------ #

    def check_source_class(self, collector_name: str, sc: SourceClass) -> None:
        if sc in DENIED_SOURCE_CLASSES:
            raise PolicyError(
                f"collector '{collector_name}' declares denied source class "
                f"'{sc.value}'. Refusing to load."
            )

    def allows_entity_type(self, t: EntityType) -> bool:
        return t in self.entity_types_allowed

    def allows_persona_collector(self, name: str) -> tuple[bool, str]:
        """Both keys must be turned. Returns (allowed, reason_if_not)."""
        if not (self.allows_entity_type(EntityType.PERSONA)
                or self.allows_entity_type(EntityType.PERSON)):
            return False, ("case entity_types_allowed excludes Person and Persona; "
                           "add one to enable person-scoped collection")
        if name not in self.persona_collectors:
            return False, (f"'{name}' is not listed in the case file's "
                           "persona_collectors allowlist")
        return True, ""

    def within_radius(self, depth: int) -> bool:
        return depth <= self.pivot_radius

    @property
    def store_salt(self) -> bytes | None:
        return self.salt if self.minimize else None

    # ---- audit ------------------------------------------------------------ #

    #: Field names whose values are minimised in the audit log when the case
    #: has `minimize: true`.
    #:
    #: Minimisation used to depend on caller discipline: `audit()` serialised
    #: whatever it was handed, so any collector could write a plain email into
    #: the JSONL while graph exports were salted. A privacy invariant enforced
    #: by convention is not enforced.
    _SENSITIVE_AUDIT_FIELDS = frozenset({
        "email", "person", "person_name", "handle", "phone", "address",
        "postal_address", "registrant", "subject", "claim", "identifier",
        "target", "query", "name",
    })

    def _salted(self, value: str) -> str:
        """Salted digest, matching the graph export's minimisation."""
        import hashlib

        salt = self.store_salt or b""
        return "min:" + hashlib.sha256(salt + value.encode()).hexdigest()[:24]

    def _minimise_audit_fields(self, fields: dict) -> dict:
        """Salt-hash sensitive values at the serialisation boundary.

        Recursive, and value-aware as well as key-aware. Two gaps this closes:

        1. Only top-level strings were minimised, so a collector passing a dict
           or list -- which is the normal shape for structured results -- wrote
           its contents in the clear under `minimize: true`.
        2. Seeded identifiers were emitted raw whenever they arrived under a
           field name not on the sensitive list. The seeds are the most
           sensitive values a case contains by definition: they are the subject
           of the investigation. They are now minimised wherever they appear,
           whatever the field is called.

        A minimisation contract that depends on every caller choosing an
        approved field name is not a contract.
        """
        if not getattr(self, "minimize", False):
            return fields
        return {k: self._minimise_value(k, v) for k, v in fields.items()}

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        """Match a field name against the sensitive set, tolerantly.

        Exact matching meant `emails` leaked while `email` was protected, and
        `person_name` while `name` was protected. A privacy control defeated by
        a plural is not one, and the caller who pluralised was not being
        careless -- a list field is naturally named in the plural.
        """
        k = key.lower().strip().rstrip("s")
        return any(k == f.rstrip("s") or f.rstrip("s") in k
                   for f in cls._SENSITIVE_AUDIT_FIELDS)

    def _seed_values(self) -> set[str]:
        """Seed identifiers, in every form. No length threshold.

        This filtered on `len(v) > 3`, which exempted short identifiers
        entirely: `handle:abc` survived in the audit log, and in the graph and
        Cypher exports through the matching filter there. A three-character
        handle is not less sensitive than a four-character one.
        """
        out: set[str] = set()
        for seed in self.seeds or []:
            text = str(seed)
            out.add(text)
            if ":" in text:
                out.add(text.split(":", 1)[1])
        return {v for v in out if v}

    def _minimise_value(self, key: str, value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return "min:<nesting-depth-exceeded>"

        if isinstance(value, dict):
            return {k: self._minimise_value(k, v, depth + 1)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._minimise_value(key, v, depth + 1) for v in value]

        if not isinstance(value, str) or not value:
            return value

        if self._is_sensitive_key(key):
            return self._salted(value)

        # Value-aware: a seeded identifier is minimised wherever it surfaces,
        # including inside free text such as a URL or an error message.
        # One generator, shared with the export scrubber, so the two cannot
        # cover different sets of spellings.
        from .minimise import canonical_forms

        text = value
        replacements: dict[str, str] = {}
        for seed in self._seed_values():
            digest = self._salted(seed)
            for form in canonical_forms(seed):
                replacements[form] = digest
        for form in sorted(replacements, key=len, reverse=True):
            text = text.replace(form, replacements[form])
        return text

    def audit(self, event: str, **fields: Any) -> None:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "case_ref": self.case_ref,
            "authorization": self.authorization,
            "event": event,
            **self._minimise_audit_fields(fields),
        }
        # 0600 append. The audit trail carries the authorization value, the
        # case reference and the event history -- governance material an
        # empirical probe found readable by every local user at 0644.
        _secure_append(self.audit_path, json.dumps(line, default=str) + "\n")

    def expiry(self) -> datetime:
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(days=self.retention_days)
