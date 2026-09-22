"""Name variant generation across scripts and romanization schemes.

The requirement that shapes this module: **when two names match through
transliteration, the report must state the exact transform chain that produced
the match.** A bare "these matched" is unreviewable. ``Vemuganti`` matching
``Vemugunti`` via one vowel substitution is a different claim from
``Мосэнерго`` matching ``Mosenergo`` via BGN/PCGN romanization, and a reviewing
analyst needs to see which happened.

So every variant carries the ordered list of named rules that produced it, and
``match()`` returns that chain.

Coverage is rule-based rather than statistical. That is a deliberate trade:
rules are auditable and explainable in a report, and a learned model that cannot
say *why* two names matched is unusable for the purpose this serves.

## Scope of the rules

- Latin: diacritic folding, doubled consonants, common orthographic variants
- Indic (Devanagari/Telugu/Tamil source, romanized): aspirate collapse, vowel
  length, v/w, retroflex, ksh/x, given/family order inversion
- Cyrillic: BGN/PCGN, ISO 9, GOST 7.79, ALA-LC, plus the ё/е and й/i variants
  that produce most real-world mismatches
- Arabic: definite-article attachment, ou/u, kh/ch, q/k, ayn/hamza dropping
- CJK: Pinyin/Wade-Giles, Hepburn/Kunrei, Revised Romanization/McCune-Reischauer

Rules are conservative by design. Over-generating variants is not free: each one
is a candidate that can collide with an unrelated real person, so the selectivity
of a matched variant is reported alongside the chain.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

# --------------------------------------------------------------------------- #
# Rule registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Rule:
    name: str
    script: str
    description: str
    apply: Callable[[str], set[str]]
    #: Whether this rule produces forms worth *querying a registry with*.
    #:
    #: Matching and lookup need different nets. Matching two names already in
    #: hand should cast wide -- a speculative vowel swap costs nothing and may
    #: recover a real link. Querying spends a request per form and risks
    #: colliding with an unrelated real person, so it needs the conservative
    #: set: transliteration schemes, diacritic folding, name ordering.
    #: Speculative substitutions are matching-only.
    lookup_safe: bool = True

    def __call__(self, s: str) -> set[str]:
        return {v for v in self.apply(s) if v and v != s}


_RULES: list[Rule] = []


def rule(name: str, script: str, description: str, *, lookup_safe: bool = True):
    def deco(fn: Callable[[str], set[str]]) -> Rule:
        r = Rule(name, script, description, fn, lookup_safe)
        _RULES.append(r)
        return r
    return deco


def _subs(s: str, pairs: Iterable[tuple[str, str]]) -> set[str]:
    """Apply each substitution independently, both directions, case-insensitively.

    Case matters here: matching case-sensitively silently missed every rule
    whose trigger fell at the start of a capitalised name -- "zh" never fired on
    "Zhang", so Pinyin/Wade-Giles pairs went undetected.
    """
    low = s.lower()
    out: set[str] = set()
    for a, b in pairs:
        if a in low:
            out.add(low.replace(a, b))
        if b in low:
            out.add(low.replace(b, a))
    return out


# ---- Latin ---------------------------------------------------------------- #

@rule("fold_diacritics", "latin", "Strip combining marks (Müller -> Muller)")
def _fold(s: str) -> set[str]:
    n = unicodedata.normalize("NFKD", s)
    return {"".join(c for c in n if not unicodedata.combining(c))}


@rule("german_umlaut_expand", "latin", "ü -> ue, ö -> oe, ä -> ae, ß -> ss")
def _umlaut(s: str) -> set[str]:
    t = s
    for a, b in (("ü", "ue"), ("ö", "oe"), ("ä", "ae"), ("ß", "ss"),
                 ("Ü", "Ue"), ("Ö", "Oe"), ("Ä", "Ae")):
        t = t.replace(a, b)
    return {t}


@rule("double_consonant", "latin", "Collapse or double consonants (Filipe/Filippe)",
      lookup_safe=False)
def _double(s: str) -> set[str]:
    out = {re.sub(r"([bcdfglmnprstz])\1", r"\1", s)}
    for m in re.finditer(r"(?<![bcdfglmnprstz])([bcdfglmnprstz])(?![bcdfglmnprstz])", s):
        i = m.start(1)
        out.add(s[:i] + s[i] * 2 + s[i + 1:])
    return out


@rule("latin_orthographic", "latin", "c/k, s/z, ph/f, y/i, x/ks", lookup_safe=False)
def _latin_orth(s: str) -> set[str]:
    return _subs(s, [("c", "k"), ("s", "z"), ("ph", "f"), ("y", "i"), ("x", "ks")])


# ---- Indic ---------------------------------------------------------------- #

@rule("indic_aspirate", "indic", "Aspirate collapse: th/t, dh/d, bh/b, kh/k, gh/g, ph/f",
      lookup_safe=False)
def _aspirate(s: str) -> set[str]:
    return _subs(s, [("th", "t"), ("dh", "d"), ("bh", "b"), ("kh", "k"),
                     ("gh", "g"), ("ph", "f"), ("ch", "c"), ("jh", "j")])


@rule("indic_vowel_length", "indic", "aa/a, ee/i, oo/u, ii/i, uu/u", lookup_safe=False)
def _vowel_len(s: str) -> set[str]:
    return _subs(s, [("aa", "a"), ("ee", "i"), ("oo", "u"), ("ii", "i"),
                     ("uu", "u"), ("ai", "ay"), ("au", "ow")])


@rule("indic_v_w", "indic", "v/w interchange (Vemuganti / Wemuganti)", lookup_safe=False)
def _v_w(s: str) -> set[str]:
    return _subs(s, [("v", "w")])


@rule("indic_retroflex", "indic", "Retroflex: t/tt, d/dd, n/nn, l/ll", lookup_safe=False)
def _retroflex(s: str) -> set[str]:
    return _subs(s, [("tt", "t"), ("dd", "d"), ("nn", "n"), ("ll", "l")])


@rule("indic_ksh", "indic", "ksh / x / ksh cluster (Lakshmi / Laxmi)", lookup_safe=False)
def _ksh(s: str) -> set[str]:
    return _subs(s, [("ksh", "x"), ("ksh", "ksh"), ("sh", "s")])


@rule("indic_final_a", "indic", "Schwa deletion: trailing -a optional (Rama / Ram)",
      lookup_safe=False)
def _final_a(s: str) -> set[str]:
    out = set()
    if s.endswith("a") and len(s) > 3:
        out.add(s[:-1])
    else:
        out.add(s + "a")
    return out


@rule("indic_vowel_quality", "indic",
      "Unstressed vowel quality: a/u, a/o, i/e, u/o at non-initial positions "
      "(Vemuganti / Vemugunti)", lookup_safe=False)
def _vowel_quality(s: str) -> set[str]:
    out: set[str] = set()
    swaps = {"a": "uo", "u": "ao", "i": "e", "e": "i", "o": "au"}
    for i, ch in enumerate(s.lower()):
        if i == 0 or ch not in swaps:
            continue
        # Only inside a word: leading vowels carry more information and
        # swapping them generates collisions with unrelated names.
        for repl in swaps[ch]:
            out.add(s[:i] + repl + s[i + 1:])
    return out


@rule("name_order_swap", "any", "Given/family order inversion (Kumar Ravi / Ravi Kumar)")
def _swap(s: str) -> set[str]:
    parts = s.split()
    if len(parts) != 2:
        return set()
    return {f"{parts[1]} {parts[0]}"}


@rule("initial_expand", "any", "Initial vs full token (T Karumudi / Tushar Karumudi)")
def _initials(s: str) -> set[str]:
    parts = s.split()
    if len(parts) < 2:
        return set()
    return {" ".join([p[0] for p in parts[:-1]] + [parts[-1]]),
            " ".join([p[0] + "." for p in parts[:-1]] + [parts[-1]])}


# ---- Cyrillic ------------------------------------------------------------- #

_CYR_BGN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_CYR_ISO9 = {**_CYR_BGN, "ё": "yo", "х": "h", "щ": "sc", "ю": "ju", "я": "ja",
             "ж": "z", "ч": "c"}
_CYR_ALA = {**_CYR_BGN, "ё": "e", "й": "i", "ю": "iu", "я": "ia", "ц": "ts"}


def _translit_map(s: str, table: dict[str, str]) -> str:
    return "".join(table.get(c, table.get(c.lower(), c)) for c in s)


@rule("cyrillic_bgn_pcgn", "cyrillic", "BGN/PCGN romanization")
def _cyr_bgn(s: str) -> set[str]:
    return {_translit_map(s, _CYR_BGN)}


@rule("cyrillic_iso9", "cyrillic", "ISO 9 romanization")
def _cyr_iso(s: str) -> set[str]:
    return {_translit_map(s, _CYR_ISO9)}


@rule("cyrillic_ala_lc", "cyrillic", "ALA-LC romanization")
def _cyr_ala(s: str) -> set[str]:
    return {_translit_map(s, _CYR_ALA)}


@rule("cyrillic_yo_e", "cyrillic", "ё/е and й/i collapse — the commonest mismatch",
      lookup_safe=False)
def _cyr_yo(s: str) -> set[str]:
    return _subs(s, [("yo", "e"), ("iy", "y"), ("ii", "i"), ("kh", "h")])


# ---- Arabic --------------------------------------------------------------- #

@rule("arabic_article", "arabic", "Definite article: al-/el-/ul- attached or detached",
      lookup_safe=False)
def _ar_article(s: str) -> set[str]:
    out = set()
    # Word-initial only. Matching "al" anywhere strips letters out of unrelated
    # names -- it was turning "Zhang" into "Zhng" and manufacturing collisions.
    for m in re.finditer(r"\b(al|el|ul)[- ]", s, flags=re.I):
        out.add(s[:m.start()] + s[m.end():])
        out.add(s[:m.start()] + "al" + s[m.end():])
    if not re.match(r"^(al|el)[- ]", s, flags=re.I):
        out.add("al-" + s)
    return out


@rule("arabic_vowel", "arabic",
      "Short-vowel ambiguity: u/o, a/e, i/e (Muhammad / Mohammed / Mohamad)", lookup_safe=False)
def _ar_vowel(s: str) -> set[str]:
    out: set[str] = set()
    swaps = {"u": "o", "o": "u", "a": "e", "e": "a", "i": "e"}
    for i, ch in enumerate(s.lower()):
        if ch in swaps:
            for r in swaps[ch]:
                out.add(s[:i] + r + s[i + 1:])
    return out


@rule("arabic_orthographic", "arabic", "ou/u, kh/ch, q/k, dropped ayn and hamza", lookup_safe=False)
def _ar_orth(s: str) -> set[str]:
    out = _subs(s, [("ou", "u"), ("kh", "ch"), ("q", "k"), ("aa", "a"), ("ee", "i")])
    out.add(s.replace("'", "").replace("`", "").replace("ʿ", ""))
    return out


# ---- CJK ------------------------------------------------------------------ #

@rule("pinyin_wade_giles", "cjk", "Pinyin / Wade-Giles (Zhang/Chang, Qing/Ching)",
      lookup_safe=False)
def _wg(s: str) -> set[str]:
    return _subs(s, [("zh", "ch"), ("q", "ch"), ("x", "hs"), ("j", "ch"),
                     ("z", "ts"), ("c", "ts'"), ("b", "p"), ("d", "t"), ("g", "k")])


@rule("hepburn_kunrei", "cjk", "Hepburn / Kunrei-shiki (shi/si, chi/ti, tsu/tu, fu/hu)",
      lookup_safe=False)
def _kunrei(s: str) -> set[str]:
    return _subs(s, [("shi", "si"), ("chi", "ti"), ("tsu", "tu"), ("fu", "hu"),
                     ("ji", "zi"), ("sha", "sya"), ("cho", "tyo"), ("jo", "zyo")])


@rule("korean_rr_mr", "cjk", "Revised Romanization / McCune-Reischauer (Busan/Pusan)",
      lookup_safe=False)
def _kr(s: str) -> set[str]:
    return _subs(s, [("b", "p"), ("d", "t"), ("g", "k"), ("j", "ch"),
                     ("eo", "o"), ("eu", "u"), ("oe", "oi")])


# --------------------------------------------------------------------------- #
# Variant generation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Variant:
    value: str
    chain: tuple[str, ...]

    @property
    def depth(self) -> int:
        return len(self.chain)

    def describe(self) -> str:
        if not self.chain:
            return "as written"
        return " -> ".join(self.chain)


#: Rules applied per pass. Depth 2 is the practical ceiling: depth 3 generates
#: thousands of variants per name and the marginal ones collide with unrelated
#: real people, which costs more than the recall gains.
MAX_DEPTH = 2

#: Cap on generated variants per name. Exceeding this means the name is too
#: short or too generic for variant expansion to be meaningful.
MAX_VARIANTS = 400


def detect_scripts(s: str) -> set[str]:
    scripts = set()
    for ch in s:
        if not ch.isalpha():
            continue
        try:
            n = unicodedata.name(ch)
        except ValueError:
            continue
        if "CYRILLIC" in n:
            scripts.add("cyrillic")
        elif "ARABIC" in n:
            scripts.add("arabic")
        elif any(k in n for k in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            scripts.add("cjk")
        elif any(k in n for k in ("DEVANAGARI", "TELUGU", "TAMIL", "BENGALI",
                                  "GUJARATI", "KANNADA", "MALAYALAM", "GURMUKHI")):
            scripts.add("indic")
        elif "LATIN" in n:
            scripts.add("latin")
    return scripts or {"latin"}


def applicable_rules(s: str, hint: str | None = None) -> list[Rule]:
    scripts = detect_scripts(s)
    if hint:
        scripts.add(hint)
    # Romanized Indic, Arabic and CJK names are written in Latin script, so
    # script detection alone cannot select their rules. Without a hint, apply
    # the romanization families too -- costlier, but a missed variant is a
    # missed identification.
    if scripts == {"latin"} and not hint:
        scripts |= {"indic", "arabic", "cjk"}
    return [r for r in _RULES if r.script in scripts or r.script == "any"]


@lru_cache(maxsize=4096)
def variants(name: str, hint: str | None = None, max_depth: int = MAX_DEPTH) -> tuple[Variant, ...]:
    """All variants of a name, each carrying the rule chain that produced it."""
    base = " ".join(name.split())
    rules = applicable_rules(base, hint)

    # Work in lowercase throughout; matching is case-insensitive and mixing
    # cases across passes multiplies the variant set for no benefit.
    seen: dict[str, tuple[str, ...]] = {base.lower(): ()}
    frontier = [(base.lower(), ())]

    for _ in range(max_depth):
        nxt: list[tuple[str, tuple[str, ...]]] = []
        for value, chain in frontier:
            for r in rules:
                if r.name in chain:
                    continue
                for v in r(value):
                    k = v.lower()
                    if k in seen or len(seen) >= MAX_VARIANTS:
                        continue
                    c = chain + (r.name,)
                    seen[k] = c
                    nxt.append((v, c))
            if len(seen) >= MAX_VARIANTS:
                break
        frontier = nxt
        if not frontier:
            break

    return tuple(Variant(v, c) for v, c in seen.items())


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

@dataclass
class NameMatch:
    a: str
    b: str
    matched: bool
    matched_form: str = ""
    chain_a: tuple[str, ...] = ()
    chain_b: tuple[str, ...] = ()
    total_depth: int = 0
    note: str = ""

    def describe(self) -> str:
        """Human-readable provenance for the report.

        This string is the point of the module -- it is what an analyst reads to
        decide whether to accept the match.
        """
        if not self.matched:
            return f"no transliteration path between '{self.a}' and '{self.b}'"
        if self.total_depth == 0:
            return "exact match as written"
        parts = []
        if self.chain_a:
            parts.append(f"'{self.a}' via {' -> '.join(self.chain_a)}")
        if self.chain_b:
            parts.append(f"'{self.b}' via {' -> '.join(self.chain_b)}")
        return (f"matched at '{self.matched_form}': " + "; ".join(parts) +
                f" ({self.total_depth} transform{'s' if self.total_depth != 1 else ''})")

    def to_dict(self) -> dict:
        return {
            "a": self.a, "b": self.b, "matched": self.matched,
            "matched_form": self.matched_form,
            "transform_chain_a": list(self.chain_a),
            "transform_chain_b": list(self.chain_b),
            "total_transforms": self.total_depth,
            "provenance": self.describe(),
        }


#: Below this length, variant expansion produces collisions with unrelated names
#: at a rate that makes any match meaningless.
MIN_NAME_LENGTH = 4


def match(a: str, b: str, hint: str | None = None,
          max_depth: int = MAX_DEPTH) -> NameMatch:
    """Find the shortest transform path connecting two names, if one exists."""
    na, nb = " ".join(a.split()), " ".join(b.split())
    if na.lower() == nb.lower():
        return NameMatch(a, b, True, na, (), (), 0)

    if min(len(na.replace(" ", "")), len(nb.replace(" ", ""))) < MIN_NAME_LENGTH:
        return NameMatch(a, b, False, note="name too short for variant expansion")

    va = {v.value.lower(): v.chain for v in variants(na, hint, max_depth)}
    vb = {v.value.lower(): v.chain for v in variants(nb, hint, max_depth)}

    common = set(va) & set(vb)
    if not common:
        return NameMatch(a, b, False)

    # A match is only meaningful if the shared form retains most of the
    # information in the originals. Without this guard, two rules that each
    # shorten a name can meet in the middle at a stub ("a wei") and report a
    # match between unrelated people.
    def _letters(x: str) -> int:
        return sum(c.isalpha() for c in x)

    floor = 0.6 * min(_letters(na), _letters(nb))
    viable = [k for k in common if _letters(k) >= floor]
    if not viable:
        return NameMatch(a, b, False,
                         note="only degenerate matches found; both names collapse "
                              "to a stub that retains too little to discriminate")

    best = min(viable, key=lambda k: (len(va[k]) + len(vb[k]), -_letters(k), k))
    ca, cb = va[best], vb[best]
    return NameMatch(a, b, True, best, ca, cb, len(ca) + len(cb))


def rule_catalog() -> list[dict[str, str]]:
    """Every rule, for documentation and report appendices."""
    return [{"name": r.name, "script": r.script, "description": r.description}
            for r in sorted(_RULES, key=lambda x: (x.script, x.name))]


# --------------------------------------------------------------------------- #
# Bounded variants for registry lookups
# --------------------------------------------------------------------------- #

#: Default cap for lookup variants. Full expansion produces hundreds of forms,
#: which is right for *matching* two known names and wrong for *querying* a
#: registry: each variant is a request, and the marginal ones collide with
#: unrelated real people.
LOOKUP_LIMIT = 12


def lookup_variants(name: str, limit: int = LOOKUP_LIMIT,
                    hint: str | None = None) -> list[str]:
    """Name forms to query a registry or index with, shortest chain first.

    This is the canonical implementation. Downstream packages must not
    reimplement name variation -- two independent versions drift, and a lookup
    that silently uses the weaker one misses records the stronger one would
    find.

    Ordered by transform depth so the most conservative forms are tried first,
    and always includes the name as written.
    """
    base = " ".join(name.split())
    if not base:
        return []

    safe = {r.name for r in _RULES if r.lookup_safe}
    ranked = sorted(
        (v for v in variants(base, hint) if all(c in safe for c in v.chain)),
        key=lambda v: (v.depth, v.value))
    out: list[str] = [base]
    seen = {base.lower()}
    for v in ranked:
        if len(out) >= limit:
            break
        if v.value.lower() in seen:
            continue
        seen.add(v.value.lower())
        out.append(v.value)
    return out


def variant_provenance(name: str, matched: str) -> str:
    """How ``matched`` was reached from ``name``, for the report.

    A lookup that hit on a transformed variant has to say which transform, or a
    reviewer cannot judge whether the match is sound.
    """
    m = match(name, matched)
    return m.describe()
