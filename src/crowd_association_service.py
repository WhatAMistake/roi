"""
Crowd Association RAG v2 Service.

Provides:
- search_similar_associations: vector retrieval over existential_associations_v2
- association_evidence_weight: explicit mathematical weighting
  (similarity, specificity, consensus, hybrid exact-match boost)
- aggregate_given_evidence: hypothesis scoring, top-k bounded aggregation with
  rank decay, margin & threshold checks
- query fragmentation for user messages without pre-LLM bias
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer

from src.crowd_artifacts import normalize_association_term

logger = logging.getLogger(__name__)

GIVENS = ["freedom", "nonsense", "solitude", "death"]
DEFAULT_COLLECTION = "existential_associations_v2"
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class EvidenceWeightConfig:
    """Hyperparameters for evidence weighting and hypothesis classification.

    Hybrid scoring knobs:
    - ``similarity_floor``: noise floor; hits below it carry zero weight.
      Raised from the legacy 0.30 to 0.50: weak semantic neighbours were
      flooding aggregation with low-information candidates, and raising the
      floor both removes that noise and resolves close ties.
    - ``exact_match_boost``: multiplier applied when the retrieved term is an
      exact lexical match with the query fragment (or has similarity >= 1).
    - ``exact_similarity_threshold``: similarity treated as an exact hit.
    - ``rank_decay``: geometric decay applied by candidate rank inside a given,
      so the best candidate dominates a pile of mediocre ones.
    """
    alpha_specificity: float = float(os.getenv("CROWD_RAG_ALPHA", "1.2"))
    beta_frequency: float = float(os.getenv("CROWD_RAG_BETA", "0.5"))
    similarity_floor: float = float(os.getenv("CROWD_RAG_SIM_FLOOR", "0.50"))
    top_k_per_given: int = int(os.getenv("CROWD_RAG_TOP_K", "5"))
    min_confidence_threshold: float = float(os.getenv("CROWD_RAG_MIN_THRESHOLD", "0.45"))
    margin_threshold: float = float(os.getenv("CROWD_RAG_MARGIN_THRESHOLD", "0.15"))
    strong_margin_threshold: float = float(os.getenv("CROWD_RAG_STRONG_MARGIN", "0.30"))
    exact_match_boost: float = float(os.getenv("CROWD_RAG_EXACT_BOOST", "2.0"))
    exact_similarity_threshold: float = float(os.getenv("CROWD_RAG_EXACT_SIM", "0.999"))
    rank_decay: float = float(os.getenv("CROWD_RAG_RANK_DECAY", "0.75"))


@dataclass
class AssociationHit:
    term: str
    display_term: str
    given: str
    similarity: float
    respondent_count: int
    given_share: float
    cross_given_counts: Dict[str, int]
    weight: float = 0.0
    matched_fragment: str = ""
    point_id: str = ""
    respondent_ids: List[int] = field(default_factory=list)
    is_exact: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GivenHypothesis:
    scores: Dict[str, float]
    normalized_scores: Dict[str, float]
    primary: Optional[str]
    secondary: Optional[str]
    confidence: str  # "weak" | "moderate" | "strong"
    evidence: List[Dict[str, Any]]
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fragment_token_set(fragment: str) -> Set[str]:
    """Tokenizes an already-normalized fragment for exact lexical comparison."""
    return set(re.findall(r"[\w-]+", fragment))


def is_exact_lexical_match(term: str, fragment: str) -> bool:
    """
    True when the association term is a lexical (not semantic) match of the
    query fragment: either the whole normalized fragment equals the term, or the
    term appears verbatim as one of the fragment's tokens.
    """
    norm_term = normalize_association_term(term)
    if not norm_term:
        return False

    norm_fragment = normalize_association_term(fragment)
    if not norm_fragment:
        return False

    if norm_term == norm_fragment:
        return True

    return norm_term in fragment_token_set(norm_fragment)


def association_evidence_weight(
    hit: AssociationHit,
    config: Optional[EvidenceWeightConfig] = None,
    is_exact: bool = False,
) -> float:
    """
    Computes evidence weight combining:
    1. Semantic similarity s in [0, 1]
    2. Given specificity (given_share^alpha)
    3. Crowd frequency / consensus (log2(1 + respondent_count)^beta)
    4. Hybrid exact-match boost (x exact_match_boost) when the term is an exact
       lexical match of the query fragment or has (near) perfect similarity.
    Hits below the similarity noise floor get zero weight.
    """
    cfg = config or EvidenceWeightConfig()

    sim = max(0.0, float(hit.similarity))
    if sim < cfg.similarity_floor:
        return 0.0

    share = max(0.0, min(1.0, float(hit.given_share)))
    specificity_factor = share ** cfg.alpha_specificity

    resp_count = max(0, int(hit.respondent_count))
    freq_log = math.log2(1.0 + resp_count)
    frequency_factor = freq_log ** cfg.beta_frequency if freq_log > 0 else 0.0

    weight = sim * specificity_factor * frequency_factor

    if is_exact or sim >= cfg.exact_similarity_threshold:
        weight *= cfg.exact_match_boost

    return round(weight, 5)


def split_query_fragments(query: str, min_chars: int = 15) -> List[str]:
    """
    Splits a query into fragments:
    - Full text
    - Sentence / clause fragments (if text is long enough)
    No LLM extraction is used to prevent circular reasoning.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    fragments = [cleaned]

    # If long enough, split by punctuation
    if len(cleaned) >= 60:
        parts = re.split(r"(?<=[.!?…;\n])\s+", cleaned)
        for part in parts:
            part = part.strip()
            if len(part) >= min_chars and part != cleaned:
                fragments.append(part)

    return fragments


