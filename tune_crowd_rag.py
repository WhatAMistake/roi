"""Temporary parameter sweep for hybrid Crowd Association RAG v2 scoring (not committed)."""

from __future__ import annotations

import contextlib
import io
import itertools
import sys

from evaluate_crowd_semantic import SemanticLOROEvaluator
from src.crowd_association_service import EvidenceWeightConfig


DEFAULT_GRID = [(0.45, 2.0, 1.0, 0.20)]


def parse_grid(spec: str) -> list[tuple[float, float, float, float, float, float]]:
    """Parses 'floor,boost,decay,minthr[,margin[,strong]];...' groups."""
    grid = []
    for group in spec.split(";"):
        group = group.strip()
        if not group:
            continue
        parts = [float(p) for p in group.split(",")]
        while len(parts) < 6:
            parts.append(0.15 if len(parts) == 4 else 0.30)
        grid.append(tuple(parts))
    return grid


def main() -> None:
    spec = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg_grid = parse_grid(spec) if spec else list(
        itertools.product([0.30, 0.40, 0.45, 0.50], [1.0, 2.0, 3.0], [1.0], [0.30])
    )
    ev = SemanticLOROEvaluator()

    print(f"{'floor':>6} {'boost':>6} {'decay':>6} {'minthr':>7} {'margin':>6} {'strong':>6} | "
          f"{'cov':>6} {'undet':>6} {'acc_over':>8} {'acc_det':>8} {'f1':>6} | per-given")
    for sim_floor, boost, decay, min_thr, margin, strong in cfg_grid:
        cfg = EvidenceWeightConfig()
        cfg.similarity_floor = sim_floor
        cfg.exact_match_boost = boost
        cfg.rank_decay = decay
        cfg.min_confidence_threshold = min_thr
        cfg.margin_threshold = margin
        cfg.strong_margin_threshold = strong
        ev.config = cfg
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = ev.run_evaluation()
        pg = " ".join(f"{g[:3]}={res['per_given_accuracy'][g]:.2f}" for g in res["labels"])
        print(f"{sim_floor:>6} {boost:>6} {decay:>6} {min_thr:>7} {margin:>6} {strong:>6} | "
              f"{res['coverage']:>6.3f} {res['undetermined_rate']:>6.3f} "
              f"{res['accuracy_overall']:>8.3f} {res['accuracy_determined']:>8.3f} "
              f"{res['macro_f1_determined']:>6.3f} | {pg}", flush=True)


if __name__ == "__main__":
    main()
