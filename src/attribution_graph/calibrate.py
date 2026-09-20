"""Calibration CLI.

    python -m attribution_graph.calibrate report --corpus pairs.json
    python -m attribution_graph.calibrate fit    --corpus pairs.json --method platt
    python -m attribution_graph.calibrate split  --corpus pairs.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .calibration import (
    IsotonicScaler,
    PlattScaler,
    calibration_report,
    expected_calibration_error,
    load_corpus,
    sample_prevalence,
    save_corpus,
    split_by_case,
)


def _report(a: argparse.Namespace) -> int:
    pairs = load_corpus(a.corpus)
    if a.holdout:
        _, pairs = split_by_case(pairs, a.test_fraction, a.seed)
        print(f"measuring on {len(pairs)} held-out pair(s)", file=sys.stderr)
    out = calibration_report(pairs, a.operational_prevalence, a.bins)
    if a.out:
        Path(a.out).write_text(out)
        print(a.out)
    else:
        print(out)
    return 0


def _fit(a: argparse.Namespace) -> int:
    pairs = load_corpus(a.corpus)
    train, test = split_by_case(pairs, a.test_fraction, a.seed)
    if not train or not test:
        print("corpus has too few distinct cases to split", file=sys.stderr)
        return 1

    method = a.method
    if method == "auto":
        # Isotonic memorises below roughly a thousand labels.
        method = "isotonic" if len(train) >= 1000 else "platt"
        print(f"auto-selected {method} for {len(train)} training pair(s)",
              file=sys.stderr)

    scaler = (IsotonicScaler() if method == "isotonic" else PlattScaler()).fit(train)

    before = expected_calibration_error(test, a.bins)
    for p in test:
        p.predicted = scaler.apply(p.log_odds)
    after = expected_calibration_error(test, a.bins)

    print(json.dumps({
        "method": method,
        "train_pairs": len(train), "test_pairs": len(test),
        "train_prevalence": round(sample_prevalence(train), 4),
        "ece_before": round(before, 5),
        "ece_after": round(after, 5),
        "improved": after < before,
        "parameters": scaler.to_dict(),
        "note": ("Report both raw and recalibrated figures. Shipping only the "
                 "corrected numbers hides how far off the model was."),
    }, indent=2))

    if a.save:
        Path(a.save).write_text(json.dumps(scaler.to_dict(), indent=2))
    return 0


def _split(a: argparse.Namespace) -> int:
    pairs = load_corpus(a.corpus)
    train, test = split_by_case(pairs, a.test_fraction, a.seed)
    save_corpus(train, a.train_out)
    save_corpus(test, a.test_out)
    print(f"{len(train)} train / {len(test)} test, split by case_ref")
    print(f"  {a.train_out}\n  {a.test_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="attribution-graph.calibrate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = dict(type=float)
    r = sub.add_parser("report")
    r.add_argument("--corpus", required=True)
    r.add_argument("--operational-prevalence", default=1e-5, **common)
    r.add_argument("--bins", type=int, default=10)
    r.add_argument("--holdout", action="store_true",
                   help="measure only on the held-out split")
    r.add_argument("--test-fraction", default=0.3, **common)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--out", default="")
    r.set_defaults(func=_report)

    f = sub.add_parser("fit")
    f.add_argument("--corpus", required=True)
    f.add_argument("--method", choices=["auto", "platt", "isotonic"], default="auto")
    f.add_argument("--test-fraction", default=0.3, **common)
    f.add_argument("--seed", type=int, default=7)
    f.add_argument("--bins", type=int, default=10)
    f.add_argument("--save", default="")
    f.set_defaults(func=_fit)

    s = sub.add_parser("split")
    s.add_argument("--corpus", required=True)
    s.add_argument("--test-fraction", default=0.3, **common)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--train-out", default="train.json")
    s.add_argument("--test-out", default="test.json")
    s.set_defaults(func=_split)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
