"""Calibration measurement and correction.

The model produces probabilities. Until those are checked against known
outcomes, they are an ordering dressed as a measurement — principled, but not
validated. This module is the machinery for checking them.

## The trap that makes most calibration exercises worthless

You will label a few hundred pairs and roughly half will be positive, because
labelling is expensive and nobody hand-labels ten thousand negatives to find
three positives. But operationally the base rate of any two identifiers denoting
the same entity is on the order of 1e-5.

A model measured at 50% prevalence and deployed at 0.001% prevalence will look
far better in the lab than in the field, and the gap is not small — it is orders
of magnitude in precision. Every function here that reports a
prevalence-sensitive metric takes an explicit ``operational_prevalence`` and
corrects for the shift. Reporting uncorrected precision from an enriched sample
is the single most common way calibration studies mislead.

## What to measure

The headline number is not accuracy or AUC. It is **precision within each
reported band** — when the tool says STRONG_EVIDENCE, how often is that right? That is
the question an analyst acting on the output is implicitly asking, and the
question a court would ask about error rate.

Secondary: the reliability diagram (are stated probabilities the observed
frequencies?), expected calibration error, and the Brier decomposition, which
separates being *calibrated* from being *discriminating* — a model can be
perfectly calibrated and useless if it says 0.5 to everything.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .scoring import Band, _raw_band

# --------------------------------------------------------------------------- #
# Labelled data
# --------------------------------------------------------------------------- #

@dataclass
class LabelledPair:
    """One identifier pair with a known outcome.

    ``label`` must come from evidence independent of the model. A pair the model
    linked and an analyst then confirmed by re-reading the same sources is not
    an independent label — it is the model grading its own work.
    """

    a: str
    b: str
    label: bool
    predicted: float
    log_odds: float = 0.0
    band: str = ""
    independent_groups: int = 0
    #: How the label was established. Needed because label quality varies and
    #: the report should say so.
    label_source: str = ""
    #: Case or batch the pair came from. Used to prevent leakage: pairs from one
    #: case are not independent, so they must not straddle a train/test split.
    case_ref: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "a": self.a, "b": self.b, "label": self.label,
            "predicted": self.predicted, "log_odds": self.log_odds,
            "band": self.band, "independent_groups": self.independent_groups,
            "label_source": self.label_source, "case_ref": self.case_ref,
            "notes": self.notes,
        }


def load_corpus(path: str | Path) -> list[LabelledPair]:
    rows = json.loads(Path(path).read_text())
    return [LabelledPair(**r) for r in rows]


def save_corpus(pairs: Sequence[LabelledPair], path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps([x.to_dict() for x in pairs], indent=2))
    return p


# --------------------------------------------------------------------------- #
# Prevalence correction
# --------------------------------------------------------------------------- #

def sample_prevalence(pairs: Sequence[LabelledPair]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for p in pairs if p.label) / len(pairs)


def correct_for_prevalence(
    p_sample: float, sample_prev: float, operational_prev: float
) -> float:
    """Shift a probability from the labelling prevalence to the deployment one.

    Standard prior-shift correction. Assumes the class-conditional likelihoods
    are unchanged between sample and deployment — true when negatives were drawn
    representatively, false if you only labelled *hard* negatives, which is a
    tempting shortcut that invalidates this.
    """
    if not 0 < sample_prev < 1 or not 0 < operational_prev < 1:
        return p_sample
    p_sample = min(max(p_sample, 1e-12), 1 - 1e-12)
    odds = p_sample / (1 - p_sample)
    shift = (operational_prev / (1 - operational_prev)) / (sample_prev / (1 - sample_prev))
    corrected = odds * shift
    return corrected / (1 + corrected)


# --------------------------------------------------------------------------- #
# Reliability
# --------------------------------------------------------------------------- #

@dataclass
class Bin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_frequency: float

    @property
    def gap(self) -> float:
        return self.observed_frequency - self.mean_predicted


def reliability_diagram(
    pairs: Sequence[LabelledPair], bins: int = 10
) -> list[Bin]:
    """Binned predicted-vs-observed. The core calibration artifact.

    A well-calibrated model sits on the diagonal: pairs it scored 0.8 are true
    80% of the time. Points above the diagonal are underconfident, below are
    overconfident. Overconfidence is the dangerous direction here.
    """
    edges = [i / bins for i in range(bins + 1)]
    out: list[Bin] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        sel = [p for p in pairs
               if (lo <= p.predicted < hi) or (hi == 1.0 and p.predicted == 1.0)]
        if not sel:
            continue
        out.append(Bin(
            lower=lo, upper=hi, count=len(sel),
            mean_predicted=sum(p.predicted for p in sel) / len(sel),
            observed_frequency=sum(1 for p in sel if p.label) / len(sel),
        ))
    return out


def expected_calibration_error(pairs: Sequence[LabelledPair], bins: int = 10) -> float:
    """Weighted mean absolute gap between predicted and observed."""
    diagram = reliability_diagram(pairs, bins)
    n = sum(b.count for b in diagram)
    if not n:
        return 0.0
    return sum(b.count * abs(b.gap) for b in diagram) / n


def maximum_calibration_error(pairs: Sequence[LabelledPair], bins: int = 10) -> float:
    diagram = reliability_diagram(pairs, bins)
    return max((abs(b.gap) for b in diagram), default=0.0)


# --------------------------------------------------------------------------- #
# Brier
# --------------------------------------------------------------------------- #

@dataclass
class BrierDecomposition:
    score: float
    reliability: float      # lower is better: calibration error
    resolution: float       # higher is better: discrimination
    uncertainty: float      # base-rate entropy; a property of the data

    def to_dict(self) -> dict:
        return {
            "brier_score": round(self.score, 5),
            "reliability": round(self.reliability, 5),
            "resolution": round(self.resolution, 5),
            "uncertainty": round(self.uncertainty, 5),
            "note": ("score = reliability - resolution + uncertainty. "
                     "A model can be perfectly calibrated (reliability 0) and "
                     "useless (resolution 0) by predicting the base rate for "
                     "everything, which is why both are reported."),
        }


def brier(pairs: Sequence[LabelledPair], bins: int = 10) -> BrierDecomposition:
    if not pairs:
        return BrierDecomposition(0.0, 0.0, 0.0, 0.0)
    n = len(pairs)
    base = sum(1 for p in pairs if p.label) / n
    score = sum((p.predicted - float(p.label)) ** 2 for p in pairs) / n

    diagram = reliability_diagram(pairs, bins)
    reliability = sum(b.count * (b.mean_predicted - b.observed_frequency) ** 2
                      for b in diagram) / n
    resolution = sum(b.count * (b.observed_frequency - base) ** 2
                     for b in diagram) / n
    uncertainty = base * (1 - base)
    return BrierDecomposition(score, reliability, resolution, uncertainty)


# --------------------------------------------------------------------------- #
# Band performance — the headline
# --------------------------------------------------------------------------- #

@dataclass
class BandPerformance:
    band: str
    n: int
    true_positives: int
    precision_sample: float
    precision_operational: float
    recall: float

    def to_dict(self) -> dict:
        return {
            "band": self.band, "n": self.n, "true_positives": self.true_positives,
            "precision_in_sample": round(self.precision_sample, 4),
            "precision_at_operational_prevalence": round(self.precision_operational, 4),
            "recall": round(self.recall, 4),
        }


def band_performance(
    pairs: Sequence[LabelledPair],
    operational_prevalence: float = 1e-5,
) -> list[BandPerformance]:
    """Precision and recall within each reported band.

    ``precision_at_operational_prevalence`` is the number to quote. In-sample
    precision from an enriched labelling set flatters the model, sometimes by
    an order of magnitude.
    """
    sample_prev = sample_prevalence(pairs)
    total_positive = sum(1 for p in pairs if p.label)
    out: list[BandPerformance] = []

    for band in (Band.STRONG_EVIDENCE, Band.MODERATE_EVIDENCE, Band.LIMITED_EVIDENCE,
                 Band.WEAK, Band.UNSUPPORTED):
        sel = [p for p in pairs if (p.band or _raw_band(p.predicted).value) == band.value]
        if not sel:
            continue
        tp = sum(1 for p in sel if p.label)
        prec = tp / len(sel)
        out.append(BandPerformance(
            band=band.value,
            n=len(sel),
            true_positives=tp,
            precision_sample=prec,
            precision_operational=correct_for_prevalence(
                prec, sample_prev, operational_prevalence),
            recall=(tp / total_positive) if total_positive else 0.0,
        ))
    return out


# --------------------------------------------------------------------------- #
# Recalibration
# --------------------------------------------------------------------------- #

@dataclass
class PlattScaler:
    """Logistic recalibration of the log-odds.

    Fits ``p = sigmoid(a * log_odds + b)``. Two parameters, so it works on a few
    hundred labelled pairs where isotonic regression would overfit. It can only
    apply a monotone logistic correction — if the miscalibration is not that
    shape, this will not fix it and the reliability diagram will show the
    residual.
    """

    a: float = 1.0
    b: float = 0.0
    n_fit: int = 0

    def fit(self, pairs: Sequence[LabelledPair], iterations: int = 400,
            lr: float = 0.05) -> PlattScaler:
        xs = [p.log_odds for p in pairs]
        ys = [1.0 if p.label else 0.0 for p in pairs]
        if not xs:
            return self
        scale = max(1.0, max(abs(x) for x in xs))
        a, b = 1.0, 0.0
        for _ in range(iterations):
            ga = gb = 0.0
            for x, y in zip(xs, ys, strict=True):
                z = a * (x / scale) + b
                p = 1.0 / (1.0 + math.exp(-max(min(z, 30), -30)))
                err = p - y
                ga += err * (x / scale)
                gb += err
            a -= lr * ga / len(xs)
            b -= lr * gb / len(xs)
        self.a, self.b, self.n_fit = a / scale, b, len(pairs)
        return self

    def apply(self, log_odds: float) -> float:
        z = max(min(self.a * log_odds + self.b, 30), -30)
        return 1.0 / (1.0 + math.exp(-z))

    def to_dict(self) -> dict:
        return {"method": "platt", "a": round(self.a, 6), "b": round(self.b, 6),
                "n_fit": self.n_fit}


@dataclass
class IsotonicScaler:
    """Non-parametric monotone recalibration via pool-adjacent-violators.

    Strictly more flexible than Platt and correspondingly hungrier: it needs
    low thousands of labelled pairs before it stops memorising the sample.
    Prefer Platt below ~1000 labels.
    """

    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    n_fit: int = 0

    def fit(self, pairs: Sequence[LabelledPair]) -> IsotonicScaler:
        data = sorted(((p.log_odds, 1.0 if p.label else 0.0) for p in pairs),
                      key=lambda t: t[0])
        if not data:
            return self
        xs = [x for x, _ in data]
        vals = [y for _, y in data]
        weights = [1.0] * len(vals)

        i = 0
        while i < len(vals) - 1:
            if vals[i] <= vals[i + 1]:
                i += 1
                continue
            w = weights[i] + weights[i + 1]
            v = (vals[i] * weights[i] + vals[i + 1] * weights[i + 1]) / w
            vals[i:i + 2] = [v]
            weights[i:i + 2] = [w]
            xs[i:i + 2] = [xs[i]]
            i = max(i - 1, 0)

        self.xs, self.ys, self.n_fit = xs, vals, len(pairs)
        return self

    def apply(self, log_odds: float) -> float:
        if not self.xs:
            return 1.0 / (1.0 + math.exp(-max(min(log_odds, 30), -30)))
        if log_odds <= self.xs[0]:
            return self.ys[0]
        if log_odds >= self.xs[-1]:
            return self.ys[-1]
        for i in range(len(self.xs) - 1):
            if self.xs[i] <= log_odds <= self.xs[i + 1]:
                return self.ys[i]
        return self.ys[-1]

    def to_dict(self) -> dict:
        return {"method": "isotonic", "knots": len(self.xs), "n_fit": self.n_fit}


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #

def split_by_case(
    pairs: Sequence[LabelledPair], test_fraction: float = 0.3, seed: int = 0
) -> tuple[list[LabelledPair], list[LabelledPair]]:
    """Split on ``case_ref``, never on individual pairs.

    Pairs from one investigation share evidence, sources and often the same
    underlying entity. Splitting at the pair level leaks: the model sees the
    same facts in training and test and reports an accuracy it will not
    reproduce on a new case.
    """
    import random

    cases = sorted({p.case_ref or f"__{i}" for i, p in enumerate(pairs)})
    rng = random.Random(seed)
    rng.shuffle(cases)
    n_test = max(1, int(len(cases) * test_fraction))
    test_cases = set(cases[:n_test])

    test, train = [], []
    for i, p in enumerate(pairs):
        (test if (p.case_ref or f"__{i}") in test_cases else train).append(p)
    return train, test


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def calibration_report(
    pairs: Sequence[LabelledPair],
    operational_prevalence: float = 1e-5,
    bins: int = 10,
) -> str:
    if not pairs:
        return "# Calibration report\n\nNo labelled pairs supplied."

    sp = sample_prevalence(pairs)
    ece = expected_calibration_error(pairs, bins)
    mce = maximum_calibration_error(pairs, bins)
    bd = brier(pairs, bins)
    diagram = reliability_diagram(pairs, bins)
    bands = band_performance(pairs, operational_prevalence)
    sources = sorted({p.label_source for p in pairs if p.label_source})
    cases = len({p.case_ref for p in pairs if p.case_ref})

    L = [
        "# Calibration report", "",
        f"- Labelled pairs: **{len(pairs)}** across {cases or 'unrecorded'} case(s)",
        f"- Sample prevalence: **{sp:.1%}** positive",
        f"- Operational prevalence assumed: **{operational_prevalence:.1e}**",
        f"- Label sources: {', '.join(sources) if sources else 'unrecorded'}",
        "",
        "## Headline",
        "",
        "| Band | n | TP | Precision (sample) | Precision (operational) | Recall |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for b in bands:
        L.append(f"| {b.band} | {b.n} | {b.true_positives} | "
                 f"{b.precision_sample:.3f} | **{b.precision_operational:.3f}** | "
                 f"{b.recall:.3f} |")

    L += [
        "",
        "The operational column is the one to quote. Sample precision comes from "
        "a labelling set enriched with positives and overstates field performance, "
        "often by an order of magnitude.",
        "",
        "## Calibration",
        "",
        f"- Expected calibration error: **{ece:.4f}**",
        f"- Maximum calibration error: **{mce:.4f}**",
        "",
        "| Predicted range | n | Mean predicted | Observed frequency | Gap |",
        "|---|---:|---:|---:|---:|",
    ]
    for b in diagram:
        arrow = "over-confident" if b.gap < -0.05 else (
            "under-confident" if b.gap > 0.05 else "")
        L.append(f"| {b.lower:.1f}–{b.upper:.1f} | {b.count} | "
                 f"{b.mean_predicted:.3f} | {b.observed_frequency:.3f} | "
                 f"{b.gap:+.3f} {arrow} |")

    L += ["", "## Brier decomposition", "",
          f"- Score: **{bd.score:.4f}** (lower better)",
          f"- Reliability: {bd.reliability:.4f} (lower better — calibration error)",
          f"- Resolution: {bd.resolution:.4f} (higher better — discrimination)",
          f"- Uncertainty: {bd.uncertainty:.4f} (property of the data, not the model)",
          "",
          "Score = reliability − resolution + uncertainty. A model that predicts "
          "the base rate for every pair is perfectly calibrated and completely "
          "useless, which is why resolution is reported alongside.",
          "",
          "## Interpretation notes", "",
          "- Over-confidence (negative gap) is the dangerous direction: the model "
          "asserting more than the evidence supports.",
          "- Bins with fewer than ~30 pairs are noisy; do not tune against them.",
          "- If these figures are quoted in a report or filing, state the sample "
          "size, the label sources, and the operational prevalence assumed. A "
          "precision figure without its prevalence assumption is not a claim "
          "anyone can check.",
          ]
    return "\n".join(L)
