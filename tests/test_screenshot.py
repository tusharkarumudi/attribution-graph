"""Screenshot capture, element location, chain integrity and text export.

The governing distinction: a screenshot is a RENDERING, not a capture. The wire
bytes are the primary evidence and verify by hash; a screenshot is what one
browser displayed from them, once, at one viewport. Every test here defends that
line or the record-keeping around it.
"""

import hashlib
from datetime import datetime, timezone

import pytest

from attribution_graph.screenshot import (
    ElementLocation,
    Screenshot,
    ScreenshotCapturer,
    ShotStatus,
    available_renderer,
)

FIXTURE = """<!DOCTYPE html><html><head><title>Test Page</title></head><body>
<header><nav><a href="/">Home</a></nav></header>
<main><p>Operated by Example Media Ltd.</p></main>
<footer><div class="contact"><a>ops@example.test</a></div></footer>
</body></html>"""


@pytest.fixture
def page(tmp_path):
    p = tmp_path / "page.html"
    p.write_text(FIXTURE)
    return f"file://{p}"


def _has_browser():
    return available_renderer() is not None


# Use the declared marker so default `addopts` actually excludes these. A
# skipif alias still collects them, so the "deterministic default suite" was not
# isolated from browser prerequisites -- five failures on a machine with the
# playwright package but no Chromium.
browser_only = pytest.mark.browser


# ---- degradation ----------------------------------------------------------- #

def test_disabled_run_records_skipped_not_missing(tmp_path):
    """An absent screenshot must be distinguishable from a blank page."""
    c = ScreenshotCapturer(tmp_path, enabled=False)
    s = c.capture("https://x.example/")
    assert s.status is ShotStatus.SKIPPED
    assert not s.captured
    assert s.note


def test_no_browser_explains_how_to_get_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "attribution_graph.screenshot.available_renderer", lambda: None)
    c = ScreenshotCapturer(tmp_path)
    s = c.capture("https://x.example/")
    assert s.status is ShotStatus.NO_BROWSER
    assert "playwright" in s.note
    assert "not a blank page" in s.note


def test_capture_failure_does_not_end_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr("attribution_graph.screenshot.available_renderer",
                        lambda: ("playwright", "playwright"))

    def boom(*a, **k):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(ScreenshotCapturer, "_via_playwright", boom)
    c = ScreenshotCapturer(tmp_path)
    s = c.capture("https://x.example/")
    assert s.status is ShotStatus.FAILED
    assert "renderer exploded" in s.note


# ---- capture and location -------------------------------------------------- #

