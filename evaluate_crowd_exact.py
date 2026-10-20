"""
Exact Lexical Baseline Evaluator for Crowd Associations (Leave-One-Respondent-Out).

Validates the crowdsourced associative field baseline without embeddings:
python evaluate_crowd_exact.py [path_to_dataset_v2.json]
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

GIVENS = ["freedom", "nonsense", "solitude", "death"]


def normalize_association_term(term: str) -> str:
    """Canonical normalization for association terms."""
    if not term:
        return ""
    t = term.lower().replace("ё", "е").strip()
    return re.sub(r"^[^\w]+|[^\w]+$", "", t)


def evaluate_exact_loro(dataset_path: str | Path) -> Dict[str, Any]:
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        # Fallback to data/dataset_v2.json if passed filename only
        fallback = Path(__file__).resolve().parent / "data" / dataset_path.name
        if fallback.exists():
            dataset_path = fallback
        else:
            raise FileNotFoundError(f"Dataset not found at {dataset_path}")

    with open(dataset_path, "r", encoding="utf-8") as f:
        records: List[Dict[str, Any]] = json.load(f)

    # Collect samples: (respondent_id, true_given, canonical_term)
    # Deduplicated per respondent per given
    samples: List[Tuple[int, str, str]] = []
    for r in records:
        rid = r["id"]
        associations = r.get("associations", {})
        for g in GIVENS:
            seen_terms: Set[str] = set()
            for raw in associations.get(g, []):
                norm = normalize_association_term(raw)
                if norm and norm not in seen_terms:
                    seen_terms.add(norm)
                    samples.append((rid, g, norm))

    # Inverted index: term -> given -> set(respondent_ids)
    word_index: Dict[str, Dict[str, Set[int]]] = defaultdict(lambda: defaultdict(set))
    for rid, g, norm in samples:
        word_index[norm][g].add(rid)

    total_samples = len(samples)
    covered_count = 0
    non_tie_count = 0
    correct_count = 0
    tie_count = 0

    per_given = {
        g: {"total": 0, "covered": 0, "non_tie": 0, "correct": 0, "ties": 0}
        for g in GIVENS
    }

    # Confusion matrix: true_given -> pred_given -> count
    confusion_matrix = {g1: {g2: 0 for g2 in GIVENS} for g1 in GIVENS}

    for rid, true_given, norm in samples:
        per_given[true_given]["total"] += 1

        # Count respondents excluding current respondent (LORO)
        counts = {}
        for g in GIVENS:
            other_rids = word_index[norm][g] - {rid}
            if other_rids:
                counts[g] = len(other_rids)

        if not counts:
            continue

        covered_count += 1
        per_given[true_given]["covered"] += 1

        max_c = max(counts.values())
        top_givens = [g for g, c in counts.items() if c == max_c]

        if len(top_givens) == 1:
            pred = top_givens[0]
            non_tie_count += 1
            per_given[true_given]["non_tie"] += 1
            confusion_matrix[true_given][pred] += 1
            if pred == true_given:
                correct_count += 1
                per_given[true_given]["correct"] += 1
        else:
            tie_count += 1
            per_given[true_given]["ties"] += 1

    coverage = covered_count / total_samples if total_samples else 0.0
    accuracy_non_tie = correct_count / non_tie_count if non_tie_count else 0.0
    overall_accuracy = correct_count / total_samples if total_samples else 0.0

    per_given_accuracy = {}
    for g in GIVENS:
        c = per_given[g]["correct"]
        nt = per_given[g]["non_tie"]
        per_given_accuracy[g] = round(c / nt, 4) if nt else 0.0

    results = {
        "samples": total_samples,
        "covered": covered_count,
        "coverage": round(coverage, 4),
        "non_tie_samples": non_tie_count,
        "tie_samples": tie_count,
        "accuracy_non_tie": round(accuracy_non_tie, 4),
        "overall_accuracy": round(overall_accuracy, 4),
        "per_given_accuracy": per_given_accuracy,
        "per_given_stats": per_given,
        "confusion_matrix": confusion_matrix,
    }

    return results


def print_evaluation_report(results: Dict[str, Any]):
    print("=" * 60)
    print("CROWD ASSOCIATION RAG: EXACT LEXICAL BASELINE (LORO)")
    print("=" * 60)
    print(f"samples={results['samples']}")
    print(f"coverage≈{results['coverage']:.3f} ({results['covered']}/{results['samples']})")
    print(f"accuracy_non_tie≈{results['accuracy_non_tie']:.3f} ({results['per_given_stats']['freedom']['correct'] + results['per_given_stats']['nonsense']['correct'] + results['per_given_stats']['solitude']['correct'] + results['per_given_stats']['death']['correct']}/{results['non_tie_samples']})")
    print(f"ties={results['tie_samples']} ({(results['tie_samples']/results['covered'])*100:.1f}% of covered)")
    print("")
    print("Per-given accuracy (non-tie):")
    for g, acc in results["per_given_accuracy"].items():
        st = results["per_given_stats"][g]
        print(f"  {g:<10} accuracy≈{acc:.3f} ({st['correct']}/{st['non_tie']})")
    print("=" * 60)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "data/dataset_v2.json"
    res = evaluate_exact_loro(target)
    print_evaluation_report(res)
