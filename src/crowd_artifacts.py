"""
Pipeline for generating Crowd Association RAG v2 artifacts from dataset_v2.json.

Artifacts produced:
- association_items_v2.json: Aggregated unique (given, term) entries with crowd statistics.
- association_index_v2.json: Inverted lexical index mapping term -> givens -> respondent_ids.
- narrative_chunks_v2.json: Pure narrative chunks without association payload.
- build_metrics.json: Detailed dataset & vocabulary statistics.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

GIVENS = ["freedom", "nonsense", "solitude", "death"]


def normalize_association_term(term: str) -> str:
    """Canonical normalization for association terms.
    - lowercase
    - strip whitespace
    - canonicalize ё -> е
    - strip leading/trailing non-word punctuation while preserving internal hyphens
    """
    if not term:
        return ""
    t = term.lower().replace("ё", "е").strip()
    # Strip leading/trailing non-alphanumeric/non-word characters
    t = re.sub(r"^[^\w]+|[^\w]+$", "", t)
    return t


def build_artifacts(
    dataset_path: str | Path,
    output_dir: str | Path,
) -> Dict[str, Any]:
    dataset_path = Path(dataset_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(dataset_path, "r", encoding="utf-8") as f:
        records: List[Dict[str, Any]] = json.load(f)

    # 1. Parse associations with per-respondent deduplication
    # respondent_given_terms[respondent_id][given] = set of canonical terms
    respondent_assoc: Dict[int, Dict[str, Set[str]]] = {}
    display_terms: Dict[str, str] = {}  # canonical -> most frequent or first display form
    term_given_respondents: Dict[str, Dict[str, Set[int]]] = defaultdict(lambda: defaultdict(set))
    raw_sample_count = 0
    valid_sample_count = 0

    for r in records:
        rid = r["id"]
        respondent_assoc[rid] = {g: set() for g in GIVENS}
        associations = r.get("associations", {})
        for g in GIVENS:
            raw_list = associations.get(g, [])
            for raw_term in raw_list:
                raw_sample_count += 1
                norm = normalize_association_term(raw_term)
                if not norm:
                    continue
                if norm not in display_terms:
                    display_terms[norm] = raw_term.strip().lower()
                if norm not in respondent_assoc[rid][g]:
                    respondent_assoc[rid][g].add(norm)
                    term_given_respondents[norm][g].add(rid)
                    valid_sample_count += 1

    # 2. Build association_items_v2.json
    # One item per unique (given, canonical_term)
    association_items: List[Dict[str, Any]] = []
    item_counter = 0

    for term, givens_map in sorted(term_given_respondents.items(), key=lambda x: x[0]):
        # Calculate cross_given_counts
        cross_given_counts = {g: len(givens_map.get(g, set())) for g in GIVENS}
        total_term_occurrences = sum(cross_given_counts.values())

        for g in GIVENS:
            resp_set = givens_map.get(g, set())
            if not resp_set:
                continue
            item_counter += 1
            resp_count = len(resp_set)
            given_share = resp_count / total_term_occurrences if total_term_occurrences > 0 else 0.0

            item = {
                "id": f"assoc_{g}_{item_counter:04d}",
                "source": "crowd_association",
                "term": term,
                "display_term": display_terms.get(term, term),
                "given": g,
                "respondent_count": resp_count,
                "given_share": round(given_share, 4),
                "cross_given_counts": cross_given_counts,
                "respondent_ids": sorted(list(resp_set)),
            }
            association_items.append(item)

    # 3. Build association_index_v2.json (Lexical inverted index)
    # term -> {given: [respondent_ids]}
    lexical_index: Dict[str, Dict[str, List[int]]] = {}
    for term, givens_map in term_given_respondents.items():
        lexical_index[term] = {
            g: sorted(list(rids)) for g, rids in givens_map.items() if rids
        }

    # 4. Build narrative_chunks_v2.json (Pure narratives WITHOUT association labels)
    narrative_chunks: List[Dict[str, Any]] = []
    narrative_types = [
        ("about_self", "self_description"),
        ("first_death", "death_experience"),
        ("first_therapist", "therapy_experience"),
        ("school_story", "school_story"),
        ("free_form", "free_form"),
    ]

    for r in records:
        rid = r["id"]
        narratives = r.get("narratives", {})
        demographics = r.get("demographics", {})
        for field, chunk_type in narrative_types:
            text = (narratives.get(field) or "").strip()
            if text:
                narrative_chunks.append({
                    "id": f"{rid}_{field}",
                    "record_id": rid,
                    "type": chunk_type,
                    "demographics": demographics,
                    "text": text,
                    # Crucial: NO "associations" payload here!
                })

    # 5. Build metrics
    unique_terms = set(term_given_respondents.keys())
    per_given_term_counts = {
        g: len([item for item in association_items if item["given"] == g])
        for g in GIVENS
    }
    per_given_sample_counts = {
        g: sum(len(respondent_assoc[rid][g]) for rid in respondent_assoc)
        for g in GIVENS
    }

    # Overlap metrics: terms occurring in >1 given
    ambiguous_terms = [
        term for term, givens_map in term_given_respondents.items()
        if len([g for g in GIVENS if givens_map.get(g)]) > 1
    ]

    build_metrics = {
        "respondent_count": len(records),
        "raw_samples": raw_sample_count,
        "valid_samples_deduplicated": valid_sample_count,
        "unique_terms_total": len(unique_terms),
        "association_items_count": len(association_items),
        "narrative_chunks_count": len(narrative_chunks),
        "per_given_unique_terms": per_given_term_counts,
        "per_given_samples": per_given_sample_counts,
        "ambiguous_terms_count": len(ambiguous_terms),
        "ambiguous_terms_ratio": round(len(ambiguous_terms) / len(unique_terms), 4) if unique_terms else 0,
    }

    # Write output files
    with open(output_dir / "association_items_v2.json", "w", encoding="utf-8") as f:
        json.dump(association_items, f, ensure_ascii=False, indent=2)

    with open(output_dir / "association_index_v2.json", "w", encoding="utf-8") as f:
        json.dump(lexical_index, f, ensure_ascii=False, indent=2)

    with open(output_dir / "narrative_chunks_v2.json", "w", encoding="utf-8") as f:
        json.dump(narrative_chunks, f, ensure_ascii=False, indent=2)

    with open(output_dir / "build_metrics.json", "w", encoding="utf-8") as f:
        json.dump(build_metrics, f, ensure_ascii=False, indent=2)

    return build_metrics


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    dataset_file = base_dir / "data" / "dataset_v2.json"
    out_dir = base_dir / "data"
    print(f"Building crowd RAG v2 artifacts from {dataset_file}...")
    metrics = build_artifacts(dataset_file, out_dir)
    print("Build complete. Metrics:")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
