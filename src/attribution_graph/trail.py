"""Verification trail.

What an investigator needs in order to check a report *now*: the reasoning in
the order it happened, an exact timestamp on every item, and a numbered citation
list at the bottom that ties each assertion to the source it came from.

That is a different artifact from the evidence manifest. The manifest proves the
bytes are unaltered; the trail explains how you got from a seed to a conclusion,
in sequence, so a second analyst can follow the same path and check each hop
against the cited source without reading any code.

Design rules that follow from the purpose:

- **Every step is timestamped individually.** A single "generated at" line on the
  report is useless; the reviewer needs to know the domain was fetched at
  14:03:22 and the registry queried at 14:03:41.
- **Every assertion carries a citation number.** Superscript markers in the body,
  a numbered source list at the end, each entry with its URL, retrieval
  timestamp, and body hash.
- **Steps that found nothing are recorded.** A reviewer needs to distinguish
  "we checked and it was empty" from "we never checked".
- **Inference is labelled as inference.** Steps are typed, so a reviewer can see
  at a glance which lines are observations and which are conclusions drawn from
  them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StepKind(StrEnum):
    SEED = "seed"                # where the investigation started
    FETCH = "fetch"              # a source was retrieved
    EXTRACT = "extract"          # a value was read out of retrieved content
    PIVOT = "pivot"              # a new lead followed from an extracted value
    QUERY_EMPTY = "empty"        # a source was checked and held nothing
    FILTER = "filter"            # something was dropped or demoted, with reason
    INFERENCE = "inference"      # a conclusion drawn, not observed
    REFUSAL = "refusal"          # a source deliberately not queried


#: Kinds that represent things observed rather than concluded.
OBSERVATIONAL = frozenset({
    StepKind.SEED, StepKind.FETCH, StepKind.EXTRACT,
    StepKind.QUERY_EMPTY, StepKind.REFUSAL,
})


@dataclass
class Source:
    """A cited source. One entry per distinct URL retrieval."""

    number: int
    url: str
    retrieved_at: datetime
    body_sha256: str = ""
    status: int | None = None
    collector: str = ""
    title: str = ""

    def citation(self) -> str:
        parts = [f"[{self.number}] {self.title or self.url}"]
        if self.title:
            parts.append(f"    {self.url}")
        parts.append(f"    Retrieved {self.retrieved_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                     + (f" (HTTP {self.status})" if self.status else ""))
        if self.body_sha256:
            parts.append(f"    SHA-256 {self.body_sha256}")
        return "\n".join(parts)


@dataclass
class Step:
    number: int
    kind: StepKind
    at: datetime
    description: str
    detail: str = ""
    citations: list[int] = field(default_factory=list)
    depth: int = 0

    @property
    def is_observation(self) -> bool:
        return self.kind in OBSERVATIONAL

    def render(self) -> str:
        marks = "".join(f"[{n}]" for n in self.citations)
        indent = "  " * self.depth
        tag = "" if self.is_observation else f" *({self.kind.value})*"
        line = (f"{indent}**{self.number}.** `{self.at.strftime('%H:%M:%S')}` "
                f"{self.description}{marks}{tag}")
        if self.detail:
            line += f"\n{indent}    {self.detail}"
        return line

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.number,
            "kind": self.kind.value,
            "at": self.at.isoformat(),
            "description": self.description,
            "detail": self.detail,
            "citations": self.citations,
            "depth": self.depth,
            "is_observation": self.is_observation,
        }


class Trail:
    """Ordered, timestamped record of an investigation with numbered citations."""

    def __init__(self, case_ref: str, authorization: str = "") -> None:
        self.case_ref = case_ref
        self.authorization = authorization
        self.started_at = _utcnow()
        self.steps: list[Step] = []
        self.sources: list[Source] = []
        self._by_url: dict[str, int] = {}

    # ---- sources ----------------------------------------------------------- #

    def cite(
        self, url: str, *, retrieved_at: datetime | None = None,
        body_sha256: str = "", status: int | None = None,
        collector: str = "", title: str = "",
    ) -> int:
        """Register a source and return its citation number.

        Repeated retrievals of the same URL get distinct numbers when the body
        differs, because they are different observations of a changing resource.
        """
        key = f"{url}|{body_sha256}"
        if key in self._by_url:
            return self._by_url[key]
        n = len(self.sources) + 1
        self.sources.append(Source(
            number=n, url=url, retrieved_at=retrieved_at or _utcnow(),
            body_sha256=body_sha256, status=status, collector=collector, title=title,
        ))
        self._by_url[key] = n
        return n

    # ---- steps -------------------------------------------------------------- #

    def add(
        self, kind: StepKind, description: str, *,
        detail: str = "", citations: list[int] | None = None,
        depth: int = 0, at: datetime | None = None,
    ) -> Step:
        s = Step(
            number=len(self.steps) + 1, kind=kind, at=at or _utcnow(),
            description=description, detail=detail,
            citations=list(citations or []), depth=depth,
        )
        self.steps.append(s)
        return s

    # convenience wrappers
    def seed(self, what: str) -> Step:
        return self.add(StepKind.SEED, f"Investigation seeded with `{what}`")

    def fetch(self, url: str, *, status: int | None = None, body_sha256: str = "",
              collector: str = "", title: str = "", depth: int = 0) -> Step:
        n = self.cite(url, body_sha256=body_sha256, status=status,
                      collector=collector, title=title)
        return self.add(StepKind.FETCH, f"Retrieved {title or url}",
                        citations=[n], depth=depth)

    def extract(self, what: str, value: str, citation: int, depth: int = 0) -> Step:
        return self.add(StepKind.EXTRACT, f"Read {what}: `{value}`",
                        citations=[citation], depth=depth)

    def pivot(self, frm: str, to: str, why: str, depth: int = 0) -> Step:
        return self.add(StepKind.PIVOT, f"Followed `{frm}` to `{to}`",
                        detail=why, depth=depth)

    def empty(self, url: str, what: str, collector: str = "", depth: int = 0) -> Step:
        n = self.cite(url, status=200, collector=collector)
        return self.add(StepKind.QUERY_EMPTY,
                        f"Checked {what} — no matching records",
                        citations=[n], depth=depth)

    def filtered(self, what: str, reason: str, depth: int = 0) -> Step:
        return self.add(StepKind.FILTER, f"Set aside `{what}`",
                        detail=reason, depth=depth)

    def refused(self, what: str, reason: str, depth: int = 0) -> Step:
        return self.add(StepKind.REFUSAL, f"Did not query {what}",
                        detail=reason, depth=depth)

    def add_imported(self, system: str, path: str, count: int) -> Step:
        n = self.cite(path, title=f'{system} export', collector=system.lower())
        return self.add(StepKind.FETCH,
                        f'Imported {count} claim(s) from {system}',
                        detail=f'source file: {path}', citations=[n])

    def infer(self, conclusion: str, *, basis: str = "",
              citations: list[int] | None = None, depth: int = 0) -> Step:
        return self.add(StepKind.INFERENCE, conclusion, detail=basis,
                        citations=citations, depth=depth)

    # ---- rendering ---------------------------------------------------------- #

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            out[s.kind.value] = out.get(s.kind.value, 0) + 1
        return out

    def render(self, title: str = "Verification trail") -> str:
        obs = sum(1 for s in self.steps if s.is_observation)
        inf = len(self.steps) - obs
        L = [
            f"# {title}",
            "",
            f"Case `{self.case_ref}`"
            + (f" · authorization: {self.authorization}" if self.authorization else ""),
            f"Started {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')} · "
            f"{len(self.steps)} steps ({obs} observations, {inf} analytical) · "
            f"{len(self.sources)} sources",
            "",
            "Steps are in the order performed, each with the time it was performed.",
            "Bracketed numbers cite the numbered source list at the end. Steps marked",
            "*(inference)* are conclusions drawn from the observations above them,",
            "not things directly observed.",
            "",
            "## Steps",
            "",
        ]
        L += [s.render() for s in self.steps]
        L += ["", "## Sources", ""]
        if not self.sources:
            L.append("_No external sources retrieved._")
        for src in self.sources:
            L.append(src.citation())
            L.append("")
        return "\n".join(L)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_ref": self.case_ref,
            "authorization": self.authorization,
            "started_at": self.started_at.isoformat(),
            "step_counts": self.counts,
            "steps": [s.to_dict() for s in self.steps],
            "sources": [
                {"number": s.number, "url": s.url,
                 "retrieved_at": s.retrieved_at.isoformat(),
                 "body_sha256": s.body_sha256, "status": s.status,
                 "collector": s.collector, "title": s.title}
                for s in self.sources
            ],
        }

    def write(self, outdir: Path, title: str = "Verification trail") -> list[Path]:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        md = outdir / "verification_trail.md"
        md.write_text(self.render(title))
        js = outdir / "verification_trail.json"
        js.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return [md, js]
