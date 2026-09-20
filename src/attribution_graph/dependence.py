"""Conditional dependence between correlation groups.

## The flaw this addresses

Correlation groups stop evidence stacking *within* one source: four hundred
commits from one repository count once. But the model then sums log-likelihood
ratios *across* groups, and that step assumes the groups are conditionally
independent given the hypothesis. They frequently are not.

An operator installing WordPress with a monetisation plugin produces, in one
action:

    an ads.txt seller record
    an analytics tag
    a favicon
    a theme fingerprint

Four correlation groups. One decision. Measured before this module existed,
three such artifacts on a single page scored ``STRONG_EVIDENCE`` at the top of the scale — the
model reading one configuration choice as three independent confirmations.

That is the same failure the correlation-group mechanism was built to prevent,
arriving one level up. And it biases in the dangerous direction: toward
overconfidence, on exactly the evidence an analyst is most likely to have.

## What this does, and what it cannot do

Groups are assigned a **dependence class**. Within a class, evidence aggregates
sub-additively — the strongest group contributes fully and the rest at a
discount — because they share a common cause. Across classes, the independence
assumption is more defensible and full addition applies.

This is a **structural correction, not a measured one.** The discount factors
are principled but unfitted, like every other constant in the model. What the
module buys is that a common cause can no longer be counted several times over;
what it does not buy is a correct magnitude. A calibration study over real cases
is the only thing that can supply that, and until one exists these figures
should be read as ordinal.

## Why classes rather than a covariance matrix

A full pairwise dependence structure would need an estimate for every pair of
evidence kinds, which is a large parameter space with no data behind it. Classes
encode the qualitative claim actually being made — "these arise from one
decision" — with one parameter each, which is the most that can be justified
without measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum


class DependenceClass(StrEnum):
    """Groups sharing a common cause. One class, one underlying decision."""

    SITE_SETUP = "site_setup"
    """Artifacts of configuring one web property: analytics tags, tag managers,
    favicons, theme and framework fingerprints, response headers. An operator
    who installs a stack emits all of these at once."""

    MONETIZATION_SETUP = "monetization_setup"
    """Artifacts of enrolling one property with ad systems: ads.txt records,
    app-ads.txt records, sellers.json reciprocity, publisher account IDs.
    One enrolment produces the whole set."""

    DNS_HOSTING = "dns_hosting"
    """Artifacts of one hosting arrangement: A/AAAA records, nameservers, mail
    exchangers, TLS certificate SANs, reverse-DNS. Choosing a host or a CDN
    determines all of them."""

    REGISTRATION = "registration"
    """Artifacts of one domain registration: registrant, admin and technical
    contacts, registrar, creation date. All entered in one form."""

    CORPORATE_FILING = "corporate_filing"
    """Artifacts of one filing: officers, registered address, company number,
    LEI, share structure. One submission to one registry."""

    CODE_PUBLICATION = "code_publication"
    """Artifacts of publishing under one account: repositories, package
    registries, commit emails, extension and app listings."""

    PERSONA_NAMING = "persona_naming"
    """Artifacts of one naming habit: handle roots across platforms, display
    names, name-derived email locals. One choice, repeated."""

    INDEPENDENT = "independent"
    """Declared independent of every other class. Full addition applies.

    This must be *asserted* by a collector, never inferred from a name."""

    UNKNOWN = "unknown"
    """Dependence not declared.

    Discounted conservatively rather than treated as independent. This was
    ``INDEPENDENT`` on the argument that a wrong discount is harder to notice
    than a missing one; an external audit made the better point that for a model
    whose dominant failure mode is overconfidence, unrecognised evidence should
    fail conservative.

    A collector that wants its evidence to aggregate fully declares
    ``dependence_class = DependenceClass.INDEPENDENT`` and takes responsibility
    for the claim."""


#: Discount applied to each group beyond the strongest in a class.
#:
#: 0.0 would collapse the class to a single group, which is too aggressive: a
#: second artifact of the same decision does carry *some* information, because
#: an operator could have configured the property differently. 1.0 is the
#: uncorrected model. These sit between, and are **unfitted** — chosen to be
#: conservative where the common cause is tightest.
#:
#: Interpret them as: how much of a second artifact's evidence survives once you
#: know it came from the same decision as the first.
WITHIN_CLASS_DISCOUNT: dict[DependenceClass, float] = {
    DependenceClass.SITE_SETUP: 0.25,
    DependenceClass.MONETIZATION_SETUP: 0.30,
    DependenceClass.DNS_HOSTING: 0.20,
    DependenceClass.REGISTRATION: 0.15,
    DependenceClass.CORPORATE_FILING: 0.25,
    DependenceClass.CODE_PUBLICATION: 0.40,
    DependenceClass.PERSONA_NAMING: 0.20,
    DependenceClass.INDEPENDENT: 1.00,
    # Undeclared evidence is discounted at the rate of the tightest known
    # common cause. Conservative by design: the cost of over-discounting is a
    # missed lead, the cost of under-discounting is a fabricated attribution.
    DependenceClass.UNKNOWN: 0.20,
}

#: Collector and group-label patterns mapping to a dependence class.
#: Matched against the correlation-group label, then the collector name.
_PATTERNS: list[tuple[re.Pattern, DependenceClass]] = [
    (re.compile(r"analytics|gtm|favicon|theme|header|artifacts\|.*\|(ga4|ua|gtm|"
                r"fbpixel|hotjar|sentry|mixpanel|clarity|yandex)", re.I),
     DependenceClass.SITE_SETUP),
    (re.compile(r"ads_txt|app_ads|sellers_json|seller|adsense|publisher", re.I),
     DependenceClass.MONETIZATION_SETUP),
    (re.compile(r"dns|rdap_ns|crtsh|cert|internetdb|pdns|mx|co_hosted|ip", re.I),
     DependenceClass.DNS_HOSTING),
    (re.compile(r"rdap|whois|registrant|registrar", re.I),
     DependenceClass.REGISTRATION),
    (re.compile(r"gleif|edgar|companies_house|opencorporates|cninfo|officer|"
                r"lei|acris|filing", re.I),
     DependenceClass.CORPORATE_FILING),
    (re.compile(r"git|github|gitlab|npm|pypi|crates|package|extension|app_store",
                re.I),
     DependenceClass.CODE_PUBLICATION),
    (re.compile(r"handle|persona|nickname|wp_users|display_name", re.I),
     DependenceClass.PERSONA_NAMING),
]


def classify_group(correlation_group: str, collector: str = "",
                   declared: DependenceClass | None = None) -> DependenceClass:
    """Which dependence class a correlation group belongs to.

    ``declared`` is the class the claim carried, and it always wins. Dependence
    is a semantic property of what a collector observed, so it belongs in typed
    metadata rather than being recovered from a string afterwards: a collector
    whose group label happens not to contain "analytics" is not thereby
    independent of one that does.

    Name matching remains as a fallback for claims that predate the field.
    Anything it cannot place is ``UNKNOWN`` and discounted, **not** assumed
    independent. That default was ``INDEPENDENT`` on the argument that a wrong
    discount is harder to notice than a missing one; an external audit made the
    better point that for a model whose dominant failure mode is overconfidence,
    unrecognised evidence should fail conservative.
    """
    if declared is not None:
        return declared
    for pattern, klass in _PATTERNS:
        if pattern.search(correlation_group) or (collector and pattern.search(collector)):
            return klass
    return DependenceClass.UNKNOWN


@dataclass
class DependenceAdjustment:
    """What the correction did, for the report."""

    classes: dict[str, int]
    raw_total: float
    adjusted_total: float
    discounted_groups: int

    @property
    def reduction(self) -> float:
        if self.raw_total == 0:
            return 0.0
        return 1.0 - (self.adjusted_total / self.raw_total)

    def render(self) -> str:
        if not self.discounted_groups:
            return "No dependent groups: all evidence classes distinct."
        lines = [
            f"{self.discounted_groups} group(s) discounted for shared cause "
            f"({self.reduction:.0%} reduction in total evidence).",
            "",
            "| Dependence class | Groups |",
            "|---|---:|",
        ]
        for k, n in sorted(self.classes.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {n} |")
        lines += [
            "",
            "Groups within a class arise from one decision — an operator "
            "configuring one property emits analytics, favicon and header "
            "artifacts together. Counting them separately reads one choice as "
            "several confirmations.",
            "",
            "The discount factors are principled but **unfitted**. Read the "
            "adjusted figure as an ordering, not a measurement.",
        ]
        return "\n".join(lines)


def adjust_for_dependence(
    group_llrs: dict[str, float],
    collectors: dict[str, str] | None = None,
    declared: dict[str, DependenceClass] | None = None,
) -> tuple[float, DependenceAdjustment]:
    """Aggregate group LLRs, discounting groups that share a common cause.

    Within a class: the strongest group contributes in full, each additional one
    at ``WITHIN_CLASS_DISCOUNT``. Across classes: full addition.

    Returns ``(adjusted_total, adjustment)``.
    """
    collectors = collectors or {}
    by_class: dict[DependenceClass, list[float]] = {}

    for group, llr in group_llrs.items():
        klass = classify_group(group, collectors.get(group, ""),
                               declared.get(group) if declared else None)
        by_class.setdefault(klass, []).append(llr)

    raw_total = sum(group_llrs.values())
    adjusted = 0.0
    discounted = 0
    counts: dict[str, int] = {}

    for klass, llrs in by_class.items():
        counts[klass.value] = len(llrs)
        if klass is DependenceClass.INDEPENDENT or len(llrs) == 1:
            adjusted += sum(llrs)
            continue

        # Order by magnitude so the strongest evidence is the one kept whole.
        ordered = sorted(llrs, key=abs, reverse=True)
        factor = WITHIN_CLASS_DISCOUNT[klass]
        adjusted += ordered[0] + factor * sum(ordered[1:])
        discounted += len(ordered) - 1

    return adjusted, DependenceAdjustment(
        classes=counts, raw_total=raw_total, adjusted_total=adjusted,
        discounted_groups=discounted)


def effective_independent_groups(
    group_llrs: dict[str, float],
    collectors: dict[str, str] | None = None,
    declared: dict[str, DependenceClass] | None = None,
) -> int:
    """Distinct dependence classes represented, not raw group count.

    This is what the corroboration requirement should count. Two groups from one
    class are one line of evidence observed twice; the requirement exists to
    demand two *lines*, and counting raw groups let one decision satisfy it.
    """
    collectors = collectors or {}
    classes = set()
    independent_singletons = 0
    for group, llr in group_llrs.items():
        if llr == 0:
            continue
        klass = classify_group(group, collectors.get(group, ""),
                               declared.get(group) if declared else None)
        if klass is DependenceClass.INDEPENDENT:
            independent_singletons += 1
        else:
            # UNKNOWN groups collapse together: without a declaration there is
            # no basis for calling two of them separate lines of evidence.
            classes.add(klass)
    return len(classes) + independent_singletons


def sub_additive(values: list[float], discount: float) -> float:
    """Strongest value in full, remainder discounted. Exposed for testing."""
    if not values:
        return 0.0
    ordered = sorted(values, key=abs, reverse=True)
    return ordered[0] + discount * sum(ordered[1:])


def log_sum_within_group(values: list[float]) -> float:
    """The existing within-group rule, for reference: max plus log(1+n)."""
    if not values:
        return 0.0
    return max(values) + math.log(1 + len(values) - 1)
