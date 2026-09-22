"""Obfuscation detection and identifier normalization.

Two problems, one mechanism.

## Problem 1: correlation-group inflation

The scoring model's central defense is that correlated observations share a
correlation group and cannot accumulate. That defense assumes identifiers
compare equal when they denote the same thing.

An adversary who can publish four cosmetic variants of one analytics ID —
``G-ABC123``, ``g-abc123``, ``G-ABC123`` with a zero-width mark, ``G‑ABC123``
with a non-breaking hyphen — gets four correlation groups from one fact. Measured
before this module existed:

    honest:  1 group  -> WEAK
    evaded:  4 groups -> ATTRIBUTED

That is not a scoring inaccuracy, it is a forged attribution. Normalizing
identifiers before they are keyed is what closes it.

## Problem 2: obfuscation is itself evidence

A legal entity registered as ``Exаmple Media Ltd`` with a Cyrillic а is not a
typo. Nobody does that by accident, and an investigation that silently repairs
the string loses the most interesting fact it found.

So normalization is **recorded, never silent**. Every transformation is
returned alongside the normalized value, counted per-case, and surfaced in the
report. A cluster whose identifiers needed heavy normalization is a cluster
someone worked to hide.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


class Obfuscation(StrEnum):
    ZERO_WIDTH = "zero_width"          # ZWSP/ZWNJ/ZWJ/BOM inside a value
    BIDI_CONTROL = "bidi_control"      # RTL/LTR overrides — can reverse display
    HOMOGLYPH = "homoglyph"            # Cyrillic/Greek letters posing as Latin
    CONFUSABLE_PUNCT = "confusable_punct"   # non-ASCII hyphens, quotes, spaces
    CASE_VARIANT = "case_variant"      # case differs on a case-insensitive id
    WHITESPACE = "whitespace"          # padding or internal runs
    COMPATIBILITY = "compatibility"    # fullwidth/superscript forms
    CONTROL_CHAR = "control_char"      # C0/C1 controls


#: Severity for reporting. Whitespace and case are usually sloppiness; the rest
#: require deliberate effort.
DELIBERATE = frozenset({
    Obfuscation.ZERO_WIDTH, Obfuscation.BIDI_CONTROL, Obfuscation.HOMOGLYPH,
    Obfuscation.CONTROL_CHAR,
})

ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff\u180e"
BIDI_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"

#: Cyrillic and Greek characters that render identically to Latin in most fonts.
HOMOGLYPHS = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0455": "s", "\u04bb": "h",
    "\u0501": "d", "\u051b": "q", "\u0261": "g", "\u04cf": "l", "\u0458": "j",
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
    "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
    "\u0425": "X", "\u0405": "S", "\u0406": "I", "\u0408": "J",
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03c5": "u", "\u03bd": "v",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
    "\u03a1": "P", "\u03a4": "T", "\u03a7": "X",
}

#: Punctuation that renders like ASCII but is not.
CONFUSABLE_PUNCT = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2015": "-", "\u2212": "-", "\uff0d": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u3000": " ",
    "\uff0e": ".", "\u3002": ".", "\uff1a": ":", "\uff0f": "/",
}

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


@dataclass
class NormalizationResult:
    original: str
    normalized: str
    findings: set[Obfuscation] = field(default_factory=set)

    @property
    def changed(self) -> bool:
        return self.original != self.normalized

    @property
    def deliberate(self) -> bool:
        """Transformations nobody applies by accident."""
        return bool(self.findings & DELIBERATE)

    def describe(self) -> str:
        if not self.findings:
            return "no obfuscation"
        tag = "deliberate" if self.deliberate else "incidental"
        return f"{tag}: {', '.join(sorted(f.value for f in self.findings))}"

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "findings": sorted(f.value for f in self.findings),
            "deliberate": self.deliberate,
        }


#: Identifier kinds whose values are case-insensitive by specification.
CASE_INSENSITIVE_KINDS = frozenset({
    "domain", "email", "handle", "seller_id", "analytics_id", "url",
    "gravatar_hash", "cert_sha256", "pgp_fpr", "ssh_fpr", "lei",
})


def normalize_value(value: str, *, case_fold: bool = False,
                    collapse_space: bool = True) -> NormalizationResult:
    """Canonicalize a value and record every transformation applied."""
    original = value
    findings: set[Obfuscation] = set()
    s = value

    if _CONTROL.search(s):
        findings.add(Obfuscation.CONTROL_CHAR)
        s = _CONTROL.sub("", s)

    if any(c in s for c in ZERO_WIDTH_CHARS):
        findings.add(Obfuscation.ZERO_WIDTH)
        s = s.translate({ord(c): None for c in ZERO_WIDTH_CHARS})

    if any(c in s for c in BIDI_CHARS):
        findings.add(Obfuscation.BIDI_CONTROL)
        s = s.translate({ord(c): None for c in BIDI_CHARS})

    if any(c in HOMOGLYPHS for c in s):
        findings.add(Obfuscation.HOMOGLYPH)
        s = "".join(HOMOGLYPHS.get(c, c) for c in s)

    if any(c in CONFUSABLE_PUNCT for c in s):
        findings.add(Obfuscation.CONFUSABLE_PUNCT)
        s = "".join(CONFUSABLE_PUNCT.get(c, c) for c in s)

    nfkc = unicodedata.normalize("NFKC", s)
    if nfkc != s:
        findings.add(Obfuscation.COMPATIBILITY)
        s = nfkc

    if collapse_space:
        collapsed = " ".join(s.split())
        if collapsed != s:
            findings.add(Obfuscation.WHITESPACE)
            s = collapsed
    else:
        stripped = s.strip()
        if stripped != s:
            findings.add(Obfuscation.WHITESPACE)
            s = stripped

    if case_fold:
        lowered = s.lower()
        if lowered != s:
            findings.add(Obfuscation.CASE_VARIANT)
            s = lowered

    return NormalizationResult(original, s, findings)


def canonical(value: str, kind: str = "") -> str:
    """Canonical form for keying. The function that closes group inflation."""
    return normalize_value(value, case_fold=kind in CASE_INSENSITIVE_KINDS).normalized


# --------------------------------------------------------------------------- #
# Case-level tracking
# --------------------------------------------------------------------------- #

@dataclass
class ObfuscationLog:
    """Per-case record of what had to be normalized, and where.

    Read this before the conclusions. A run with deliberate findings is a run
    where someone put effort into not being correlated, and that is itself an
    investigative result.
    """

    entries: list[tuple[str, str, NormalizationResult]] = field(default_factory=list)

    def record(self, context: str, kind: str, result: NormalizationResult) -> None:
        if result.findings:
            self.entries.append((context, kind, result))

    @property
    def deliberate(self) -> list[tuple[str, str, NormalizationResult]]:
        return [e for e in self.entries if e[2].deliberate]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, _, r in self.entries:
            for f in r.findings:
                out[f.value] = out.get(f.value, 0) + 1
        return out

    def render(self) -> str:
        if not self.entries:
            return "No obfuscation detected."
        L = [f"{len(self.entries)} identifier(s) required normalization "
             f"({len(self.deliberate)} deliberate).", ""]
        counts = self.counts()
        L.append("| Technique | Count |")
        L.append("|---|---:|")
        for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            marker = " **" if k in {f.value for f in DELIBERATE} else " "
            L.append(f"|{marker}{k}{marker.strip() and '**' or ''} | {n} |")
        if self.deliberate:
            L += ["", "Deliberate obfuscation — these do not occur by accident:", ""]
            for ctx, kind, r in self.deliberate[:20]:
                L.append(f"- `{kind}` in {ctx}: {r.describe()}")
                L.append(f"  `{r.original!r}` → `{r.normalized!r}`")
        return "\n".join(L)


# --------------------------------------------------------------------------- #
# Text-level detection
# --------------------------------------------------------------------------- #

def scan_text(text: str, context: str = "") -> set[Obfuscation]:
    """Detect obfuscation in free text without altering it.

    For prose fields where normalization is not wanted but the presence of
    invisible characters is worth knowing about — an imprint page with bidi
    overrides, a seller name with zero-width padding.
    """
    findings: set[Obfuscation] = set()
    if any(c in text for c in ZERO_WIDTH_CHARS):
        findings.add(Obfuscation.ZERO_WIDTH)
    if any(c in text for c in BIDI_CHARS):
        findings.add(Obfuscation.BIDI_CONTROL)
    if any(c in HOMOGLYPHS for c in text):
        findings.add(Obfuscation.HOMOGLYPH)
    if _CONTROL.search(text):
        findings.add(Obfuscation.CONTROL_CHAR)
    return findings
