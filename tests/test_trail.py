"""Verification trail: order, timestamps, citations."""

from attribution_graph import StepKind, Trail


def test_steps_are_numbered_and_individually_timestamped():
    t = Trail("C-1", "warrant 9")
    t.seed("domain:a.example")
    t.fetch("https://a.example/ads.txt", status=200, body_sha256="ab" * 32)
    assert [s.number for s in t.steps] == [1, 2]
    assert all(s.at for s in t.steps)
    assert t.steps[0].at <= t.steps[1].at


def test_citations_are_numbered_and_listed():
    t = Trail("C-1")
    n = t.cite("https://api.gleif.org/x", body_sha256="cd" * 32, status=200)
    t.extract("LEI", "5493001KJTIIGC8Y1R12", n)
    out = t.render()
    assert f"[{n}]" in out
    assert "https://api.gleif.org/x" in out
    assert "cd" * 32 in out


def test_repeated_url_with_different_body_gets_a_new_citation():
    t = Trail("C-1")
    a = t.cite("https://a.example", body_sha256="11" * 32)
    b = t.cite("https://a.example", body_sha256="22" * 32)
    assert a != b


def test_empty_results_are_recorded_distinctly():
    t = Trail("C-1")
    t.empty("https://api.ch/search?q=Acme", "UK Companies House", "companies_house_uk")
    assert t.steps[0].kind is StepKind.QUERY_EMPTY
    assert "no matching records" in t.render()


def test_refusals_are_recorded():
    t = Trail("C-1")
    t.refused("people-search aggregators", "denied source class")
    assert t.steps[0].kind is StepKind.REFUSAL


def test_inference_is_visually_distinguished_from_observation():
    t = Trail("C-1")
    t.fetch("https://a.example", status=200)
    t.infer("a.example and b.example share an operator", basis="shared AdSense ID")
    assert t.steps[0].is_observation
    assert not t.steps[1].is_observation
    assert "*(inference)*" in t.render()


def test_header_counts_observations_and_inferences():
    t = Trail("C-1")
    t.fetch("https://a.example", status=200)
    t.fetch("https://b.example", status=200)
    t.infer("common control")
    assert "2 observations, 1 analytical" in t.render()


def test_trail_writes_both_formats(tmp_path):
    t = Trail("C-1")
    t.seed("domain:a.example")
    t.fetch("https://a.example", status=200, body_sha256="ef" * 32)
    paths = t.write(tmp_path)
    assert {p.name for p in paths} == {"verification_trail.md", "verification_trail.json"}
    assert all(p.exists() for p in paths)
