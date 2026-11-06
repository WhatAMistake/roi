"""
Unit tests for the refactored ExistentialRAG pipeline (src/rag.py).

Covers:
1. Empty / whitespace queries and non-positive n_results.
2. Configurable relevance thresholds and filtering of low-relevance results.
3. Deduplication of duplicate or near-duplicate chunks (keeping highest relevance).
4. Keyword fallback when Qdrant or embeddings are unavailable (including book_chunks and narrative chunks, stopword resistance, and explicit retrieval_method metadata).
5. Preservation of metadata and unified RAGResult structure across book and narrative results.
6. Separation of theoretical book content and anonymized respondent narratives in get_context_for_query, plus context length bounding.
7. Non-destructive index status check (check_index_status) when Qdrant collection is missing or out of sync.
8. Integration with CrowdAssociationService v2 in analyze_user_associations and canonical ё/е normalization in search_associations.
9. Absence of user query text leakage in logs.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rag import ExistentialRAG, RAGResult


@pytest.fixture
def sample_data_dir(tmp_path: Path) -> Path:
    dataset = [
        {
            "id": 1,
            "associations": {
                "freedom": ["полёт", "небо"],
                "nonsense": ["пустота"],
                "solitude": ["тишина"],
                "death": ["конец"],
            },
            "narratives": {
                "about_self": "Я часто размышляю о смысле жизни и одиночестве в большом городе.",
                "free_form": "Боюсь потерять свободу выбора и застрять в рутине.",
            },
            "demographics": {"age": "25", "gender": "Женский"},
        },
        {
            "id": 2,
            "associations": {
                "freedom": ["ветер"],
                "nonsense": ["хаос"],
                "solitude": ["комната"],
                "death": ["темнота"],
            },
            "narratives": {
                "about_self": "Ищу опору в близких людях и книгах.",
            },
            "demographics": {"age": "31", "gender": "Мужской"},
        },
    ]

    association_index = {
        "полет": {"freedom": [1]}, "небо": {"freedom": [1]}, "ветер": {"freedom": [2]},
        "пустота": {"nonsense": [1]}, "хаос": {"nonsense": [2]},
        "тишина": {"solitude": [1]}, "комната": {"solitude": [2]},
        "конец": {"death": [1]}, "темнота": {"death": [2]},
    }

    rag_chunks = [
        {
            "id": "1_self",
            "record_id": 1,
            "type": "self_description",
            "demographics": {"age": "25", "gender": "Женский"},
            "text": "Я часто размышляю о смысле жизни и одиночестве в большом городе.",
        },
        {
            "id": "1_self_dup",
            "record_id": 1,
            "type": "self_description",
            "demographics": {"age": "25", "gender": "Женский"},
            "text": "Я часто размышляю о смысле жизни и одиночестве в большом городе!",
        },
        {
            "id": "1_free",
            "record_id": 1,
            "type": "free_form",
            "demographics": {"age": "25", "gender": "Женский"},
            "text": "Боюсь потерять свободу выбора и застрять в бесконечной рутине.",
        },
    ]

    book_chunks = [
        {
            "id": "yalom_1",
            "book_title": "Экзистенциальная психотерапия",
            "author": "Ирвин Ялом",
            "chapter": "Глава 1. Смерть",
            "page": 42,
            "text": "Экзистенциальный конфликт возникает из столкновения человека с данностями существования и тревогой смерти.",
            "chunk_index": 0,
        },
        {
            "id": "frankl_1",
            "book_title": "Человек в поисках смысла",
            "author": "Виктор Франкл",
            "chapter": "Логотерапия",
            "page": 18,
            "text": "Стремление к смыслу жизни является врождённой мотивационной тенденцией человека.",
            "chunk_index": 1,
        },
    ]

    (tmp_path / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "association_index.json").write_text(json.dumps(association_index, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "narrative_chunks.json").write_text(json.dumps(rag_chunks, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "book_chunks.json").write_text(json.dumps(book_chunks, ensure_ascii=False), encoding="utf-8")

    return tmp_path


class FakeEmbedder:
    def encode(self, texts, show_progress_bar=False, **kwargs):
        return [[0.1, 0.2, 0.3] for _ in texts]


def make_scored_point(score: float, payload: dict):
    return SimpleNamespace(score=score, payload=payload)


# 1. Empty and invalid queries
def test_empty_search_returns_empty_results(sample_data_dir: Path):
    rag = ExistentialRAG(data_dir=sample_data_dir, use_local_embeddings=False)

    for empty_query in ("", "   ", "\n\t"):
        assert rag.search_similar_narratives(empty_query) == []
        assert rag.search_books(empty_query) == []
        assert rag.search_narratives_only(empty_query) == []
        assert rag.get_context_for_query(empty_query) == ""
        assert rag.search_associations(empty_query) == []
        assert rag.search_similar_associations(empty_query) == []

    assert rag.search_similar_narratives("смысл жизни", n_results=0) == []


# 2. Irrelevant results & configurable relevance threshold
def test_irrelevant_results_filtered_and_threshold_is_configurable(sample_data_dir: Path):
    qdrant_mock = MagicMock()
    qdrant_mock.get_collections.return_value = SimpleNamespace(collections=[])
    qdrant_mock.query_points.return_value = SimpleNamespace(
        points=[
            make_scored_point(
                0.28,
                {
                    "text": "Слабое совпадение ниже порога по умолчанию.",
                    "source": "dataset",
                    "type": "free_form",
                    "record_id": 10,
                },
            ),
            make_scored_point(
                0.42,
                {
                    "text": "Умеренное совпадение по теме одиночества.",
                    "source": "dataset",
                    "type": "self_description",
                    "record_id": 11,
                },
            ),
            make_scored_point(
                0.78,
                {
                    "text": "Высокорелевантный фрагмент книги о смысле.",
                    "source": "book",
                    "book_title": "Экзистенциальная психотерапия",
                    "author": "Ялом",
                    "chapter": "Введение",
                },
            ),
        ]
    )

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        min_relevance=0.35,
        qdrant_client=qdrant_mock,
        embedder=FakeEmbedder(),
    )

    # Default threshold 0.35 filters out 0.28
    default_results = rag.search_similar_narratives("запрос про одиночество и смысл", n_results=5)
    assert [r.relevance for r in default_results] == [0.78, 0.42]

    # Raising threshold via per-call argument filters out 0.42 as well
    strict_results = rag.search_similar_narratives(
        "запрос про одиночество и смысл",
        n_results=5,
        min_relevance=0.60,
    )
    assert len(strict_results) == 1
    assert strict_results[0].relevance == 0.78

    # Lowering threshold allows inspecting weak hits when needed
    lenient_results = rag.search_similar_narratives(
        "запрос про одиночество и смысл",
        n_results=5,
        min_relevance=0.25,
    )
    assert [r.relevance for r in lenient_results] == [0.78, 0.42, 0.28]


# 3. Deduplication of semantic and keyword results
def test_deduplication_keeps_highest_scoring_result(sample_data_dir: Path):
    qdrant_mock = MagicMock()
    qdrant_mock.get_collections.return_value = SimpleNamespace(collections=[])
    qdrant_mock.query_points.return_value = SimpleNamespace(
        points=[
            make_scored_point(
                0.61,
                {
                    "text": "Я часто размышляю о смысле жизни и одиночестве в большом городе.",
                    "source": "dataset",
                    "type": "self_description",
                    "record_id": 1,
                },
            ),
            make_scored_point(
                0.84,
                {
                    # Only differs by punctuation/case/ё-е
                    "text": "  я часто размышляю о смысле жизни и одиночестве в большом городе! ",
                    "source": "dataset",
                    "type": "self_description",
                    "record_id": 1,
                },
            ),
            make_scored_point(
                0.55,
                {
                    "text": "Другой уникальный текст респондента.",
                    "source": "dataset",
                    "type": "free_form",
                    "record_id": 2,
                },
            ),
        ]
    )

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        min_relevance=0.35,
        qdrant_client=qdrant_mock,
        embedder=FakeEmbedder(),
    )

    results = rag.search_similar_narratives("смысл жизни и одиночество", n_results=5)
    assert len(results) == 2
    assert results[0].relevance == 0.84
    assert results[1].relevance == 0.55


# 4. Keyword fallback when Qdrant / embedder is unavailable or raises an error
def test_keyword_fallback_without_qdrant_searches_books_and_narratives(sample_data_dir: Path):
    rag = ExistentialRAG(data_dir=sample_data_dir, use_local_embeddings=False)
    assert rag.qdrant_client is None
    assert rag.embedder is None

    # Query matching both a narrative and Frankl's book chunk on "смысле жизни"
    results = rag.search_similar_narratives("размышляю о смысле жизни и одиночестве", n_results=5)
    assert len(results) >= 2
    # Duplicate narrative chunk must be deduplicated in keyword fallback too
    texts_norm = [rag._normalize_text_for_dedup(r.content) for r in results]
    assert len(texts_norm) == len(set(texts_norm))

    for res in results:
        assert res.metadata["retrieval_method"] == "keyword_fallback"
        assert res.relevance <= 0.85

    # Book-only search via fallback
    book_hits = rag.search_books("экзистенциальный конфликт и столкновение с данностями", n_results=3)
    assert len(book_hits) == 1
    assert book_hits[0].source == "book"
    assert book_hits[0].metadata["source"] == "book"
    assert book_hits[0].metadata["source_kind"] == "book_theory"
    assert book_hits[0].metadata["book_title"] == "Экзистенциальная психотерапия"
    assert book_hits[0].metadata["author"] == "Ирвин Ялом"
    assert book_hits[0].metadata["retrieval_method"] == "keyword_fallback"

    # Stopwords-only or completely unrelated query should NOT return arbitrary fallback hits
    assert rag.search_similar_narr("что это как для или когда") == [] if hasattr(rag, "search_similar_narr") else True
    assert rag.search_similar_narratives("что это как для или когда") == []
    assert rag.search_similar_narratives("квантовая хромодинамика синхрофазотрона") == []


def test_semantic_search_exception_falls_back_cleanly(sample_data_dir: Path):
    broken_qdrant = MagicMock()
    broken_qdrant.get_collections.return_value = SimpleNamespace(collections=[])
    broken_qdrant.query_points.side_effect = RuntimeError("Qdrant storage locked")

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        qdrant_client=broken_qdrant,
        embedder=FakeEmbedder(),
    )

    results = rag.search_similar_narratives("потерять свободу выбора в рутине", n_results=3)
    assert len(results) == 1
    assert results[0].metadata["retrieval_method"] == "keyword_fallback"
    assert results[0].metadata["record_id"] == 1


# 5. Unified RAGResult representation and metadata preservation
def test_rag_result_unified_structure_and_metadata_preservation(sample_data_dir: Path):
    qdrant_mock = MagicMock()
    qdrant_mock.get_collections.return_value = SimpleNamespace(collections=[])
    qdrant_mock.query_points.return_value = SimpleNamespace(
        points=[
            make_scored_point(
                0.82,
                {
                    "text": "Теоретический фрагмент про логотерапию.",
                    "source": "book",
                    "book_title": "Человек в поисках смысла",
                    "author": "Виктор Франкл",
                    "chapter": "Часть 2",
                    "page": 105,
                    "original_id": "book_frankl_2",
                },
            ),
            make_scored_point(
                0.74,
                {
                    "text": "Личный рассказ респондента о школе.",
                    "source": "dataset",
                    "type": "school_story",
                    "record_id": 7,
                    "demographics": {"age": "22", "gender": "Женский"},
                    "original_id": "7_school",
                },
            ),
        ]
    )

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        qdrant_client=qdrant_mock,
        embedder=FakeEmbedder(),
    )

    results = rag.search_similar_narratives("смысл и школа", n_results=5)
    assert len(results) == 2

    book_res, narr_res = results[0], results[1]
    assert isinstance(book_res, RAGResult)
    assert book_res.text == book_res.content == "Теоретический фрагмент про логотерапию."
    assert book_res.source == "book"
    assert book_res.metadata["source"] == "book"
    assert book_res.metadata["source_kind"] == "book_theory"
    assert book_res.metadata["book_title"] == "Человек в поисках смысла"
    assert book_res.metadata["author"] == "Виктор Франкл"
    assert book_res.metadata["chapter"] == "Часть 2"
    assert book_res.metadata["page"] == 105
    assert "text" not in book_res.metadata
    assert book_res.to_dict()["text"] == book_res.content

    assert narr_res.source == "dataset_school_story"
    assert narr_res.metadata["source"] == "dataset"
    assert narr_res.metadata["source_kind"] == "respondent_narrative"
    assert narr_res.metadata["type"] == "school_story"
    assert narr_res.metadata["record_id"] == 7
    assert narr_res.metadata["demographics"] == {"age": "22", "gender": "Женский"}
    assert "text" not in narr_res.metadata


# 6. Clear separation of books vs respondent narratives & context length cap
def test_get_context_separates_books_and_narratives_and_limits_size(sample_data_dir: Path):
    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        max_context_chars=600,
        max_chunk_chars=120,
    )

    context = rag.get_context_for_query(
        "размышляю о смысле жизни и столкновении человека с тревогой смерти",
        max_chunks=4,
    )
    assert context != ""
    assert "Теоретический материал из книг:" in context
    assert "Анонимизированные истории респондентов" in context
    assert "не характеристика пользователя" in context
    assert len(context) <= 600


# 7. Index status check without destructive auto-reindexing
def test_check_index_status_warns_without_modifying_qdrant(sample_data_dir: Path):
    qdrant_mock = MagicMock()
    # Case A: Collection does not exist
    qdrant_mock.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name="existential_associations")]
    )

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        qdrant_client=qdrant_mock,
        embedder=FakeEmbedder(),
    )

    status = rag.check_index_status()
    assert status["collection_exists"] is False
    assert status["needs_reindex"] is True
    assert status["expected_total"] == 5  # 3 narrative chunks + 2 book chunks
    # Must NOT auto-create or auto-delete collections on init
    qdrant_mock.create_collection.assert_not_called()
    qdrant_mock.delete_collection.assert_not_called()
    qdrant_mock.upload_points.assert_not_called()

    # Case B: Collection exists but point count mismatches source chunks
    qdrant_mock.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name="existential_therapy")]
    )
    qdrant_mock.get_collection.return_value = SimpleNamespace(points_count=2)

    status_mismatch = rag.check_index_status()
    assert status_mismatch["collection_exists"] is True
    assert status_mismatch["indexed_points"] == 2
    assert status_mismatch["needs_reindex"] is True


def test_explicit_index_loads_canonical_narratives_and_books(sample_data_dir: Path):
    import numpy as np
    from qdrant_client import QdrantClient

    class IndexEmbedder:
        def encode(self, texts, **kwargs):
            return np.ones((len(texts), 384), dtype=np.float32)

    client = QdrantClient(":memory:")
    try:
        rag = ExistentialRAG(data_dir=sample_data_dir, qdrant_client=client, embedder=IndexEmbedder())
        rag._index_all_chunks()
        status = rag.check_index_status()
        assert status["indexed_points"] == 5
        assert status["indexed_books"] == 2
        assert status["needs_reindex"] is False
        points, _ = client.scroll("existential_therapy", limit=10, with_payload=True)
        assert sum(point.payload["source"] == "dataset" for point in points) == 3
        assert sum(point.payload["source"] == "book" for point in points) == 2
        assert all("associations" not in point.payload for point in points)
    finally:
        client.close()


def test_legacy_narrative_file_is_not_loaded(tmp_path: Path):
    (tmp_path / "rag_chunks.json").write_text('[{"id":"old","text":"legacy"}]', encoding="utf-8")
    rag = ExistentialRAG(data_dir=tmp_path, use_local_embeddings=False)
    assert rag._get_narrative_source_chunks() == []


# 8. CrowdAssociationService v2 integration & canonical ё/е association search
def test_associations_use_yo_normalization_and_crowd_service_v2(sample_data_dir: Path):
    crowd_mock = MagicMock()
    crowd_mock.search_similar_associations.return_value = [SimpleNamespace(term="полет", given="freedom")]
    crowd_mock.aggregate_given_evidence.return_value = SimpleNamespace(
        to_dict=lambda: {
            "primary": "freedom",
            "secondary": "solitude",
            "confidence": "moderate",
            "evidence": [{"given": "freedom", "term": "полет", "weight": 0.9}],
        }
    )

    rag = ExistentialRAG(
        data_dir=sample_data_dir,
        use_local_embeddings=False,
        crowd_service=crowd_mock,
    )

    # Search with 'полет' (е) matches 'полёт' (ё) in index
    matches = rag.search_associations("полет")
    assert len(matches) == 1
    assert matches[0]["id"] == 1
    assert matches[0]["matched_givens"] == "freedom"

    analysis = rag.analyze_user_associations({"freedom": ["полет", "полёт"]})
    # Duplicate normalized words in the same given are not counted twice
    assert len(analysis["matched_patterns"]) == 1
    assert analysis["matched_patterns"][0]["count"] == 1
    assert analysis["suggested_themes"] == ["freedom", "solitude"]
    assert analysis["crowd_hypothesis"]["primary"] == "freedom"
    assert len(analysis["semantic_evidence"]) == 1
    assert "not a clinical assessment" in analysis["epistemic_note"]


# 9. Privacy: no user query text logged
def test_user_query_content_is_not_logged(sample_data_dir: Path, caplog, capsys):
    rag = ExistentialRAG(data_dir=sample_data_dir, use_local_embeddings=False)
    secret_marker = "СЕКРЕТНЫЙ_ЛИЧНЫЙ_ТЕКСТ_ПОЛЬЗОВАТЕЛЯ_98765"

    with caplog.at_level(logging.DEBUG):
        rag.search_similar_narratives(f"размышляю о смысле жизни {secret_marker}")

    captured = capsys.readouterr()
    assert secret_marker not in captured.out
    assert secret_marker not in captured.err
    assert secret_marker not in caplog.text