class CrowdAssociationService:
    def __init__(
        self,
        data_dir: str | Path = "data",
        collection_name: str = DEFAULT_COLLECTION,
        model_name: str = DEFAULT_MODEL,
        config: Optional[EvidenceWeightConfig] = None,
        qdrant_client: Optional[QdrantClient] = None,
        embedder: Optional[SentenceTransformer] = None,
    ):
        self.data_dir = Path(data_dir)
        self.collection_name = collection_name
        self.model_name = model_name
        self.config = config or EvidenceWeightConfig()

        self._embedder = embedder
        self._qdrant = qdrant_client

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            logger.info("Initializing SentenceTransformer: %s", self.model_name)
            self._embedder = SentenceTransformer(self.model_name)
        return self._embedder

    @property
    def qdrant(self) -> QdrantClient:
        if self._qdrant is None:
            storage_path = self.data_dir / "qdrant_storage"
            storage_path.mkdir(parents=True, exist_ok=True)
            logger.info("Connecting to Qdrant at: %s", storage_path)
            self._qdrant = QdrantClient(path=str(storage_path))
        return self._qdrant

    def search_similar_associations(
        self,
        query: str,
        n_results: int = 30,
        filter_respondent_id: Optional[int] = None,
        use_fragments: bool = True,
    ) -> List[AssociationHit]:
        """
        Retrieves top similar associations for query.
        Handles query fragmentation and cross-fragment deduplication.
        Returns a list of AssociationHit (no classification performed here).
        """
        if not query or not query.strip():
            return []

        fragments = split_query_fragments(query) if use_fragments else [query.strip()]
        if not fragments:
            return []

        # Encode fragments
        embeddings = self.embedder.encode(
            fragments,
            batch_size=len(fragments),
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        all_hits_by_key: Dict[Tuple[str, str], AssociationHit] = {}

        for fragment_text, emb in zip(fragments, embeddings):
            query_vector = emb.tolist()

            # Optional respondent filter for leave-one-out evaluation
            q_filter = None
            if filter_respondent_id is not None:
                q_filter = qmodels.Filter(
                    must_not=[
                        qmodels.FieldCondition(
                            key="respondent_ids",
                            match=qmodels.MatchValue(value=filter_respondent_id),
                        )
                    ]
                )

            search_response = self.qdrant.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=n_results,
                query_filter=q_filter,
                with_payload=True,
            ).points

            for pt in search_response:
                payload = pt.payload or {}
                term = payload.get("term", "")
                given = payload.get("given", "")
                sim = float(pt.score)

                # Hybrid scoring: exact lexical match between the normalized
                # fragment and the normalized crowd term.
                is_exact = is_exact_lexical_match(term, fragment_text) or sim >= self.config.exact_similarity_threshold

                key = (given, term)
                if key not in all_hits_by_key or sim > all_hits_by_key[key].similarity:
                    hit = AssociationHit(
                        term=term,
                        display_term=payload.get("display_term", term),
                        given=given,
                        similarity=round(sim, 4),
                        respondent_count=int(payload.get("respondent_count", 1)),
                        given_share=float(payload.get("given_share", 1.0)),
                        cross_given_counts=payload.get("cross_given_counts", {}),
                        point_id=str(pt.id),
                        matched_fragment=fragment_text,
                        respondent_ids=payload.get("respondent_ids", []),
                        is_exact=is_exact,
                    )
                    hit.weight = association_evidence_weight(
                        hit, self.config, is_exact=is_exact
                    )
                    all_hits_by_key[key] = hit

        # Sort all deduplicated hits by weight descending, then similarity
        dedup_hits = list(all_hits_by_key.values())
        dedup_hits.sort(key=lambda h: (h.weight, h.similarity), reverse=True)
        return dedup_hits[:n_results]

    def aggregate_given_evidence(
        self,
        hits: Sequence[AssociationHit],
        config: Optional[EvidenceWeightConfig] = None,
    ) -> GivenHypothesis:
        """
        Aggregates retrieved association hits into a GivenHypothesis:
        - Bounded top-k aggregation per given (prevents large associative vocabularies from dominating)
        - Exponential rank decay per given, so the strongest candidate (typically
          an exact or high-similarity match) dominates a pile of mediocre ones
        - Margin & threshold checks
        - Returns primary, secondary, confidence, and top evidence terms
        """; 
        cfg = config or self.config

        # Group hits by given
        given_hits: Dict[str, List[AssociationHit]] = {g: [] for g in GIVENS}
        for hit in hits:
            if hit.given in given_hits and hit.weight > 0.0:
                given_hits[hit.given].append(hit)

        # Sort each group by weight descending and pick top_k
        top_k = cfg.top_k_per_given
        scores: Dict[str, float] = {}
        top_evidence_items: List[Dict[str, Any]] = []

        for g in GIVENS:
            ghits = sorted(
                given_hits[g], key=lambda h: (h.weight, h.similarity), reverse=True
            )[:top_k]
            # Rank-decayed aggregation: rank 0 (best candidate) keeps full weight,
            # each following candidate contributes rank_decay**rank of its weight.
            score_sum = 0.0
            for rank, h in enumerate(ghits):
                score_sum += h.weight * (cfg.rank_decay ** rank)
            scores[g] = round(score_sum, 4)
            for h in ghits:
                top_evidence_items.append({
                    "given": g,
                    "term": h.term,
                    "display_term": h.display_term,
                    "similarity": h.similarity,
                    "respondent_count": h.respondent_count,
                    "given_share": h.given_share,
                    "weight": h.weight,
                    "is_exact": h.is_exact,
                    "matched_fragment": h.matched_fragment,
                })

        total_score = sum(scores.values())
        normalized_scores = {
            g: round(scores[g] / total_score, 4) if total_score > 0 else 0.0
            for g in GIVENS
        }

        # Rank givens by raw score
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        g1, s1 = ranked[0]
        g2, s2 = ranked[1]
        delta = s1 - s2

        primary: Optional[str] = None
        secondary: Optional[str] = None
        confidence = "weak"

        # Check thresholds
        if s1 < cfg.min_confidence_threshold or total_score == 0:
            primary = None
            secondary = None
            confidence = "weak"
        elif delta >= cfg.strong_margin_threshold:
            primary = g1
            secondary = None
            confidence = "strong"
        elif delta >= cfg.margin_threshold:
            primary = g1
            if s2 >= cfg.min_confidence_threshold:
                secondary = g2
            confidence = "moderate"
        else:
            # Score delta is smaller than margin_threshold: close tie -> undetermined primary
            primary = None
            secondary = None
            confidence = "weak"

        details = {
            "top_score": s1,
            "runner_up_score": s2,
            "score_delta": round(delta, 4),
            "total_score": round(total_score, 4),
            "min_threshold": cfg.min_confidence_threshold,
            "margin_threshold": cfg.margin_threshold,
        }

        return GivenHypothesis(
            scores=scores,
            normalized_scores=normalized_scores,
            primary=primary,
            secondary=secondary,
            confidence=confidence,
            evidence=top_evidence_items,
            details=details,
        )

    def analyze_text(
        self,
        text: str,
        n_results: int = 30,
        use_fragments: bool = True,
    ) -> Tuple[List[AssociationHit], GivenHypothesis]:
        """Convenience method: searches associations and aggregates hypothesis."""
        hits = self.search_similar_associations(
            query=text,
            n_results=n_results,
            use_fragments=use_fragments,
        )
        hypothesis = self.aggregate_given_evidence(hits)
        return hits, hypothesis
