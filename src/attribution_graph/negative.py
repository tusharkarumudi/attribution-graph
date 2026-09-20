"""Negative evidence.

Three distinct things get conflated under "we didn't find anything", and they
carry very different weight:

1. **Not looked for.** No information. Contributes nothing, but must be visible
   in the report so a reviewer does not read silence as a negative result.
2. **Looked for, absent.** A source was queried and held no matching record.
   Weak evidence against, scaled by how complete that source is.
3. **Expected, absent.** The hypothesis predicts something and it is not there.
   This is the strongest negative signal available and the one nothing in OSINT
   tooling currently models.

The third case is what makes negative evidence worth implementing. If two domains
are commonly controlled, you *expect* shared infrastructure, overlapping
registration windows, a common registrar. Their absence is not proof of anything,
but it is evidence, and a model that only ever accumulates positive evidence will
drift upward on every hypothesis it examines.

The weight of an absence depends entirely on source completeness. Companies House
is near-complete for UK companies, so a UK entity absent from it is meaningfully
unlikely to exist. OpenCorporates is broad but incomplete, so absence there means
much less. Those coverage figures are declared per source rather than assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from .model import Claim, Identifier, Predicate, Reliability


class AbsenceKind(StrEnum):
    NOT_CHECKED = "not_checked"
    CHECKED_ABSENT = "checked_absent"
    EXPECTED_ABSENT = "expected_absent"


class CoverageBasis(StrEnum):
    """Where a completeness figure came from.

    Added after an internal audit found the inconsistency: ``DEFAULT_COVERAGE``
    was corrected from 0.50 to 0.0 on the grounds that a guessed figure gives an
    absence unearned weight -- and the fix shipped alongside fourteen equally
    guessed figures (0.98, 0.95, 0.99 ...) presented as if measured.

    Both are estimates. The difference now recorded is whether anyone can check
    them.
    """

    PUBLISHED = "published"
    """The source publishes a completeness or coverage figure."""

    STATUTORY = "statutory"
    """Registration is legally mandatory in the jurisdiction, so completeness
    approaches the compliance rate. Still an estimate, but a defensible one."""

    MEASURED = "measured"
    """Measured against a labelled sample. None yet -- this is what the
    calibration study should produce."""

    ESTIMATED = "estimated"
    """An author's judgement. Usable, and should be read as ordinal. Every
    non-zero figure currently in this table is one of these."""

    UNDECLARED = "undeclared"
    """No figure. Contributes zero weight."""


@dataclass(frozen=True)
class SourceCoverage:
    """How complete a source is for its domain.

    ``completeness`` is the probability that a real entity within the source's
    stated scope actually appears in it.

    **Most of these are author estimates, not measurements.** ``basis`` records
    which. An internal audit found the inconsistency worth naming: the default
    was corrected from 0.50 to 0.0 because a guessed figure gives an absence
    unearned weight, and that fix shipped alongside a dozen equally guessed
    figures presented as though they were measured. Only the statutory and
    published ones rest on anything a reviewer can check; the rest should be
    read as ordinal until the calibration study measures them.
    """

    source: str
    scope: str
    completeness: float
    note: str = ""
    #: Where ``completeness`` came from. Defaults to ESTIMATED, because an
    #: undocumented figure is a judgement whatever it looks like.
    basis: CoverageBasis = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.basis is None:
            object.__setattr__(self, "basis", CoverageBasis.ESTIMATED)

    @property
    def is_checkable(self) -> bool:
        """Whether the figure rests on something a reviewer can verify."""
        return self.basis in (CoverageBasis.PUBLISHED, CoverageBasis.STATUTORY,
                              CoverageBasis.MEASURED)


#: Declared coverage. Deliberately conservative -- overstating completeness
#: turns a weak absence into a strong false negative.
COVERAGE: dict[str, SourceCoverage] = {
    "companies_house_uk": SourceCoverage(
        "companies_house_uk", "UK-registered companies", 0.98,
        "Statutory register; a UK company not present is very unlikely to exist.",
        basis=CoverageBasis.STATUTORY),
    "sec_edgar": SourceCoverage(
        "sec_edgar", "US public filers", 0.95,
        "Near-complete for filers; says nothing about private US companies.",
        basis=CoverageBasis.STATUTORY),
    "gleif": SourceCoverage(
        "gleif", "entities holding an LEI", 0.99,
        "Complete for LEI holders, but most companies never obtain one — "
        "absence here is close to meaningless for a general entity.",
        basis=CoverageBasis.PUBLISHED),
    # --- third-party lookup services -------------------------------------- #
    # These do not publish coverage. Presence is informative; absence is not,
    # so completeness is 0 and their negatives carry no weight.
    "reverse_publisher_id": SourceCoverage(
        "reverse_publisher_id", "domains indexed by third-party reverse lookup",
        0.0,
        "Services publish no coverage figures. A small operator is commonly "
        "absent regardless of the truth."),
    "business_directory": SourceCoverage(
        "business_directory", "self-submitted directory listings", 0.0,
        "Compiled from self-submission and purchased lists; absence means "
        "nobody submitted an entry."),
    "icij_offshore_leaks": SourceCoverage(
        "icij_offshore_leaks", "entities in the published leak corpora", 0.85,
        "Near-complete for the leaks it contains, which cover a small fraction "
        "of offshore activity. Absence rules out those specific disclosures "
        "and nothing more."),
    "cninfo_disclosure": SourceCoverage(
        "cninfo_disclosure", "Shanghai/Shenzhen listed companies", 0.97,
        "Authoritative for listed companies. Most Chinese entities are "
        "unlisted and appear only in provincial AMR registries.",
        basis=CoverageBasis.STATUTORY),
    "dmca_agent": SourceCoverage(
        "dmca_agent", "US DMCA designated-agent registrations", 0.80,
        "Complete for those who registered. Registration is optional and "
        "many operators never file."),
    "extension_store_developer": SourceCoverage(
        "extension_store_developer", "published browser extensions", 0.95,
        "Complete for listed extensions; absence only means no extension."),
    "opencorporates": SourceCoverage(
        "opencorporates", "companies in covered jurisdictions", 0.75,
        "Broad but uneven; coverage varies sharply by jurisdiction."),
    "sellers_json": SourceCoverage(
        "sellers_json", "ad-tech sellers on a given exchange", 0.90,
        "Required by the spec, but confidential entries suppress the name."),
    "rdap": SourceCoverage(
        "rdap", "registered domains", 0.99,
        "Complete for existence; redaction limits what it discloses."),
    "crtsh": SourceCoverage(
        "crtsh", "publicly logged certificates", 0.97,
        "Near-complete since CT enforcement; misses private CAs."),
    "wayback": SourceCoverage(
        "wayback", "archived web content", 0.40,
        "Highly uneven. Absence from the archive is weak evidence of anything."),
}

#: An undeclared source contributes **nothing** to an absence.
#:
#: This was 0.50, described as a coin flip, which gave every unregistered
#: source's empty result real evidential weight -- around 0.26 nats pushing a
#: score down. A coin flip is not neutrality: you cannot infer anything from an
#: absence in a source whose coverage you have not measured, and treating
#: ignorance as 50% confidence is how a lookup service with no published
#: coverage ends up arguing against a link.
#:
#: Zero forces declaration. A collector that wants its absences to count must
#: register a coverage figure that can be argued with.
DEFAULT_COVERAGE = SourceCoverage(
    "unknown", "unspecified", 0.0,
    "Coverage not declared, so an absence here carries no weight. Declare a "
    "completeness figure in COVERAGE to make this source's negatives count.")


def absence_llr(source: str, kind: AbsenceKind) -> float:
    """Log-likelihood contribution of a negative observation.

    ``P(absent | exists)`` is ``1 - completeness``; ``P(absent | not exists)``
    is ~1. So the ratio is ``log(1 - completeness)``, bounded to keep a single
    near-complete source from dominating.
    """
    if kind is AbsenceKind.NOT_CHECKED:
        return 0.0
    cov = COVERAGE.get(source, DEFAULT_COVERAGE)
    llr = math.log(max(1.0 - cov.completeness, 0.01))
    if kind is AbsenceKind.EXPECTED_ABSENT:
        # A prediction of the hypothesis failed, which is worth more than a
        # source simply not carrying the record.
        llr *= 1.5
    return max(llr, -6.0)


def absence_claim(
    subject: Identifier,
    source: str,
    kind: AbsenceKind,
    *,
    query_url: str,
    what_was_sought: str,
    observed_at: datetime | None = None,
    note: str = "",
) -> Claim:
    """A recorded negative observation, carrying its own weight.

    Emitted as ``CONTRADICTS`` so the scoring model treats it as negative
    evidence, with the effective magnitude carried in ``weight``.

    ``note`` records why the absence means what it means. This matters more for
    negatives than positives: "no record found" is read as a finding by default,
    and for a source that does not publish its coverage it is nothing of the
    sort. A source with 98% coverage and one with unknown coverage produce the
    same empty result and warrant opposite conclusions, so the distinction has
    to travel with the claim rather than live in the collector that made it.
    """
    cov = COVERAGE.get(source, DEFAULT_COVERAGE)
    llr = absence_llr(source, kind)
    # CONTRADICTS is scored as -reliability * weight * 4.0, so normalise the
    # intended magnitude into weight.
    weight = min(abs(llr) / 4.0, 1.0) if llr else 0.0

    return Claim(
        subject=subject,
        predicate=Predicate.CONTRADICTS,
        object=f"absent:{source}",
        collector=f"{source}:negative",
        source_url=query_url,
        reliability=Reliability.MODERATE,
        weight=weight,
        observed_at=observed_at or datetime.now(timezone.utc),
        correlation_group=f"absence|{source}|{subject.key}",
        raw={
            "absence_kind": kind.value,
            "sought": what_was_sought,
            "source_completeness": cov.completeness,
            "coverage_note": cov.note,
            "llr": round(llr, 3),
            "interpretation": _interpret(source, kind, cov),
            **({"note": note} if note else {}),
        },
    )


def _interpret(source: str, kind: AbsenceKind, cov: SourceCoverage) -> str:
    if kind is AbsenceKind.NOT_CHECKED:
        return f"{source} was not queried; this is not a negative result"
    strength = ("strong" if cov.completeness >= 0.95 else
                "moderate" if cov.completeness >= 0.80 else "weak")
    base = (f"queried {source} ({cov.scope}, ~{cov.completeness:.0%} complete) "
            f"and found no match — {strength} evidence against")
    if kind is AbsenceKind.EXPECTED_ABSENT:
        return base + "; the hypothesis predicted a match here"
    return base


# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #

@dataclass
class Expectation:
    """Something a hypothesis predicts should be observable."""

    description: str
    source: str
    check: str
    satisfied: bool | None = None

    @property
    def status(self) -> str:
        return {True: "confirmed", False: "not found",
                None: "not checked"}[self.satisfied]


def common_control_expectations(domain_a: str, domain_b: str) -> list[Expectation]:
    """What ought to be true if two domains share an operator.

    Used to turn a hypothesis into a checklist, so the report can show what was
    predicted, what was confirmed, and what was predicted but absent — which is
    the part that usually goes unrecorded.
    """
    return [
        Expectation(f"{domain_a} and {domain_b} share a registrar", "rdap",
                    "compare RDAP registrar entities"),
        Expectation("registration windows overlap", "rdap",
                    "compare RDAP registration events"),
        Expectation("a certificate covers both names", "crtsh",
                    "search CT for SANs covering both"),
        Expectation("both declare the same ad-tech seller", "sellers_json",
                    "compare ads.txt DIRECT records"),
        Expectation("both appear in the archive over a common period", "wayback",
                    "compare CDX coverage windows"),
    ]


def expectation_claims(
    subject: Identifier, expectations: list[Expectation], query_url: str = ""
) -> list[Claim]:
    """Emit claims for expectations that were checked and failed."""
    out: list[Claim] = []
    for e in expectations:
        if e.satisfied is False:
            out.append(absence_claim(
                subject, e.source, AbsenceKind.EXPECTED_ABSENT,
                query_url=query_url or f"expectation://{e.source}",
                what_was_sought=e.description,
            ))
    return out


def render_expectations(expectations: list[Expectation]) -> str:
    """Report block. Shows all three states so silence is never ambiguous."""
    L = ["| Expected if hypothesis holds | Source | Result |",
         "|---|---|---|"]
    for e in expectations:
        L.append(f"| {e.description} | `{e.source}` | {e.status} |")
    unchecked = [e for e in expectations if e.satisfied is None]
    if unchecked:
        L += ["", f"_{len(unchecked)} expectation(s) were not checked. "
                  "These are gaps in the investigation, not negative findings._"]
    return "\n".join(L)
