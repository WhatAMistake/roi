"""
Semantic Leave-One-Respondent-Out (LORO) Evaluator for Crowd Association RAG v2.

Evaluates semantic retrieval and hypothesis aggregation with strict respondent-level partitioning:
- Held-out respondent's entries do not leak into candidate retrieval pool
- Computes Coverage, Accuracy, Macro-F1, Undetermined Rate, Per-Given Accuracy, Confusion Matrix
- Compares Random Baseline (25%), Exact Lexical Baseline, and Semantic Retrieval
- Hybrid scoring: exact lexical matches receive the exact-match boost
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics import confusion_matrix, f1_score

from src.crowd_association_service import (
    GIVENS,
    AssociationHit,
    CrowdAssociationService,
    EvidenceWeightConfig,
    GivenHypothesis,
    association_evidence_weight,
)

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def normalize_association_term(term: str) -> str:
    """Canonical normalization for association terms."""
    if not term:
        return ""
    t = term.lower().replace("ё", "е").strip()
    return re.sub(r"^[^\w]+|[^\w]+$", "", t)


def term_is_exact(query_term: str, candidate_term: str) -> bool:
    """Normalized lexical equality between the held-out term and a candidate term."""
    q = normalize_association_term(query_term)
    c = normalize_association_term(candidate_term)
    return bool(q) and q == c


class SemanticLOROEvaluator:
    def __init__(
        self,
        dataset_path: str | Path = "data/dataset_v2.json",
        items_path: str | Path = "data/association_items_v2.json",
        model_name: str = DEFAULT_MODEL,
        config: Optional[EvidenceWeightConfig] = None,
    ):
        self.dataset_path = Path(dataset_path)
        self.items_path = Path(items_path)
        self.model_name = model_name
        self.config = config or EvidenceWeightConfig()

        # Load raw dataset
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)

        # Load items
        with open(self.items_path, "r", encoding="utf-8") as f:
            self.items = json.load(f)

        # Build validation samples (respondent_id, true_given, canonical_term)
        self.samples: List[Tuple[int, str, str]] = []
        for r in self.dataset:
            rid = r["id"]
            associations = r.get("associations", {})
            for g in GIVENS:
                seen_terms: Set[str] = set()
                for raw in associations.get(g, []):
                    norm = normalize_association_term(raw)
                    if norm and norm not in seen_terms:
                        seen_terms.add(norm)
                        self.samples.append((rid, g, norm))

        # Collect all unique terms across samples and items
        unique_terms = sorted(list(set(s[2] for s in self.samples) | set(it["term"] for it in self.items)))
        self.term_to_idx = {term: idx for idx, term in enumerate(unique_terms)}
        self.unique_terms = unique_terms

        print(f"[EVAL] Initializing embedder ({model_name})...")
        self.embedder = SentenceTransformer(model_name)

        print(f"[EVAL] Encoding {len(unique_terms)} unique vocabulary terms...")
        # (N, D) normalized embeddings
        self.embeddings = self.embedder.encode(
            unique_terms,
            batch_size=128,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        # Precompute item metadata
        # Each item has: given, term, term_idx, respondent_ids (set)
        self.item_records = []
        for it in self.items:
            self.item_records.append({
                "given": it["given"],
                "term": it["term"],
                "term_idx": self.term_to_idx[it["term"]],
                "respondent_ids": set(it.get("respondent_ids", [])),
                "display_term": it.get("display_term", it["term"]),
            })

        self._service = CrowdAssociationService(config=self.config)

    def run_evaluation(
        self,
        top_n_candidates: int = 30,
    ) -> Dict[str, Any]:
        """Runs strict Leave-One-Respondent-Out evaluation."""
        start_time = time.time()
        cfg = self.config

        # Group samples by respondent
        samples_by_resp: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
        for rid, given, term in self.samples:
            samples_by_resp[rid].append((given, term))

        all_rids = sorted(list(samples_by_resp.keys()))
        print(f"[EVAL] Running LORO across {len(all_rids)} respondents ({len(self.samples)} samples)...")

        y_true: List[str] = []
        y_pred: List[Optional[str]] = []

        per_given_stats = {
            g: {"total": 0, "determined": 0, "correct": 0, "undetermined": 0}
            for g in GIVENS
        }

        # For each held-out respondent
        for resp_idx, held_out_rid in enumerate(all_rids):
            # 1. Filter and adjust candidate items for this fold
            fold_candidates = []
            fold_term_indices = []

            # We also need cross_given counts for this fold
            # First tally terms across givens for this fold
            term_given_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for it in self.item_records:
                remaining_resp = len(it["respondent_ids"] - {held_out_rid})
                if remaining_resp > 0:
                    term_given_counts[it["term"]][it["given"]] = remaining_resp

            for it in self.item_records:
                term = it["term"]
                g = it["given"]
                resp_count = term_given_counts[term].get(g, 0)
                if resp_count == 0:
                    continue  # Exclude item if it was only present for held-out respondent

                total_cross = sum(term_given_counts[term].values())
                given_share = resp_count / total_cross if total_cross > 0 else 0.0

                fold_candidates.append({
                    "given": g,
                    "term": term,
                    "display_term": it["display_term"],
                    "term_idx": it["term_idx"],
                    "respondent_count": resp_count,
                    "given_share": given_share,
                    "cross_given_counts": dict(term_given_counts[term]),
                })
                fold_term_indices.append(it["term_idx"])

            if not fold_candidates:
                continue

            # Candidate embeddings matrix for this fold: (M, D)
            cand_vectors = self.embeddings[fold_term_indices]

            # 2. Evaluate held-out respondent's samples
            for true_given, query_term in samples_by_resp[held_out_rid]:
                per_given_stats[true_given]["total"] += 1
                y_true.append(true_given)

                # Query vector
                q_idx = self.term_to_idx[query_term]
                q_vec = self.embeddings[q_idx]  # shape (D,)

                # Cosine similarities: (M,)
                sims = np.dot(cand_vectors, q_vec)

                # Create hits
                hits: List[AssociationHit] = []
                for i, sim_val in enumerate(sims):
                    if sim_val < cfg.similarity_floor:
                        continue
                    cand = fold_candidates[i]
                    # Hybrid scoring: the held-out association term is itself an
                    # exact lexical match for its own (given, term) candidates that
                    # other respondents produced, so it earns the exact-match boost.
                    is_exact = (
                        term_is_exact(query_term, cand["term"])
                        or float(sim_val) >= cfg.exact_similarity_threshold
                    )
                    hit = AssociationHit(
                        term=cand["term"],
                        display_term=cand["display_term"],
                        given=cand["given"],
                        similarity=round(float(sim_val), 4),
                        respondent_count=cand["respondent_count"],
                        given_share=round(cand["given_share"], 4),
                        cross_given_counts=cand["cross_given_counts"],
                        is_exact=is_exact,
                    )
                    hit.weight = association_evidence_weight(hit, cfg, is_exact=is_exact)
                    hits.append(hit)

                # Sort hits by weight descending and pick top_n_candidates
                hits.sort(key=lambda h: (h.weight, h.similarity), reverse=True)
                top_hits = hits[:top_n_candidates]

                # Aggregate hypothesis using the canonical service function
                hyp = self._service.aggregate_given_evidence(top_hits, cfg)

                if hyp.primary is None:
                    y_pred.append(None)
                    per_given_stats[true_given]["undetermined"] += 1
                else:
                    pred_given = hyp.primary
                    y_pred.append(pred_given)
                    per_given_stats[true_given]["determined"] += 1
                    if pred_given == true_given:
                        per_given_stats[true_given]["correct"] += 1

        elapsed = time.time() - start_time
        print(f"[EVAL] Evaluation completed in {elapsed:.2f}s")

        # Compute summary metrics
        total = len(y_true)
        determined_count = sum(1 for p in y_pred if p is not None)
        undetermined_count = total - determined_count
        correct_count = sum(1 for yt, yp in zip(y_true, y_pred) if yp is not None and yt == yp)

        coverage = determined_count / total if total else 0.0
        undetermined_rate = undetermined_count / total if total else 0.0
        accuracy_overall = correct_count / total if total else 0.0
        accuracy_determined = correct_count / determined_count if determined_count else 0.0

        per_given_accuracy = {}
        for g in GIVENS:
            st = per_given_stats[g]
            acc = st["correct"] / st["determined"] if st["determined"] else 0.0
            per_given_accuracy[g] = round(acc, 4)

        # Macro-F1 on determined predictions
        det_indices = [i for i, p in enumerate(y_pred) if p is not None]
        det_y_true = [y_true[i] for i in det_indices]
        det_y_pred = [y_pred[i] for i in det_indices]

        labels = GIVENS
        macro_f1 = f1_score(det_y_true, det_y_pred, labels=labels, average="macro")
        conf_mat = confusion_matrix(det_y_true, det_y_pred, labels=labels).tolist()

        results = {
            "samples": total,
            "determined": determined_count,
            "undetermined": undetermined_count,
            "coverage": round(coverage, 4),
            "undetermined_rate": round(undetermined_rate, 4),
            "accuracy_overall": round(accuracy_overall, 4),
            "accuracy_determined": round(accuracy_determined, 4),
            "macro_f1_determined": round(float(macro_f1), 4),
            "per_given_accuracy": per_given_accuracy,
            "per_given_stats": per_given_stats,
            "confusion_matrix": conf_mat,
            "labels": labels,
            "elapsed_seconds": round(elapsed, 2),
        }

        return results


def print_comparison_report(exact_res: Dict[str, Any], semantic_res: Dict[str, Any]):
    print("=" * 75)
    print("               CROWD ASSOCIATION RAG: COMPREHENSIVE BENCHMARK")
    print("=" * 75)
    print(f"{'Metric':<30} | {'Random':<10} | {'Lexical Exact':<14} | {'Semantic V2':<12}")
    print("-" * 75)
    print(f"{'Coverage':<30} | {'100.0%':<10} | {exact_res['coverage']*100:>12.1f}% | {semantic_res['coverage']*100:>10.1f}%")
    print(f"{'Undetermined Rate':<30} | {'0.0%':<10} | {'N/A':<14} | {semantic_res['undetermined_rate']*100:>10.1f}%")
    print(f"{'Accuracy (Non-Tie/Determined)':<30} | {'25.0%':<10} | {exact_res['accuracy_non_tie']*100:>12.1f}% | {semantic_res['accuracy_determined']*100:>10.1f}%")
    print(f"{'Overall Sample Accuracy':<30} | {'25.0%':<10} | {exact_res['overall_accuracy']*100:>12.1f}% | {semantic_res['accuracy_overall']*100:>10.1f}%")
    print(f"{'Macro F1 (Determined)':<30} | {'0.250':<10} | {'N/A':<14} | {semantic_res['macro_f1_determined']:>11.3f}")
    print("-" * 75)
    print("Per-Given Accuracy:")
    for g in GIVENS:
        lex_acc = exact_res['per_given_accuracy'].get(g, 0.0) * 100
        sem_acc = semantic_res['per_given_accuracy'].get(g, 0.0) * 100
        print(f"  {g:<28} | {'25.0%':<10} | {lex_acc:>12.1f}% | {sem_acc:>10.1f}%")
    print("-" * 75)
    print("Semantic Confusion Matrix (Rows: True, Columns: Predicted):")
    labels = semantic_res["labels"]
    header = "          " + "  ".join(f"{l[:7]:>7}" for l in labels)
    print(header)
    for i, row in enumerate(semantic_res["confusion_matrix"]):
        row_str = f"{labels[i][:8]:<8}: " + "  ".join(f"{val:>7}" for val in row)
        print(row_str)
    print("=" * 75)


if __name__ == "__main__":
    from evaluate_crowd_exact import evaluate_exact_loro

    parser = argparse.ArgumentParser(description="Evaluate Crowd Association Semantic Retrieval")
    parser.add_argument("--dataset", default="data/dataset_v2.json")
    parser.add_argument("--items", default="data/association_items_v2.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sim-floor", type=float, default=None, help="Similarity noise floor")
    parser.add_argument("--exact-boost", type=float, default=None, help="Exact-match weight multiplier")
    parser.add_argument("--rank-decay", type=float, default=None, help="Per-given candidate rank decay")
    parser.add_argument("--min-threshold", type=float, default=None, help="Min confidence threshold")
    parser.add_argument("--margin", type=float, default=None, help="Moderate margin threshold")
    parser.add_argument("--strong-margin", type=float, default=None, help="Strong margin threshold")
    parser.add_argument("--top-k", type=int, default=None, help="Max candidates per given")
    parser.add_argument("--report-json", default=None, help="Write the full semantic result dict to this JSON file")
    args = parser.parse_args()

    cfg = EvidenceWeightConfig()
    if args.sim_floor is not None:
        cfg.similarity_floor = args.sim_floor
    if args.exact_boost is not None:
        cfg.exact_match_boost = args.exact_boost
    if args.rank_decay is not None:
        cfg.rank_decay = args.rank_decay
    if args.min_threshold is not None:
        cfg.min_confidence_threshold = args.min_threshold
    if args.margin is not None:
        cfg.margin_threshold = args.margin
    if args.strong_margin is not None:
        cfg.strong_margin_threshold = args.strong_margin
    if args.top_k is not None:
        cfg.top_k_per_given = args.top_k

    # 1. Run exact lexical baseline
    exact_results = evaluate_exact_loro(args.dataset)

    # 2. Run semantic LORO
    evaluator = SemanticLOROEvaluator(
        dataset_path=args.dataset,
        items_path=args.items,
        model_name=args.model,
        config=cfg,
    )
    semantic_results = evaluator.run_evaluation()

    # 3. Print comprehensive comparison
    print_comparison_report(exact_results, semantic_results)

    if args.report_json:
        payload = {
            "config": {
                "similarity_floor": cfg.similarity_floor,
                "exact_match_boost": cfg.exact_match_boost,
                "exact_similarity_threshold": cfg.exact_similarity_threshold,
                "rank_decay": cfg.rank_decay,
                "top_k_per_given": cfg.top_k_per_given,
                "min_confidence_threshold": cfg.min_confidence_threshold,
                "margin_threshold": cfg.margin_threshold,
                "strong_margin_threshold": cfg.strong_margin_threshold,
            },
            "semantic": semantic_results,
            "exact_lexical": exact_results,
        }
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[EVAL] Report written to {args.report_json}")