@browser_only
def test_captures_an_image_with_a_hash(tmp_path, page):
    c = ScreenshotCapturer(tmp_path)
    s = c.capture(page)
    assert s.captured
    assert s.image_bytes > 0
    raw = (tmp_path / s.image_path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == s.image_sha256


@browser_only
def test_records_where_on_the_page_a_finding_appeared(tmp_path, page):
    """'The email was on the page' is weak. Where it appeared is checkable."""
    c = ScreenshotCapturer(tmp_path)
    s = c.capture(page, needles=["ops@example.test"])
    assert s.findings
    f = s.findings[0]
    assert f.region == "footer"
    assert "contact" in f.dom_path
    assert f.x is not None and f.y is not None
    assert "ops@example.test" in f.surrounding_text


@browser_only
def test_records_the_page_title_and_viewport(tmp_path, page):
    c = ScreenshotCapturer(tmp_path, viewport=(800, 600))
    s = c.capture(page)
    assert s.page_title == "Test Page"
    assert s.viewport == "800x600"


@browser_only
def test_parent_body_hash_is_caller_declared_not_verified(tmp_path, page):
    """This is a KNOWN LIMITATION, asserted so it cannot be mistaken for a
    guarantee.

    The previous test passed "deadbeef" and checked it was copied, which proved
    only that an assignment happened. Playwright navigates the live URL
    independently: it does not render the preserved body and does not check that
    the browser's main-document bytes match the declared hash. A page changing
    between the HTTP capture and the screenshot yields screenshot B labelled as
    rendering body A.

    Until the capturer renders the preserved body or hashes the browser's own
    response, `parent_body_sha256` is provenance the caller asserted, not
    provenance the tool established.
    """
    c = ScreenshotCapturer(tmp_path)
    s = c.capture(page, parent_body_sha256="0" * 64)
    assert s.parent_body_sha256 == "0" * 64
    # The field is not derived from what was rendered, and nothing in the
    # object claims otherwise.
    assert s.image_sha256 != s.parent_body_sha256
    assert s.to_dict()["kind"] == "derived_rendering"


# ---- the rendering-vs-capture distinction ---------------------------------- #

def test_every_screenshot_is_labelled_a_derived_rendering():
    s = Screenshot(url="https://x", captured_at=datetime.now(timezone.utc),
                   status=ShotStatus.CAPTURED)
    assert s.to_dict()["kind"] == "derived_rendering"


def test_text_log_states_a_screenshot_is_not_proof_of_what_was_served(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    c.capture("https://x.example/")
    out = c.export_text()
    assert "RENDERING, not a capture" in out
    assert "not offered as proof of what was served" in out


def test_annotated_image_references_its_unmodified_parent():
    s = Screenshot(url="https://x", captured_at=datetime.now(timezone.utc),
                   status=ShotStatus.CAPTURED, image_sha256="a" * 64,
                   annotated_path="screenshots/0001.annotated.png",
                   annotated_sha256="b" * 64)
    d = s.to_dict()
    assert "the unannotated image is the one to verify" in d["annotated"]["note"]


# ---- chain integrity ------------------------------------------------------- #

def test_chain_links_every_entry(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    a = c.capture("https://a.example/")
    b = c.capture("https://b.example/")
    assert a.prev_hash == ""
    assert b.prev_hash == a.entry_hash
    ok, problems = c.verify()
    assert ok and not problems


def test_reordering_breaks_the_chain(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    c.capture("https://a.example/")
    c.capture("https://b.example/")
    c.shots.reverse()
    ok, problems = c.verify()
    assert not ok and any("chain break" in p for p in problems)


def test_tampering_with_an_entry_is_detected(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    s = c.capture("https://a.example/")
    s.url = "https://evil.example/"
    ok, problems = c.verify()
    assert not ok and any("entry hash mismatch" in p for p in problems)


@browser_only
def test_altered_image_is_detected(tmp_path, page):
    c = ScreenshotCapturer(tmp_path)
    s = c.capture(page)
    (tmp_path / s.image_path).write_bytes(b"not the original image")
    ok, problems = c.verify()
    assert not ok and any("hash mismatch" in p for p in problems)


def test_image_path_escaping_the_package_is_refused(tmp_path):
    """A manifest is untrusted input when verifying someone else's package."""
    c = ScreenshotCapturer(tmp_path, enabled=False)
    s = c.capture("https://a.example/")
    s.status = ShotStatus.CAPTURED
    s.image_path = "../../../etc/passwd"
    ok, problems = c.verify()
    assert not ok and any("escapes the package" in p for p in problems)


# ---- text export ----------------------------------------------------------- #

def test_text_export_is_readable_without_the_package(tmp_path):
    """An evidence record that needs its own tool to be legible is weaker."""
    c = ScreenshotCapturer(tmp_path, enabled=False)
    c.capture("https://a.example/", note="policy: screenshots off")
    out = c.export_text(tmp_path / "SCREENSHOT_LOG.txt")
    written = (tmp_path / "SCREENSHOT_LOG.txt").read_text()
    assert written == out
    assert "SCREENSHOT EVIDENCE LOG" in out
    assert "https://a.example/" in out
    assert "CHAIN VERIFICATION" in out


def test_text_export_records_the_capture_timestamp(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    s = c.capture("https://a.example/")
    assert s.captured_at.isoformat() in c.export_text()


def test_text_export_separates_not_captured(tmp_path):
    c = ScreenshotCapturer(tmp_path, enabled=False)
    c.capture("https://a.example/")
    out = c.export_text()
    assert "NOT CAPTURED" in out
    assert "not a blank page" in out


@browser_only
def test_text_export_includes_element_locations(tmp_path, page):
    c = ScreenshotCapturer(tmp_path)
    c.capture(page, needles=["ops@example.test"])
    out = c.export_text()
    assert "Findings" in out
    assert "footer" in out


def test_json_export_marks_screenshots_as_secondary(tmp_path):
    import json

    c = ScreenshotCapturer(tmp_path, enabled=False)
    c.capture("https://a.example/")
    doc = json.loads(c.export_json(tmp_path / "screenshot_manifest.json"))
    assert doc["kind"] == "screenshot_log"
    assert "primary artifacts" in doc["note"]
    assert (tmp_path / "screenshot_manifest.json").exists()


# ---- element location ------------------------------------------------------ #

def test_location_describes_itself_for_the_report():
    loc = ElementLocation(selector="footer > a", x=8, y=140, region="footer")
    d = loc.describe()
    assert "footer" in d and "(8, 140)" in d


def test_empty_location_says_so_rather_than_implying_precision():
    assert "not determined" in ElementLocation().describe()


def test_location_serialises_without_empty_fields():
    d = ElementLocation(selector="a", region="footer").to_dict()
    assert "selector" in d and "x" not in d


# ---- integration with the evidence package --------------------------------- #

def test_evidence_package_writes_the_screenshot_log(tmp_path):
    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))
    root = tmp_path / "evidence"
    log = EvidenceLog(scope, root)
    log.record("https://a.example/", 200, b"<html></html>", collector="t")

    cap = ScreenshotCapturer(root, enabled=False)
    cap.capture("https://a.example/")

    write_evidence_package(log, "summary", capturer=cap)
    assert (root / "SCREENSHOT_LOG.txt").exists()
    assert (root / "screenshot_manifest.json").exists()
    # the wire manifest stays separate
    assert (root / "evidence_manifest.json").exists()
