"""
Unit and Regression Tests for Crowd Association RAG v2.

Validates all 7 mandatory criteria specified in AGENT_TASK_CROWD_RAG_V2.md:
1. One respondent entering the same word twice does not duplicate respondent frequency.
2. ё/е canonical normalization equivalence.
3. Ambiguous term with multiple givens has given_share < 1.0.
4. Retrieval can return evidence across multiple givens.
5. Weak/indistinguishable scores return undetermined (None).
6. Narrative retrieval does not vote for givens.
7. Rebuild command picks up updated JSON data.

Additional hybrid-scoring (Exact Match Boost + similarity noise floor) criteria:
8. Exact lexical match detection between association term and query fragment.
9. Exact-match boost multiplies evidence weight only for exact hits.
10. Similarity noise floor discards weak evidence.
11. Rank decay keeps the strongest candidate ahead of many mediocre ones.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.crowd_artifacts import (
    GIVENS,
    build_artifacts,
    normalize_association_term,
)
from src.crowd_association_service import (
    AssociationHit,
    CrowdAssociationService,
    EvidenceWeightConfig,
    association_evidence_weight,
    is_exact_lexical_match,
)
from scripts.rebuild_association_index import rebuild_association_index


# Criterion 1: One respondent entering the same word twice does not duplicate respondent frequency
def test_duplicate_term_per_respondent_not_doubled():
    mock_records = [
        {
            "id": 1,
            "associations": {
                "freedom": ["птица", "птица", "небо"],  # 'птица' entered twice
                "nonsense": [],
                "solitude": [],
                "death": [],
            },
            "narratives": {},
            "demographics": {},
        },
        {
            "id": 2,
            "associations": {
                "freedom": ["птица"],  # respondent 2 enters 'птица'
                "nonsense": [],
                "solitude": [],
                "death": [],
            },
            "narratives": {},
            "demographics": {},
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dataset = Path(tmpdir) / "dataset.json"
        with open(tmp_dataset, "w", encoding="utf-8") as f:
            json.dump(mock_records, f)

        build_artifacts(tmp_dataset, tmpdir)

        with open(Path(tmpdir) / "association_items_v2.json", "r", encoding="utf-8") as f:
            items = json.load(f)

        bird_items = [it for it in items if it["term"] == "птица" and it["given"] == "freedom"]
        assert len(bird_items) == 1
        # Respondent 1 entered it twice, respondent 2 entered it once -> respondent_count must be 2
        assert bird_items[0]["respondent_count"] == 2
        assert bird_items[0]["respondent_ids"] == [1, 2]


# Criterion 2: ё/е canonical normalization equivalence
def test_yo_e_canonical_normalization():
    assert normalize_association_term("полёт") == "полет"
    assert normalize_association_term("всё") == "все"
    assert normalize_association_term("слёзы") == "слезы"
    assert normalize_association_term("  Безнадёжность! ") == "безнадежность"
    assert normalize_association_term("самолёт-истребитель") == "самолет-истребитель"


# Criterion 3: Ambiguous term with multiple givens has given_share < 1.0
def test_ambiguous_term_has_given_share_less_than_one():
    # Load actual generated association_items_v2.json
    items_path = Path("data/association_items_v2.json")
    assert items_path.exists(), "association_items_v2.json must exist"

    with open(items_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    # Find cross-given terms like 'пустота'
    void_items = [it for it in items if it["term"] == "пустота"]
    assert len(void_items) > 1, "'пустота' should appear in multiple givens"

    for it in void_items:
        assert it["given_share"] < 1.0
        assert sum(it["cross_given_counts"].values()) > it["respondent_count"]


# Criterion 4: Retrieval can return evidence across multiple givens
def test_retrieval_returns_multi_given_evidence():
    service = CrowdAssociationService(data_dir="data")
    hits = service.search_similar_associations("пустота и бессмысленная изоляция", n_results=10)

    assert len(hits) > 0
    givens_found = set(h.given for h in hits)
    # The query mentions themes from both nonsense/solitude, so multiple givens must be present
    assert len(givens_found) >= 2


# Criterion 5: Weak/flat scores return undetermined (None)
def test_weak_scores_return_undetermined():
    service = CrowdAssociationService(data_dir="data")

    # 1. Empty hits
    hyp_empty = service.aggregate_given_evidence([])
    assert hyp_empty.primary is None
    assert hyp_empty.secondary is None
    assert hyp_empty.confidence == "weak"

    # 2. Hits below min_confidence_threshold
    weak_hits = [
        AssociationHit(
            term="что-то",
            display_term="что-то",
            given="freedom",
            similarity=0.31,
            respondent_count=1,
            given_share=0.2,
            cross_given_counts={"freedom": 1, "death": 1, "solitude": 1, "nonsense": 1},
            weight=0.05,
        ),
        AssociationHit(
            term="нечто",
            display_term="нечто",
            given="death",
            similarity=0.31,
            respondent_count=1,
            given_share=0.2,
            cross_given_counts={"freedom": 1, "death": 1, "solitude": 1, "nonsense": 1},
            weight=0.05,
        ),
    ]
    cfg = EvidenceWeightConfig(min_confidence_threshold=0.50, margin_threshold=0.20)
    hyp_weak = service.aggregate_given_evidence(weak_hits, config=cfg)
    assert hyp_weak.primary is None
    assert hyp_weak.confidence == "weak"


# Criterion 6: Narrative retrieval does not vote for givens
def test_narrative_retrieval_does_not_vote_on_given():
    chunks_path = Path("data/narrative_chunks_v2.json")
    assert chunks_path.exists(), "narrative_chunks_v2.json must exist"

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    assert len(chunks) > 0
    for chunk in chunks:
        # Crucial requirement: pure narrative chunks without association payload
        assert "associations" not in chunk
        assert "given" not in chunk


# Criterion 7: Rebuild command picks up updated JSON data
def test_rebuild_picks_up_updated_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_data = Path(tmpdir)
        mock_items = [
            {
                "id": "assoc_freedom_0001",
                "source": "crowd_association",
                "term": "тестовое_крыло",
                "display_term": "тестовое_крыло",
                "given": "freedom",
                "respondent_count": 3,
                "given_share": 1.0,
                "cross_given_counts": {"freedom": 3, "nonsense": 0, "solitude": 0, "death": 0},
                "respondent_ids": [101, 102, 103],
            }
        ]
        with open(tmp_data / "association_items_v2.json", "w", encoding="utf-8") as f:
            json.dump(mock_items, f)

        res = rebuild_association_index(
            data_dir=tmp_data,
            collection_name="test_temp_collection",
            force=True,
        )
        assert res["status"] == "rebuilt"
        assert res["points_count"] == 1
        assert res["items_count"] == 1


# Criterion 8: Hybrid scoring - exact lexical match detection
def test_exact_lexical_match_detection():
    # Whole-fragment equality (case and ё/е canonicalized)
    assert is_exact_lexical_match("пустота", "ПУСТОТА")
    assert is_exact_lexical_match("полет", "полёт")
    # Term appears verbatim as a token inside a longer query fragment
    assert is_exact_lexical_match("смерть", "я постоянно думаю про смерть и пустоту")
    # Semantic / morphological neighbours are NOT exact matches
    assert not is_exact_lexical_match("смерть", "меня пугает собственная смертность")
    assert not is_exact_lexical_match("страх", "мне очень страшно")
    assert not is_exact_lexical_match("", "пустота")


# Criterion 9: Hybrid scoring - exact-match boost multiplies weight only when exact
def test_exact_match_boost_scales_weight():
    hit = AssociationHit(
        term="пустота",
        display_term="пустота",
        given="solitude",
        similarity=0.8,
        respondent_count=4,
        given_share=0.5,
        cross_given_counts={"freedom": 4, "nonsense": 0, "solitude": 4, "death": 0},
    )

    cfg = EvidenceWeightConfig(exact_match_boost=2.0)
    base = association_evidence_weight(hit, cfg)
    boosted = association_evidence_weight(hit, cfg, is_exact=True)
    assert base > 0.0
    assert boosted == pytest.approx(base * cfg.exact_match_boost, rel=1e-4)

    # Boost disabled -> exact and non-exact weights coincide
    cfg_off = EvidenceWeightConfig(exact_match_boost=1.0)
    assert association_evidence_weight(hit, cfg_off, is_exact=True) == pytest.approx(
        association_evidence_weight(hit, cfg_off), rel=1e-4
    )

    # Similarity above the configured exact threshold is treated as exact too
    cfg_sim = EvidenceWeightConfig(exact_match_boost=2.0, exact_similarity_threshold=0.999)
    identical = AssociationHit(
        term="пустота",
        display_term="пустота",
        given="solitude",
        similarity=1.0,
        respondent_count=4,
        given_share=0.5,
        cross_given_counts={"freedom": 4, "solitude": 4},
    )
    assert association_evidence_weight(identical, cfg_sim) == pytest.approx(
        association_evidence_weight(identical, cfg_sim, is_exact=True), rel=1e-4
    )


# Criterion 10: Similarity noise floor discards weak evidence
@pytest.mark.parametrize("floor", [0.45, 0.50])
def test_similarity_floor_zeroes_weak_hits(floor):
    cfg = EvidenceWeightConfig(similarity_floor=floor)
    weak = AssociationHit(
        term="отражение",
        display_term="отражение",
        given="freedom",
        similarity=round(floor - 0.0001, 5),
        respondent_count=30,
        given_share=1.0,
        cross_given_counts={"freedom": 30},
    )
    assert association_evidence_weight(weak, cfg) == 0.0

    strong = AssociationHit(
        term="отражение",
        display_term="отражение",
        given="freedom",
        similarity=floor,
        respondent_count=30,
        given_share=1.0,
        cross_given_counts={"freedom": 30},
    )
    assert association_evidence_weight(strong, cfg) > 0.0


# Criterion 11: Rank decay makes the top candidate dominate mediocre ones
def test_top_candidate_dominates_mediocre_candidates():
    service = CrowdAssociationService(data_dir="data")
    cfg = EvidenceWeightConfig(
        rank_decay=0.5,
        top_k_per_given=5,
        min_confidence_threshold=0.0,
        margin_threshold=0.0,
    )

    exact_hit = AssociationHit(
        term="одиночество",
        display_term="одиночество",
        given="solitude",
        similarity=1.0,
        respondent_count=10,
        given_share=0.5,
        cross_given_counts={"solitude": 10, "freedom": 10},
        weight=1.0,
        is_exact=True,
    )
    mediocre = [
        AssociationHit(
            term=f"слово{i}",
            display_term=f"слово{i}",
            given="freedom",
            similarity=0.6,
            respondent_count=3,
            given_share=0.5,
            cross_given_counts={"freedom": 3, "death": 3},
            weight=0.3,
        )
        for i in range(5)
    ]

    hyp = service.aggregate_given_evidence([exact_hit, *mediocre], config=cfg)

    # Top candidate keeps full weight, the other four are decayed geometrically
    assert hyp.scores["solitude"] == pytest.approx(1.0, rel=1e-4)
    assert hyp.scores["freedom"] == pytest.approx(
        0.3 * (1 + 0.5 + 0.25 + 0.125 + 0.0625), rel=1e-3
    )
    assert hyp.primary == "solitude"
    assert hyp.secondary is None
    assert hyp.confidence == "strong"
