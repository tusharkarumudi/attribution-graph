"""Fetch policy: declared per case, recorded in the evidence package.

You are right about the state of the world — robots.txt is routinely bypassed
with headless browsers and residential proxies, and a library that silently
enforced it would be enforcing a norm the ecosystem abandoned. So there is no
silent enforcement here.

What there is instead is a **declared** policy, because of something specific to
what this toolkit is for. The output is meant to survive review — by opposing
counsel, by a regulator, by a journalist's editor. In that setting the question
is never "did the tool obey robots.txt", it is "can you state what your
collection policy was". A run that cannot answer that has a soft spot that has
nothing to do with whether the bytes are accurate.

So the policy is a case-file field with three settings, and whichever is chosen
goes into the manifest:

- ``respect``  — do not fetch disallowed paths. Slowest, cleanest record.
- ``record``   — fetch anyway, note the directive against the capture. **Default.**
- ``ignore``   — fetch, do not check. Fastest; the manifest says so.

``record`` is the default because it costs one cached fetch per host and leaves
you able to say exactly what you did. ``ignore`` is a legitimate choice and the
module supports it without complaint.

Two things worth knowing when you pick:

**Most sources here do not raise the question.** RDAP, crt.sh, GLEIF, EDGAR,
sellers.json and ads.txt are all published specifically for machine consumption.
Only imprint scraping and county-records HTML touch robots-relevant territory,
so ``respect`` costs you very little in practice.

**Post-*hiQ* and *Van Buren*, scraping public data is not a CFAA problem, but
terms-of-service violations can still support contract and state-law claims.**
"Everybody does it" is an accurate description of practice and not a defence. If
the collection is going into a filing, that is worth a conversation with counsel
before the run rather than after.
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse


class RobotsPolicy(StrEnum):
    RESPECT = "respect"
    RECORD = "record"
    IGNORE = "ignore"


@dataclass
class FetchDecision:
    url: str
    allowed: bool
    policy: RobotsPolicy
    robots_directive: str = "unknown"
    crawl_delay: float | None = None
    note: str = ""

    @property
    def should_fetch(self) -> bool:
        return self.allowed or self.policy is not RobotsPolicy.RESPECT

    def manifest_entry(self) -> dict:
        """This decision, as the evidence manifest records it.

        The manifest used to carry only a list of disallowed URLs, which drops
        the one distinction that matters when reading it later: a URL skipped
        because the site disallows it and a URL skipped because the machine
        running the tool could not reach the origin look identical as bare
        strings. `robots_directive` and `note` separate them, and `fetched`
        states whether the decision was acted on, which differs between
        `respect` and `record`.
        """
        return {
            "url": self.url,
            "policy": self.policy.value,
            "robots_directive": self.robots_directive,
            "fetched": self.should_fetch,
            "note": self.note,
        }


@dataclass
class _RobotsOutcome:
    """Sentinel for a robots.txt retrieval that produced no rules."""

    def __init__(self, name: str, allows: bool, reason: str) -> None:
        self.name, self.allows, self.reason = name, allows, reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<robots {self.name}>"


#: 4xx: the resource does not exist, so no rules apply (RFC 9309 §2.3.1.3).
UNAVAILABLE = _RobotsOutcome(
    "unavailable", True,
    "robots.txt is unavailable (4xx); RFC 9309 permits crawling")

#: 5xx: the resource may exist and may disallow, so the crawler must assume
#: complete disallow (RFC 9309 §2.3.1.4).
UNREACHABLE = _RobotsOutcome(
    "unreachable", False,
    "robots.txt is unreachable (5xx); RFC 9309 requires assuming complete "
    "disallow until the condition resolves")

#: The request never left this machine: DNS did not resolve, a proxy refused
#: it, or there was no route. The conservative outcome is the same, but the
#: REASON is not a statement about the site at all, and reporting it as one
#: sent analysts looking for a block that the site had not applied. A sandbox
#: with an egress allowlist produces this for every host it does not permit.
#: ...and the fetch PROCEEDS. RFC 9309 §2.3.1.4 requires complete disallow for
#: "server or network errors" affecting robots.txt -- an origin that answered
#: 5xx, or a network path to it that failed. It does not address the crawler's
#: own connectivity, and the rationale does not reach it either: a request that
#: never leaves this machine disregards no published rule and places no load on
#: the origin. Treating it as a disallow meant one failed robots.txt in a
#: sandbox suppressed 33 attempts that would have reported honestly, and turned
#: a local egress problem into a sentence about the site.
#:
#: If the host really is gone, nothing is gained or lost: the next request
#: fails the same way and is reported as the transport error it is.
UNREACHABLE_LOCAL = _RobotsOutcome(
    "unreachable_local", True,
    "robots.txt could not be fetched FROM THIS MACHINE (the host did not "
    "resolve, or egress was refused), so no rules were ever retrieved; this is "
    "a local network condition, not a rule published by the site, and RFC 9309 "
    "§2.3.1.4 does not cover it. Each retrieval is attempted and reported on "
    "its own result")


#: Error text that means the request never left this machine. Matched on the
#: message because the transport raises several unrelated exception types for
#: the same condition, and the distinction an analyst needs is "ours or
#: theirs", not which library raised.
_LOCAL_FAILURE_MARKERS = (
    "name or service not known", "temporary failure in name resolution",
    "nodename nor servname", "does not resolve", "no address associated",
    "getaddrinfo", "proxy", "tunnel connection failed", "network is unreachable",
    "no route to host", "connection refused",
)


def _is_local_failure(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in _LOCAL_FAILURE_MARKERS)


def _host_unresolvable(fetcher, host: str) -> bool:
    """Did the fetcher record this host as unreachable from here?"""
    blocked = getattr(fetcher, "blocked", None) or ()
    hostname = host.split(":")[0].lower()
    for url, reason in blocked:
        if hostname in str(url).lower() and any(
                m in str(reason).lower() for m in _LOCAL_FAILURE_MARKERS):
            return True
    return False


@dataclass
class PolicyEngine:
    """Evaluates fetch decisions and caches robots.txt per host."""

    policy: RobotsPolicy = RobotsPolicy.RECORD
    user_agent: str = "*"
    #: (scheme, host) -> parser | UNAVAILABLE | UNREACHABLE.
    #:
    #: Keyed by scheme as well as host: http:// and https:// robots.txt are
    #: separate resources and may carry different rules.
    _cache: dict[tuple[str, str], object] = field(
        default_factory=dict
    )

    async def _robots_for(self, fetcher, host: str, scheme: str):
        """Retrieve robots.txt, distinguishing *unavailable* from *unreachable*.

        RFC 9309 §2.3.1 separates two cases that were being collapsed:

        - **Unavailable** (4xx): the resource does not exist. The crawler may
          assume no rules apply and proceed.
        - **Unreachable** (5xx, network failure, timeout): the resource may
          exist and may disallow. The crawler *must* assume complete disallow
          until the condition resolves.

        Both returned ``None`` here, and ``None`` meant "no rules, go ahead" --
        so a target serving 503 on /robots.txt got crawled under a policy
        called `respect`. An operator who reads that policy name has been told
        something untrue.

        Cached per host: the key distinguishes the three outcomes so a
        transient 503 is not remembered as permission.
        """
        # Scheme is part of the key: http:// and https:// robots.txt are
        # separate resources and may differ.
        key = (scheme, host)
        if key in self._cache:
            return self._cache[key]

        outcome: object
        try:
            r = await fetcher.get(f"{scheme}://{host}/robots.txt", allow_html=True)
        except Exception as e:
            outcome = (UNREACHABLE_LOCAL if _is_local_failure(e) else UNREACHABLE)
        else:
            if r is None and _host_unresolvable(fetcher, host):
                outcome = UNREACHABLE_LOCAL
            elif r is None or r.status >= 500:
                outcome = UNREACHABLE
            elif r.status == 200 and r.text:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(r.text.splitlines())
                outcome = parser
            elif 400 <= r.status < 500:
                outcome = UNAVAILABLE
            else:
                outcome = UNAVAILABLE

        self._cache[key] = outcome
        return outcome

    async def evaluate(self, fetcher, url: str) -> FetchDecision:
        if self.policy is RobotsPolicy.IGNORE:
            return FetchDecision(url, True, self.policy, "not checked",
                                 note="policy=ignore; robots.txt not retrieved")

        parsed = urlparse(url)
        if not parsed.netloc:
            return FetchDecision(url, True, self.policy, "n/a")

        outcome = await self._robots_for(
            fetcher, parsed.netloc, parsed.scheme or "https")

        if isinstance(outcome, _RobotsOutcome):
            # `unavailable` and `unreachable` both produced no rules and were
            # both treated as permission. RFC 9309 gives them opposite
            # meanings, and the difference is exactly the case where a target
            # is failing: 5xx on /robots.txt used to mean "crawl freely" under
            # a policy named `respect`.
            allowed = outcome.allows
            if not allowed and self.policy is RobotsPolicy.RECORD:
                allowed = True
            return FetchDecision(
                url, allowed, self.policy, outcome.name,
                note=outcome.reason + (
                    "; fetched anyway under policy=record"
                    if outcome is UNREACHABLE
                    and self.policy is RobotsPolicy.RECORD
                    else ""))

        parser = outcome

        allowed = parser.can_fetch(self.user_agent, url)
        delay = parser.crawl_delay(self.user_agent)
        directive = "allow" if allowed else "disallow"
        note = ""
        if not allowed and self.policy is RobotsPolicy.RECORD:
            note = ("path is disallowed by robots.txt; fetched under policy=record "
                    "and the directive is preserved in this manifest entry")
        elif not allowed:
            note = "path is disallowed by robots.txt; skipped under policy=respect"

        return FetchDecision(url, allowed, self.policy, directive,
                             crawl_delay=delay, note=note)

    def summary(self, decisions: list[FetchDecision]) -> dict:
        """Manifest block stating what the policy was and what it produced."""
        disallowed = [d for d in decisions if not d.allowed]
        fetched_anyway = [d for d in disallowed if d.should_fetch]
        return {
            "robots_policy": self.policy.value,
            "user_agent": self.user_agent,
            "urls_evaluated": len(decisions),
            "disallowed_by_robots": len(disallowed),
            "fetched_despite_disallow": len(fetched_anyway),
            "skipped": len(disallowed) - len(fetched_anyway),
            # Structured, not bare URLs. `FetchDecision.manifest_entry` was
            # written for this block and never called, so the manifest stated
            # that 34 URLs were disallowed without saying that every one of
            # them was a local network failure rather than a site's policy.
            "disallowed": [d.manifest_entry() for d in disallowed[:100]],
            "disallowed_urls": [d.url for d in disallowed[:100]],
            "statement": _statement(self.policy, len(disallowed), len(fetched_anyway)),
        }


def _statement(policy: RobotsPolicy, disallowed: int, fetched: int) -> str:
    if policy is RobotsPolicy.IGNORE:
        return ("Collection was performed without consulting robots.txt. No "
                "determination was made as to whether any retrieved path was "
                "disallowed.")
    if policy is RobotsPolicy.RESPECT:
        return (f"robots.txt was consulted for each host. {disallowed} path(s) were "
                f"disallowed and were not retrieved.")
    return (f"robots.txt was consulted for each host and the directive recorded "
            f"against each capture. {disallowed} path(s) were disallowed; "
            f"{fetched} of those were retrieved notwithstanding, under a "
            f"recorded policy of 'record'.")
