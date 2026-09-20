"""Calibration measurement."""

import random

import pytest

from attribution_graph import (
    IsotonicScaler,
    LabelledPair,
    PlattScaler,
    band_performance,
    brier,
    calibration_report,
    correct_for_prevalence,
    expected_calibration_error,
    reliability_diagram,
    split_by_case,
)


def _pairs(n=400, seed=0, calibrated=True, overconfident=False):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.random()
        true_p = p if calibrated else (p * 0.6 if overconfident else min(p * 1.4, 1.0))
        label = rng.random() < true_p
        out.append(LabelledPair(
            a=f"id:{i}a", b=f"id:{i}b", label=label, predicted=p,
            log_odds=0.0 if p in (0, 1) else __import__("math").log(p / (1 - p)),
            case_ref=f"case{i % 20}", label_source="synthetic",
        ))
    return out


def test_reliability_diagram_bins_cover_the_data():
    d = reliability_diagram(_pairs())
    assert sum(b.count for b in d) == 400
    assert all(0 <= b.observed_frequency <= 1 for b in d)


def test_calibrated_model_has_low_ece():
    assert expected_calibration_error(_pairs(2000, calibrated=True)) < 0.08


def test_overconfident_model_is_detected():
    """The dangerous direction: model asserts more than the evidence supports."""
    d = reliability_diagram(_pairs(2000, calibrated=False, overconfident=True))
    high = [b for b in d if b.mean_predicted > 0.6]
    assert high and sum(b.gap for b in high) / len(high) < -0.1


def test_prevalence_correction_reduces_precision():
    """The core trap: lab prevalence flatters the model."""
    corrected = correct_for_prevalence(0.95, sample_prev=0.5, operational_prev=1e-5)
    assert corrected < 0.95
    assert corrected < 0.01


def test_prevalence_correction_is_identity_when_prevalences_match():
    assert correct_for_prevalence(0.9, 0.3, 0.3) == pytest.approx(0.9, abs=1e-9)


def test_band_performance_reports_both_precisions():
    perf = band_performance(_pairs(1000), operational_prevalence=1e-5)
    assert perf
    for b in perf:
        assert b.precision_operational <= b.precision_sample + 1e-9


def test_brier_decomposition_identity_holds():
    p = _pairs(1000)
    bd = brier(p)
    assert bd.score == pytest.approx(
        bd.reliability - bd.resolution + bd.uncertainty, abs=0.02)


def test_constant_predictor_is_calibrated_but_has_no_resolution():
    """Perfectly calibrated and completely useless -- why both are reported."""
    base = 0.3
    rng = random.Random(1)
    p = [LabelledPair(a=f"{i}", b=f"{i}", label=rng.random() < base,
                      predicted=base, log_odds=0.0) for i in range(2000)]
    bd = brier(p)
    assert bd.resolution < 0.01
    assert bd.reliability < 0.01


def test_split_is_by_case_not_by_pair():
    """Pairs from one case share evidence; splitting on pairs leaks."""
    p = _pairs(400)
    train, test = split_by_case(p, test_fraction=0.3, seed=7)
    assert train and test
    assert not ({x.case_ref for x in train} & {x.case_ref for x in test})


def test_platt_improves_a_miscalibrated_model():
    train = _pairs(1500, seed=2, calibrated=False, overconfident=True)
    scaler = PlattScaler().fit(train)
    before = expected_calibration_error(train)
    for x in train:
        x.predicted = scaler.apply(x.log_odds)
    assert expected_calibration_error(train) <= before


def test_isotonic_is_monotone():
    s = IsotonicScaler().fit(_pairs(1500, seed=3))
    xs = [-8, -4, -1, 0, 1, 4, 8]
    ys = [s.apply(x) for x in xs]
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))


def test_report_states_its_prevalence_assumption():
    out = calibration_report(_pairs(500), operational_prevalence=1e-5)
    assert "Operational prevalence assumed" in out
    assert "overstates field performance" in out
    assert "not a claim anyone can check" in out


def test_empty_corpus_does_not_crash():
    assert "No labelled pairs" in calibration_report([])
