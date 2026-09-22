"""Trusted evidence verification. Reads data; never executes package code.

    python -m attribution_graph.verify ./out/evidence

`attribution verify` used to run the `verify.py` found *inside* the evidence
directory. An evidence package is untrusted input by definition -- it is the
thing under examination, frequently produced by someone else -- so the
verification workflow itself invited arbitrary code execution. A benign
reproduction replaced `verify.py` with a script that wrote a marker file, and
the normal workflow ran it.

The generated standalone `verify.py` still ships, because a dependency-free
checker someone can read in full has real value. But the *installed command*
uses this module, which parses the manifest as data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

#: Fields the entry hash commits to. Must match Capture.compute_entry_hash.
ENTRY_SCHEMA = "capture/2"


@dataclass
class VerificationResult:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    case_ref: str = ""
    captures: int = 0
    chain_head: str = ""

    def fail(self, problem: str) -> None:
        self.ok = False
        self.problems.append(problem)

    def render(self) -> str:
        lines = [f"case:     {self.case_ref}",
                 f"captures: {self.captures}",
                 f"chain:    {self.chain_head}"]
        if self.problems:
            lines.append(f"\nFAILED ({len(self.problems)} problem(s)):")
            lines += [f"  {p}" for p in self.problems]
        else:
            lines += [
                "",
                "PASSED - every body matches its digest, every entry hash covers",
                "its full provenance envelope, and the chain head matches the",
                "manifest.",
                "",
                "This proves internal consistency. It cannot prove the package was",
                "not regenerated wholesale; only an external anchor (an RFC 3161",
                "timestamp over the manifest hash, or a signature) establishes",
                "that. See TIMESTAMP.md.",
            ]
        return "\n".join(lines)


def _entry_hash(cap: dict) -> str:
    payload = json.dumps({
        "schema": cap.get("schema", ENTRY_SCHEMA),
        "sequence": cap["sequence"],
        "url": cap["url"],
        "method": cap.get("method", "GET"),
        "requested_at": cap["requested_at"],
        "status": cap.get("status"),
        "body_sha256": cap.get("body_sha256", ""),
        "body_bytes": cap.get("body_bytes", 0),
        "body_path": cap.get("body_path", ""),
        "collector": cap.get("collector", ""),
        "outcome": cap.get("outcome", "success"),
        "note": cap.get("note", ""),
        "request_headers": dict(sorted((cap.get("request_headers") or {}).items())),
        "response_headers": dict(sorted((cap.get("response_headers") or {}).items())),
        "egress": cap.get("egress", ""),
        "prev_hash": cap.get("prev_hash", ""),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_package(root: Path | str) -> VerificationResult:
    """Verify an evidence package by reading it. No code from it is executed."""
    root = Path(root).resolve()
    result = VerificationResult()

    manifest = root / "evidence_manifest.json"
    if not manifest.exists():
        result.fail(f"no evidence_manifest.json in {root}")
        return result

    try:
        doc = json.loads(manifest.read_text())
    except json.JSONDecodeError as e:
        result.fail(f"manifest is not valid JSON: {e}")
        return result

    result.case_ref = doc.get("case_ref", "(unknown)")
    result.chain_head = doc.get("manifest_hash", "")
    captures = doc.get("captures", [])
    result.captures = len(captures)

    prev = ""
    for cap in captures:
        seq = cap.get("sequence", "?")

        body_path = cap.get("body_path", "")
        if body_path:
            # An untrusted manifest is a file-read primitive without this.
            candidate = (root / body_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                result.fail(f"seq {seq}: body_path escapes the package")
                prev = cap.get("entry_hash", "")
                continue
            if not candidate.exists():
                result.fail(f"seq {seq}: body missing ({body_path})")
            else:
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if digest != cap.get("body_sha256"):
                    result.fail(f"seq {seq}: body hash mismatch")

        if cap.get("prev_hash", "") != prev:
            result.fail(f"seq {seq}: chain break")
        if _entry_hash(cap) != cap.get("entry_hash"):
            result.fail(f"seq {seq}: entry hash mismatch")
        prev = cap.get("entry_hash", "")

    if prev != result.chain_head:
        result.fail(
            f"chain head mismatch: manifest declares {result.chain_head[:16]}... "
            f"but the captures recompute to {prev[:16] or '(none)'}... — one or "
            "more trailing captures have been removed, or the manifest was edited")

    # Top-level envelope. The chain covers captures; this covers the claims the
    # package makes about itself -- under what authority the collection
    # happened, under what policy, with which build.
    from .evidence import envelope_digest_of

    declared_envelope = doc.get("envelope_digest")
    if declared_envelope is None:
        result.problems.append(
            "manifest has no envelope_digest: top-level metadata "
            "(case_ref, authorization, fetch_policy, environment) is "
            "unauthenticated. Written by a version before manifest/1.")
        result.ok = False
    elif envelope_digest_of(doc) != declared_envelope:
        result.fail(
            "envelope digest mismatch: the manifest's own metadata has been "
            "altered. One or more of case_ref, authorization, opened_at, "
            "closed_at, environment, fetch_policy, outcomes, notice, "
            "capture_count or manifest_hash does not match what was recorded.")

    declared = doc.get("capture_count")
    if declared is not None and declared != len(captures):
        result.fail(f"capture_count says {declared} but {len(captures)} present")

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m attribution_graph.verify",
        description="Verify an evidence package. Reads it as data; never "
                    "executes code from it.")
    ap.add_argument("path", help="evidence directory")
    a = ap.parse_args(argv)
    result = verify_package(a.path)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
