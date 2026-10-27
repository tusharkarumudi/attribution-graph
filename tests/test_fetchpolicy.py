

# --------------------------------------------------------------------------- #
# Whose failure was it?
# --------------------------------------------------------------------------- #

def test_a_5xx_on_robots_still_stops_the_crawl():
    """RFC 9309 §2.3.1.4: the origin answered, and what it said was 5xx. The
    file may exist and may disallow, so complete disallow is required. This is
    the case the RFC is about and it does not change."""
    from attribution_graph.fetchpolicy import UNREACHABLE

    assert UNREACHABLE.allows is False


def test_a_4xx_on_robots_permits_crawling():
    from attribution_graph.fetchpolicy import UNAVAILABLE

    assert UNAVAILABLE.allows is True


def test_our_own_network_failing_does_not_stop_the_crawl():
    """The case the RFC does not cover.

    §2.3.1.4 is about "server or network errors" affecting robots.txt -- an
    origin that answered 5xx, or a path to it that failed. A request that never
    leaves this machine disregards no published rule and puts no load on the
    origin, so there is nothing to be conservative about: attempt the retrieval
    and report what actually happens.

    Treating it as a disallow meant one failed robots.txt inside a sandbox
    suppressed 33 retrievals and reported them as the site's policy.
    """
    from attribution_graph.fetchpolicy import UNREACHABLE_LOCAL

    assert UNREACHABLE_LOCAL.allows is True
    assert "FROM THIS MACHINE" in UNREACHABLE_LOCAL.reason
    assert "not a rule published by the site" in UNREACHABLE_LOCAL.reason


def test_a_dns_failure_is_classified_as_ours():
    import socket

    from attribution_graph.fetchpolicy import _is_local_failure

    for exc in (socket.gaierror(-2, "Name or service not known"),
                OSError("Tunnel connection failed: 403 Forbidden"),
                OSError("[Errno 101] Network is unreachable"),
                RuntimeError("proxy error")):
        assert _is_local_failure(exc), exc


def test_an_origin_error_is_not_classified_as_ours():
    from attribution_graph.fetchpolicy import _is_local_failure

    for exc in (TimeoutError("read timed out"),
                ValueError("certificate verify failed"),
                RuntimeError("server disconnected without sending a response")):
        assert not _is_local_failure(exc), exc


def test_ads_txt_is_reached_when_robots_failed_locally():
    """The behaviour that matters, end to end: a sandboxed run must still try
    the one file the whole method rests on."""
    import asyncio

    from attribution_graph.fetchpolicy import PolicyEngine, RobotsPolicy

    class Fetcher:
        blocked = [("https://x.example/robots.txt", "host does not resolve")]

        async def get(self, url, **kw):
            return None

    engine = PolicyEngine(policy=RobotsPolicy.RESPECT, user_agent="t")
    decision = asyncio.run(engine.evaluate(Fetcher(), "https://x.example/ads.txt"))
    assert decision.allowed is True, decision.note
    assert "FROM THIS MACHINE" in decision.note
