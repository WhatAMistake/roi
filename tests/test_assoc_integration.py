"""Regression coverage for the categorized /assoc retrieval path."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models

from src.crowd_association_service import AssociationHit, CrowdAssociationService
from src.rag import ExistentialRAG


EXAMPLE = {
    "freedom": ["без границ", "жизнь", "простор", "движение", "независимость"],
    "nonsense": ["бесполезность", "апатия", "потеря", "пустота", "отсутствие"],
    "solitude": ["тоска", "опасность", "беспомощность", "грусть", "остаться"],
    "death": ["конец", "война", "убийство", "разложение", "кладбище"],
}


def test_real_corpus_exact_pairs_work_without_semantic_service():
    rag = ExistentialRAG(data_dir=Path(__file__).resolve().parents[1] / "data",
                         use_local_embeddings=False)
    analysis = rag.analyze_user_associations(EXAMPLE)
    assert {(m["givens"], m["normalized_word"]) for m in analysis["matched_patterns"]} == {
        (given, term) for given, terms in EXAMPLE.items() for term in terms
    }
    assert len(analysis["matched_patterns"]) == 20
    assert analysis["semantic_status"]["state"] == "unavailable"
    assert analysis["result_status"] == "semantic_unavailable"


def test_exact_term_canonicalization_and_no_substring_matches(tmp_path):
    (tmp_path / "dataset.json").write_text(json.dumps([
        {"id": 1, "associations": {"freedom": ["жизненный", "жизненный"]}},
        {"id": 2, "associations": {"death": ["полёт"]}},
        {"id": 3, "associations": {"solitude": ["пустота"]}},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "association_index.json").write_text(
        '{"жизнь": {"freedom": [1]}}', encoding="utf-8"
    )
    rag = ExistentialRAG(data_dir=tmp_path, use_local_embeddings=False)
    analysis = rag.analyze_user_associations({
        "freedom": ["жизнь", "ЖИЗНЕННЫЙ!"],
        "death": ["Полёт", "полет", "пустота"],
    })
    assert {(m["givens"], m["normalized_word"]) for m in analysis["matched_patterns"]} == {
        ("freedom", "жизненный"), ("death", "полет")
    }


def test_individual_queries_filter_category_and_deduplicate_votes(tmp_path):
    service = CrowdAssociationService(data_dir=tmp_path)
    service.check_index_status = MagicMock(return_value={"available": True, "indexed_points": 3})

    def search(term, **kwargs):
        given = kwargs["given"]
        common = dict(similarity=0.8, respondent_count=1, given_share=1.0,
                      cross_given_counts={}, weight=1.0, matched_fragment=term)
        return [
            AssociationHit(term="shared", display_term="shared", given=given,
                           respondent_ids=[123], point_id="private-id", **common),
            AssociationHit(term="wrong", display_term="wrong", given="death", **common),
        ]

    service.search_similar_associations = MagicMock(side_effect=search)
    rag = ExistentialRAG(data_dir=tmp_path, use_local_embeddings=False, crowd_service=service)
    result = rag.analyze_user_associations({"freedom": ["one", "two"]})
    assert [c.args[0] for c in service.search_similar_associations.call_args_list] == ["one", "two"]
    assert all(c.kwargs["given"] == "freedom" and not c.kwargs["use_fragments"]
               for c in service.search_similar_associations.call_args_list)
    assert len(result["semantic_evidence"]) == 2
    assert all(hit["given"] == "freedom" for hit in result["semantic_evidence"])
    assert all("respondent_ids" not in hit and "point_id" not in hit
               for hit in result["semantic_evidence"])
    assert result["crowd_hypothesis"]["scores"]["freedom"] == 1.0
    assert result["crowd_hypothesis"]["scores"]["death"] == 0


def test_status_distinguishes_no_matches_weak_and_failure(tmp_path):
    service = CrowdAssociationService(data_dir=tmp_path)
    service.check_index_status = MagicMock(return_value={"available": True})
    service.search_similar_associations = MagicMock(return_value=[])
    rag = ExistentialRAG(data_dir=tmp_path, use_local_embeddings=False, crowd_service=service)
    assert rag.analyze_user_associations({"freedom": ["unknown"]})["result_status"] == "no_matches"
    hit = AssociationHit(term="x", display_term="x", given="freedom", similarity=0.6,
                         respondent_count=1, given_share=1, cross_given_counts={},
                         weight=0.1, matched_fragment="unknown")
    service.search_similar_associations.return_value = [hit]
    result = rag.analyze_user_associations({"freedom": ["unknown"]})
    assert result["result_status"] == "no_confident_hypothesis"
    assert result["crowd_hypothesis"]["primary"] is None
    assert result["suggested_themes"] == []
    service.search_similar_associations.side_effect = RuntimeError("offline")
    result = rag.analyze_user_associations({"freedom": ["unknown"]})
    assert result["semantic_status"]["state"] == "unavailable"
    assert result["result_status"] == "semantic_unavailable"
    assert result["semantic_evidence"] == []


def test_collection_status_and_filtered_qdrant_search():
    client = QdrantClient(":memory:")
    class Embedder:
        def encode(self, texts, **kwargs):
            return np.array([[1.0, 0.0] for _ in texts])

    service = CrowdAssociationService(qdrant_client=client, embedder=Embedder())
    assert not service.check_index_status()["collection_exists"]
    client.create_collection(service.collection_name, vectors_config=models.VectorParams(
        size=2, distance=models.Distance.COSINE))
    status = service.check_index_status()
    assert status["collection_exists"] and status["indexed_points"] == 0 and not status["available"]
    client.upsert(service.collection_name, points=[
        models.PointStruct(id=1, vector=[1.0, 0.0], payload={
            "given": "freedom", "term": "жизненный", "respondent_count": 1, "given_share": 1}),
        models.PointStruct(id=2, vector=[1.0, 0.0], payload={
            "given": "death", "term": "жизнь", "respondent_count": 1, "given_share": 1}),
    ])
    assert service.check_index_status()["indexed_points"] == 2
    hits = service.search_similar_associations("жизнь", given="freedom", use_fragments=False)
    assert len(hits) == 1 and hits[0].given == "freedom"
    assert hits[0].term == "жизненный" and not hits[0].is_exact
    client.close()
