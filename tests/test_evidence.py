"""Evidence chain of custody."""

import json
import subprocess
import sys

import pytest

from attribution_graph import CaseScope, EvidenceLog, write_evidence_package


@pytest.fixture
def log(tmp_path):
    c = tmp_path / "case.yaml"
    c.write_text("case_ref: EV-1\nauthorization: test warrant 123\n"
                 "seeds: [domain:example.com]\n"
                 f"audit_path: {tmp_path / 'audit.jsonl'}\n")
    return EvidenceLog(CaseScope.load(str(c)), tmp_path / "evidence")


def test_capture_is_content_addressed(log):
    a = log.record("https://a.example", 200, b"hello")
    b = log.record("https://b.example", 200, b"hello")
    assert a.body_sha256 == b.body_sha256           # same body stored once
    assert a.sequence == 1 and b.sequence == 2      # distinct events


def test_chain_links_each_entry_to_the_previous(log):
    a = log.record("https://a.example", 200, b"one")
    b = log.record("https://b.example", 200, b"two")
    assert a.prev_hash == ""
    assert b.prev_hash == a.entry_hash


def test_verification_passes_on_clean_log(log):
    log.record("https://a.example", 200, b"one")
    log.record("https://b.example", 200, b"two")
    ok, problems = log.verify()
    assert ok and not problems


def test_tampering_with_a_body_is_detected(log):
    cap = log.record("https://a.example", 200, b"original")
    (log.root / cap.body_path).write_bytes(b"tampered")
    ok, problems = log.verify()
    assert not ok
    assert any("hash mismatch" in p for p in problems)


def test_removing_a_capture_breaks_the_chain(log):
    log.record("https://a.example", 200, b"one")
    log.record("https://b.example", 200, b"two")
    log.record("https://c.example", 200, b"three")
    del log.captures[1]
    ok, problems = log.verify()
    assert not ok
    assert any("chain break" in p for p in problems)


def test_negative_results_are_recorded(log):
    """'We searched and found nothing' is a finding and is unrecoverable later."""
    c = log.record_negative("https://api.ch/search?q=Acme", "companies_house_uk",
                            "no matching entity on 2026-08-17")
    assert c.outcome == "empty"
    assert "no matching entity" in c.note


def test_refusals_are_recorded(log):
    c = log.record_refusal("person_name:Jane Doe", "records", "entity-keyed only")
    assert c.outcome == "refused"


def test_manifest_states_non_reproducibility(log):
    log.record("https://a.example", 200, b"x")
    doc = json.loads(log.write_manifest().read_text())
    assert "not reproducible" in doc["notice"].lower()
    assert doc["self_verified"] is True
    assert doc["outcomes"]["success"] == 1


def test_declaration_includes_chain_head_and_authorization(log):
    log.record("https://a.example", 200, b"x")
    paths = write_evidence_package(log, "test findings")
    text = (log.root / "DECLARATION_DRAFT.md").read_text()
    assert log.manifest_hash in text
    assert "test warrant 123" in text
    assert "analytical opinion" in text          # analysis vs observation split
    assert len(paths) == 4


def test_standalone_verifier_runs_without_the_package(log):
    log.record("https://a.example", 200, b"one")
    log.record("https://b.example", 200, b"two")
    write_evidence_package(log)
    r = subprocess.run([sys.executable, str(log.root / "verify.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "PASSED" in r.stdout


def test_standalone_verifier_fails_on_tampered_package(log):
    cap = log.record("https://a.example", 200, b"one")
    write_evidence_package(log)
    (log.root / cap.body_path).write_bytes(b"altered")
    r = subprocess.run([sys.executable, str(log.root / "verify.py")],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "FAILED" in r.stdout


def test_report_can_suppress_scores():
    from attribution_graph import AttributionGraph, EntityType, report, resolve
    g = AttributionGraph(case_ref="X")
    r = resolve(g, {EntityType.COMPANY})

    class S:
        case_ref = "X"
        authorization = "a"

        def expiry(self):
            from datetime import datetime, timezone
            return datetime.now(timezone.utc)

    with_scores = report(g, r, S(), show_scores=True)
    without = report(g, r, S(), show_scores=False)
    assert "evidence score" in with_scores and "analytical judgment" in with_scores
    assert "p(same)" not in without and "analytical judgment" not in without


def test_source_attribution_lists_terms_for_sources_used():
    from attribution_graph import (
        AttributionGraph,
        Claim,
        Identifier,
        IdKind,
        Predicate,
        source_attribution,
    )
    g = AttributionGraph(case_ref="X")
    g.add_claim(Claim(
        subject=Identifier(IdKind.ORG_NAME, "Acme"), predicate=Predicate.LEGAL_NAME,
        object=Identifier(IdKind.LEI, "5493001KJTIIGC8Y1R12"),
        collector="opencorporates", source_url="https://opencorporates.com/x"))
    out = source_attribution(g)
    assert "OpenCorporates" in out and "non-commercial" in out
    assert "Confirm current terms" in out


def test_fetch_policy_is_recorded_in_the_manifest(log):
    from attribution_graph import PolicyEngine, RobotsPolicy
    eng = PolicyEngine(policy=RobotsPolicy.RECORD, user_agent="test/1")
    log.record("https://a.example", 200, b"x")
    log.fetch_policy = eng.summary([])
    doc = json.loads(log.write_manifest().read_text())
    assert doc["fetch_policy"]["robots_policy"] == "record"
    assert "statement" in doc["fetch_policy"]


def test_declaration_states_the_collection_policy(log):
    from attribution_graph import PolicyEngine, RobotsPolicy
    log.record("https://a.example", 200, b"x")
    log.fetch_policy = PolicyEngine(policy=RobotsPolicy.IGNORE).summary([])
    write_evidence_package(log)
    text = (log.root / "DECLARATION_DRAFT.md").read_text()
    assert "Collection policy" in text
    assert "without consulting robots.txt" in text


@pytest.mark.parametrize("policy,expect", [
    ("respect", "were not retrieved"),
    ("record", "notwithstanding"),
    ("ignore", "No determination was made"),
])
def test_each_policy_produces_a_distinct_statement(policy, expect):
    from attribution_graph import FetchDecision, PolicyEngine, RobotsPolicy
    eng = PolicyEngine(policy=RobotsPolicy(policy))
    d = FetchDecision("https://a.example/x", False, RobotsPolicy(policy), "disallow")
    assert expect in eng.summary([d])["statement"]


def test_respect_skips_disallowed_but_others_fetch():
    from attribution_graph import FetchDecision, RobotsPolicy
    for p, should in ((RobotsPolicy.RESPECT, False), (RobotsPolicy.RECORD, True),
                      (RobotsPolicy.IGNORE, True)):
        assert FetchDecision("https://a/x", False, p, "disallow").should_fetch is should


# ---- EA-01/EA-02: tamper and truncation resistance ------------------------- #

def _package(tmp_path, n=2):
    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))
    root = tmp_path / "ev"
    log = EvidenceLog(scope, root)
    for i in range(n):
        log.record(f"https://a.example/{i}", 200, f"body{i}".encode(), collector="t")
    write_evidence_package(log, "summary")
    return log, root


def _run_verifier(root):
    import subprocess
    import sys

    return subprocess.run([sys.executable, str(root / "verify.py")],
                          capture_output=True, text=True, cwd=root)


def test_intact_package_verifies(tmp_path):
    _, root = _package(tmp_path)
    assert _run_verifier(root).returncode == 0


@pytest.mark.parametrize("field,value", [
    ("collector", "someone_else"),
    ("note", "rewritten"),
    ("body_path", "captures/other.bin"),
    ("egress", "de"),
])
def test_provenance_tampering_is_detected(tmp_path, field, value):
    """The entry hash covered only body identity, so an audit rewrote request
    headers, response headers, collector and note on a real package and the
    standalone verifier reported PASSED. "Verified" did not mean the provenance
    was unaltered, which is the one thing the package exists to establish."""
    import json

    _, root = _package(tmp_path)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"][0][field] = value
    (root / "evidence_manifest.json").write_text(json.dumps(m, indent=2))

    r = _run_verifier(root)
    assert r.returncode != 0
    assert "entry hash mismatch" in r.stdout


def test_header_tampering_is_detected(tmp_path):
    import json

    _, root = _package(tmp_path)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"][0]["response_headers"] = {"server": "fabricated"}
    (root / "evidence_manifest.json").write_text(json.dumps(m, indent=2))
    assert _run_verifier(root).returncode != 0


def test_suffix_truncation_is_detected(tmp_path):
    """A hash chain prevents insertion and reordering in the middle, because
    later entries commit to earlier ones. It does not prevent deleting a
    suffix. An audit removed the final capture, decremented capture_count, left
    manifest_hash untouched, and the verifier reported PASSED."""
    import json

    _, root = _package(tmp_path, n=2)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"] = m["captures"][:1]
    m["capture_count"] = 1
    (root / "evidence_manifest.json").write_text(json.dumps(m, indent=2))

    r = _run_verifier(root)
    assert r.returncode != 0
    assert "chain head mismatch" in r.stdout


def test_count_mismatch_is_detected(tmp_path):
    import json

    _, root = _package(tmp_path, n=2)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["capture_count"] = 99
    (root / "evidence_manifest.json").write_text(json.dumps(m, indent=2))
    assert _run_verifier(root).returncode != 0


def test_verifier_states_what_it_cannot_prove(tmp_path):
    """Internal consistency is not provenance. Only an external anchor
    establishes that the package was not regenerated wholesale."""
    _, root = _package(tmp_path)
    out = _run_verifier(root).stdout
    assert "cannot prove" in out
    assert "RFC 3161" in out or "TIMESTAMP" in out


def test_entry_schema_is_versioned(tmp_path):
    """Adding a hashed field without bumping the schema changes every digest
    and fails loudly, rather than silently leaving a field unprotected."""
    from attribution_graph.evidence import Capture

    assert Capture.ENTRY_SCHEMA.startswith("capture/")


# ---- RB-04: verification must never execute package-supplied code ---------- #

def test_trusted_verifier_reads_data_and_executes_nothing(tmp_path):
    """`attribution verify` located verify.py inside the package and ran it with
    subprocess. An evidence package is untrusted input -- it is the thing being
    checked -- so its author could put arbitrary Python there and anyone
    following the documented workflow would execute it."""
    from attribution_graph import verify_package

    _, root = _package(tmp_path)
    marker = tmp_path / "EXECUTED"
    (root / "verify.py").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('pwned')\n")

    result = verify_package(root)
    assert result.ok
    assert not marker.exists(), "package-supplied code must never run"


def test_trusted_verifier_detects_the_same_tampering(tmp_path):
    import json

    from attribution_graph import verify_package

    _, root = _package(tmp_path)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"][0]["collector"] = "forged"
    (root / "evidence_manifest.json").write_text(json.dumps(m))
    assert not verify_package(root).ok


def test_trusted_verifier_detects_suffix_deletion(tmp_path):
    import json

    from attribution_graph import verify_package

    _, root = _package(tmp_path, n=2)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"] = m["captures"][:1]
    m["capture_count"] = 1
    (root / "evidence_manifest.json").write_text(json.dumps(m))
    result = verify_package(root)
    assert not result.ok
    assert any("chain head" in p for p in result.problems)


def test_trusted_verifier_refuses_a_body_path_escape(tmp_path):
    import json

    from attribution_graph import verify_package

    _, root = _package(tmp_path)
    m = json.loads((root / "evidence_manifest.json").read_text())
    m["captures"][0]["body_path"] = "../../../etc/passwd"
    (root / "evidence_manifest.json").write_text(json.dumps(m))
    assert any("escapes" in p for p in verify_package(root).problems)


def test_trusted_verifier_states_what_it_cannot_prove(tmp_path):
    from attribution_graph import verify_package

    _, root = _package(tmp_path)
    assert "cannot prove" in verify_package(root).render()
