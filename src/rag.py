"""
RAG Pipeline для экзистенциального терапевта.
Интегрирует теоретический корпус книг, анонимизированные нарративы респондентов
и сервис анализа ассоциаций CrowdAssociationService v2.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False

try:
    from src.crowd_artifacts import normalize_association_term
except ImportError:
    try:
        from crowd_artifacts import normalize_association_term
    except ImportError:
        def normalize_association_term(term: str) -> str:
            if not term:
                return ""
            t = term.lower().replace("ё", "е").strip()
            return re.sub(r"^[^\w]+|[^\w]+$", "", t)


logger = logging.getLogger(__name__)

GIVENS = ("freedom", "nonsense", "solitude", "death")
DEFAULT_COLLECTION = "existential_therapy"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_STOPWORDS_RU = frozenset({
    "это", "как", "так", "ибо", "или", "что", "кто", "где", "когда", "уже",
    "еще", "ещё", "для", "при", "под", "над", "без", "про", "через", "после",
    "перед", "между", "очень", "просто", "только", "даже", "тоже", "либо",
    "если", "хотя", "чтобы", "будто", "словно", "точно", "почти", "совсем",
    "вообще", "всегда", "иногда", "сейчас", "теперь", "потом", "затем", "тогда",
    "здесь", "тут", "там", "куда", "откуда", "почему", "зачем", "какой",
    "какая", "какое", "какие", "которая", "который", "которое", "которые",
    "свой", "своя", "свое", "свои", "своих", "своим", "моя", "мое", "мои",
    "моих", "твой", "твоя", "твое", "твои", "наш", "наша", "наше", "наши",
    "ваш", "ваша", "ваше", "ваши", "этот", "эта", "эти", "этих", "этим",
    "этой", "этого", "тот", "та", "те", "тех", "тем", "той", "того", " весь",
    "вся", "все", "всё", "всех", "всем", "сама", "само", "сами", "самих",
    "меня", "мне", "мной", "тебя", "тебе", "тобой", "него", "нее", "неё",
    "них", "ним", "ней", "нами", "вами", "себя", "себе", "собой", "быть",
    "был", "была", "было", "были", "будет", "будут", "буду", "есть", "нет",
})

_STOPWORDS_EN = frozenset({
    "the", "and", "that", "this", "with", "from", "have", "has", "had", "were",
    "was", "are", "been", "being", "what", "when", "where", "which", "who",
    "whom", "whose", "why", "how", "about", "into", "over", "after", "before",
    "between", "under", "again", "further", "then", "once", "here", "there",
    "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "than", "too", "very", "can", "will",
    "just", "should", "now", "your", "their", "our", "they", "them", "his",
    "her", "its", "myself", "yourself", "himself", "herself",
})


@dataclass
class RAGResult:
    """Единое представление результата поиска в RAG."""
    content: str
    source: str
    relevance: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Алиас для content (единый доступ к тексту)."""
        return self.content

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text"] = self.content
        return data


class ExistentialRAG:
    """RAG-система для экзистенциальной терапии."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        use_local_embeddings: bool = True,
        min_relevance: Optional[float] = None,
        keyword_min_relevance: Optional[float] = None,
        max_context_chars: Optional[int] = None,
        max_chunk_chars: Optional[int] = None,
        qdrant_client: Optional[Any] = None,
        embedder: Optional[Any] = None,
        crowd_service: Optional[Any] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.use_local_embeddings = use_local_embeddings

        self.min_relevance = (
            float(min_relevance)
            if min_relevance is not None
            else float(os.getenv("RAG_MIN_RELEVANCE", "0.35"))
        )
        self.keyword_min_relevance = (
            float(keyword_min_relevance)
            if keyword_min_relevance is not None
            else float(os.getenv("RAG_KEYWORD_MIN_RELEVANCE", "0.20"))
        )
        self.max_context_chars = (
            int(max_context_chars)
            if max_context_chars is not None
            else int(os.getenv("RAG_MAX_CONTEXT_CHARS", "1800"))
        )
        self.max_chunk_chars = (
            int(max_chunk_chars)
            if max_chunk_chars is not None
            else int(os.getenv("RAG_MAX_CHUNK_CHARS", "450"))
        )

        # The canonical files contain the crowd artifacts previously named v2.
        self.dataset: list[dict[str, Any]] = self._load_json("dataset.json", default=[])
        self._records_by_id: dict[Any, dict[str, Any]] = {
            r.get("id"): r for r in self.dataset if isinstance(r, dict) and "id" in r
        }

        self.association_index: dict[str, Any] = self._load_json("association_index.json", default={})
        self.narrative_chunks: list[dict[str, Any]] = self._load_json("narrative_chunks.json", default=[])
        self.book_chunks: list[dict[str, Any]] = self._load_json("book_chunks.json", default=[])

        self.embedder = embedder
        self.qdrant_client = qdrant_client
        self.qdrant_collection = DEFAULT_COLLECTION
        self.crowd_service = crowd_service
        self.index_status: dict[str, Any] = {}

        if self.embedder is not None or self.qdrant_client is not None:
            self._init_services_from_injected()
        elif EMBEDDINGS_AVAILABLE and use_local_embeddings:
            self._init_embeddings()

    def _load_json(self, filename: str, default: Any = None) -> Any:
        """Безопасная загрузка JSON файла."""
        fallback = ({} if "index" in filename else []) if default is None else default
        path = self.data_dir / filename
        if not path.exists():
            return fallback
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("[RAG] Failed to load %s: %s", path, exc)
            return fallback

    def _get_narrative_source_chunks(self) -> list[dict[str, Any]]:
        """Возвращает актуальный список чанков пользовательских нарративов."""
        return self.narrative_chunks

    def _init_services_from_injected(self) -> None:
        """Инициализация при переданных извне qdrant_client / embedder."""
        if self.qdrant_client is not None:
            self.index_status = self.check_index_status()
            if self.crowd_service is None:
                self._init_crowd_service()

    def _init_embeddings(self) -> None:
        """Инициализация модели эмбеддингов и подключение к существующему хранилищу Qdrant."""
        if self.embedder is None:
            try:
                self.embedder = SentenceTransformer(DEFAULT_EMBEDDING_MODEL)
            except Exception as exc:
                logger.warning("[RAG] Failed to load embedding model: %s", exc)
                self.embedder = None

        if QDRANT_AVAILABLE and self.embedder is not None:
            self._init_qdrant()

    def _init_qdrant(self) -> None:
        """Подключение к существующему Qdrant без разрушительных операций и авто-переиндексации."""
        qdrant_path = self.data_dir / "qdrant_storage"
        qdrant_path.mkdir(parents=True, exist_ok=True)

        if self.qdrant_client is None:
            try:
                self.qdrant_client = QdrantClient(path=str(qdrant_path))
            except Exception as exc:
                logger.warning("[RAG] Could not connect to Qdrant at %s: %s", qdrant_path, exc)
                self.qdrant_client = None
                return

        self.index_status = self.check_index_status()
        if self.index_status.get("needs_reindex"):
            logger.warning("[RAG] %s", self.index_status.get("message", "Index out of sync."))

        self._init_crowd_service()

    def _init_crowd_service(self) -> None:
        """Подключение CrowdAssociationService v2."""
        if self.crowd_service is not None:
            return
        try:
            try:
                from src.crowd_association_service import CrowdAssociationService
            except ImportError:
                from crowd_association_service import CrowdAssociationService

            self.crowd_service = CrowdAssociationService(
                data_dir=self.data_dir,
                qdrant_client=self.qdrant_client,
                embedder=self.embedder,
            )
        except Exception as exc:
            logger.warning("[RAG] Could not initialize CrowdAssociationService: %s", exc)
            self.crowd_service = None

    def check_index_status(self) -> dict[str, Any]:
        """
        Проверяет актуальность векторного индекса Qdrant относительно исходных чанков.
        Не выполняет автоматического создания/удаления или переиндексации.
        """
        narrative_chunks = self._get_narrative_source_chunks()
        expected_narratives = len(narrative_chunks) if narrative_chunks else 0
        expected_books = len(self.book_chunks) if self.book_chunks else 0
        expected_total = expected_narratives + expected_books

        status: dict[str, Any] = {
            "collection": self.qdrant_collection,
            "qdrant_available": self.qdrant_client is not None,
            "collection_exists": False,
            "indexed_points": 0,
            "expected_narratives": expected_narratives,
            "expected_books": expected_books,
            "expected_total": expected_total,
            "needs_reindex": False,
            "message": "OK",
        }

        if self.qdrant_client is None:
            status["needs_reindex"] = expected_total > 0
            status["message"] = "Qdrant client is not initialized."
            return status

        try:
            collections_resp = self.qdrant_client.get_collections()
            collection_names = [c.name for c in getattr(collections_resp, "collections", [])]
            if self.qdrant_collection not in collection_names:
                status["needs_reindex"] = expected_total > 0
                status["message"] = (
                    f"Collection '{self.qdrant_collection}' does not exist "
                    f"(expected {expected_total} chunks: {expected_narratives} narratives, {expected_books} books). "
                    f"Reindexing is required."
                )
                return status

            status["collection_exists"] = True
            col_info = self.qdrant_client.get_collection(collection_name=self.qdrant_collection)
            points_count = int(getattr(col_info, "points_count", 0) or 0)
            status["indexed_points"] = points_count

            if points_count != expected_total:
                status["needs_reindex"] = True
                status["message"] = (
                    f"Index count mismatch for '{self.qdrant_collection}': "
                    f"indexed={points_count}, expected={expected_total} "
                    f"(narratives={expected_narratives}, books={expected_books}). "
                    f"Run explicit reindexing to synchronize."
                )
            elif expected_books > 0 and hasattr(self.qdrant_client, "count"):
                try:
                    book_filter = Filter(
                        must=[FieldCondition(key="source", match=MatchValue(value="book"))]
                    )
                    indexed_books = int(
                        self.qdrant_client.count(
                            collection_name=self.qdrant_collection,
                            count_filter=book_filter,
                        ).count
                    )
                    status["indexed_books"] = indexed_books
                    if indexed_books != expected_books:
                        status["needs_reindex"] = True
                        status["message"] = (
                            f"Book chunks mismatch in '{self.qdrant_collection}': "
                            f"indexed_books={indexed_books}, expected_books={expected_books}. "
                            f"Run explicit reindexing."
                        )
                except Exception as exc:
                    status["needs_reindex"] = True
                    status["message"] = f"Could not verify book chunk count: {exc}"
        except Exception as exc:
            status["needs_reindex"] = True
            status["message"] = f"Could not inspect collection '{self.qdrant_collection}': {exc}"

        return status

    def _index_all_chunks(self) -> None:
        """
        Явная индексация всех чанков (нарративы + книги) в коллекцию Qdrant.
        Вызывается только вручную при сервисном обслуживании.
        """
        if not self.qdrant_client or not self.embedder:
            logger.warning("[RAG] Cannot index chunks: qdrant_client or embedder is unavailable.")
            return

        collections = [c.name for c in self.qdrant_client.get_collections().collections]
        if self.qdrant_collection not in collections:
            self.qdrant_client.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )

        all_chunks: list[dict[str, Any]] = []
        narrative_chunks = self._get_narrative_source_chunks()

        for chunk in narrative_chunks:
            text = (chunk.get("text") or "").strip()
            if not text:
                continue
            meta: dict[str, Any] = {
                "source": "dataset",
                "source_kind": "respondent_narrative",
                "type": chunk.get("type", "narrative"),
                "record_id": chunk.get("record_id"),
            }
            if "demographics" in chunk and isinstance(chunk["demographics"], dict):
                meta["demographics"] = chunk["demographics"]
            all_chunks.append({
                "id": str(chunk["id"]),
                "text": text,
                "metadata": meta,
            })

        for chunk in self.book_chunks:
            text = (chunk.get("text") or "").strip()
            if not text:
                continue
            all_chunks.append({
                "id": f"book_{chunk['id']}",
                "text": text,
                "metadata": {
                    "source": "book",
                    "source_kind": "book_theory",
                    "book_title": chunk.get("book_title", ""),
                    "author": chunk.get("author", ""),
                    "chapter": chunk.get("chapter", ""),
                    "page": chunk.get("page"),
                },
            })

        if not all_chunks:
            logger.info("[RAG] No chunks available for indexing.")
            return

        batch_size = 128
        for i in range(0, len(all_chunks), batch_size):
            batch = all_chunks[i : i + batch_size]
            embeddings = self.embedder.encode([c["text"] for c in batch], show_progress_bar=False)
            points = []
            for j, chunk in enumerate(batch):
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(chunk["id"])))
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=embeddings[j].tolist(),
                        payload={
                            "text": chunk["text"],
                            "original_id": chunk["id"],
                            **chunk["metadata"],
                        },
                    )
                )
            self.qdrant_client.upload_points(
                collection_name=self.qdrant_collection,
                points=points,
            )
            if i == 0 or i + len(batch) == len(all_chunks) or (i // batch_size + 1) % 10 == 0:
                logger.info("[RAG] Indexed %d/%d chunks", i + len(batch), len(all_chunks))

        self.index_status = self.check_index_status()

    # ------------------------------------------------------------------
    # Работа с ассоциациями (CrowdAssociationService v2 + лексический индекс)
    # ------------------------------------------------------------------

    def search_similar_associations(self, query: str, n_results: int = 30) -> list:
        """Семантический поиск ассоциаций через CrowdAssociationService v2."""
        if not query or not query.strip() or n_results <= 0:
            return []
        if self.crowd_service:
            return self.crowd_service.search_similar_associations(query, n_results=n_results)
        return []

    def aggregate_given_evidence(self, hits: Sequence[Any]) -> Any:
        """Агрегация гипотезы по экзистенциальным данностям через CrowdAssociationService v2."""
        if self.crowd_service:
            return self.crowd_service.aggregate_given_evidence(hits)
        return None

    def search_associations(self, word: str, givens: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Лексический поиск записей респондентов по слову-ассоциации.
        Использует каноническую нормализацию (ё/е, пунктуация) и не дублирует записи.
        """
        norm_word = normalize_association_term(word)
        if not norm_word:
            return []

        def _word_matches(index_word: str, search_word: str) -> bool:
            idx_norm = normalize_association_term(index_word)
            if not idx_norm:
                return False
            if idx_norm == search_word:
                return True
            if len(search_word) >= 4 and len(idx_norm) >= 4:
                return search_word in idx_norm or idx_norm in search_word
            return False

        target_givens = (
            [givens]
            if (givens and givens in GIVENS)
            else list(GIVENS)
        )

        results: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, str]] = set()

        # Canonical format: term -> given -> respondent IDs.
        if self.association_index:
            for index_word, givens_map in self.association_index.items():
                if not isinstance(givens_map, dict) or not _word_matches(index_word, norm_word):
                    continue
                for g in target_givens:
                    for rid in givens_map.get(g, []):
                        dedup_key = (rid, g if givens else "")
                        if dedup_key in seen_keys:
                            continue
                        record = self._records_by_id.get(rid)
                        if record:
                            seen_keys.add(dedup_key)
                            results.append({
                                **record,
                                "matched_givens": g,
                                "matched_word": index_word,
                            })

        return results

    def analyze_user_associations(self, associations: dict[str, list[str]]) -> dict[str, Any]:
        """
        Анализ пользовательских ассоциаций относительно анонимизированного крауд-корпуса.
        Использует CrowdAssociationService v2 как основной источник семантических свидетельств
        при его доступности и дополняет лексическими совпадениями.
        Результаты описывают выборку респондентов, а не психологический диагноз пользователя.
        """
        analysis: dict[str, Any] = {
            "matched_patterns": [],
            "suggested_themes": [],
            "similar_profiles": [],
            "semantic_evidence": [],
            "crowd_hypothesis": {},
            "epistemic_note": (
                "Matches reflect anonymized respondent associations in the study corpus, "
                "not a clinical assessment or psychological fact about the user."
            ),
        }

        if not associations or not isinstance(associations, dict):
            return analysis

        seen_patterns: set[tuple[str, str]] = set()
        query_terms: list[str] = []

        for given, words in associations.items():
            if not isinstance(words, list):
                continue
            for raw_word in words:
                clean_word = (raw_word or "").strip()
                norm_word = normalize_association_term(clean_word)
                if not norm_word:
                    continue
                query_terms.append(clean_word)

                pattern_key = (given, norm_word)
                if pattern_key in seen_patterns:
                    continue
                seen_patterns.add(pattern_key)

                matches = self.search_associations(clean_word, given if given in GIVENS else None)
                if matches:
                    unique_rids = {m.get("id") for m in matches if m.get("id") is not None}
                    analysis["matched_patterns"].append({
                        "givens": given,
                        "word": clean_word,
                        "normalized_word": norm_word,
                        "count": len(unique_rids) or len(matches),
                        "source_kind": "crowd_association_lexical",
                    })

        analysis["matched_patterns"].sort(key=lambda x: x["count"], reverse=True)

        # Основной семантический анализ через CrowdAssociationService v2 (без дублирующего движка)
        if self.crowd_service and query_terms:
            combined_query = ", ".join(query_terms)
            try:
                hits = self.search_similar_associations(combined_query, n_results=15)
                if hits:
                    hypothesis = self.aggregate_given_evidence(hits)
                    if hypothesis is not None:
                        hyp_dict = hypothesis.to_dict() if hasattr(hypothesis, "to_dict") else {}
                        analysis["crowd_hypothesis"] = hyp_dict
                        analysis["semantic_evidence"] = hyp_dict.get("evidence", [])
                        primary = hyp_dict.get("primary")
                        secondary = hyp_dict.get("secondary")
                        if primary:
                            analysis["suggested_themes"].append(primary)
                        if secondary and secondary not in analysis["suggested_themes"]:
                            analysis["suggested_themes"].append(secondary)
            except Exception as exc:
                logger.warning("[RAG] CrowdAssociationService analysis failed: %s", exc)

        return analysis

    # ------------------------------------------------------------------
    # Поиск по теоретическим книгам и пользовательским нарративам
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text_for_dedup(text: str) -> str:
        """Канонизация текста для устранения дубликатов."""
        if not text:
            return ""
        collapsed = re.sub(r"\s+", " ", text.strip().lower().replace("ё", "е"))
        return re.sub(r"[^\w\s]", "", collapsed).strip()

    @staticmethod
    def _classify_payload_source(payload: dict[str, Any]) -> tuple[str, str]:
        """
        Определяет унифицированный идентификатор источника (`source`) и категорию (`source_kind`),
        четко разделяя теоретический материал книг и анонимизированные нарративы респондентов.
        """
        raw_source = str(payload.get("source") or "").lower().strip()
        chunk_type = str(payload.get("type") or "").lower().strip()
        original_id = str(payload.get("original_id") or payload.get("id") or "")

        is_book = (
            raw_source == "book"
            or original_id.startswith("book_")
            or bool(payload.get("book_title"))
        )
        if is_book:
            return "book", "book_theory"

        narrative_subtype = chunk_type if chunk_type and chunk_type != "unknown" else "narrative"
        return f"dataset_{narrative_subtype}", "respondent_narrative"

    @classmethod
    def _build_rag_result_from_payload(
        cls,
        payload: dict[str, Any],
        relevance: float,
        retrieval_method: str = "semantic",
    ) -> Optional[RAGResult]:
        """Создаёт унифицированный RAGResult из payload Qdrant или JSON-чанка."""
        if not isinstance(payload, dict):
            return None

        text = (payload.get("text") or "").strip()
        if not text:
            return None

        source_label, source_kind = cls._classify_payload_source(payload)

        # Копируем метаданные без дублирования полного текста и без протекания
        # сырых ассоциаций респондента как диагноза
        metadata: dict[str, Any] = {}
        for k, v in payload.items():
            if k == "text":
                continue
            metadata[k] = v

        if source_kind == "book_theory":
            metadata["source"] = "book"
            metadata["source_kind"] = "book_theory"
            metadata.setdefault("book_title", payload.get("book_title", ""))
            metadata.setdefault("author", payload.get("author", ""))
            metadata.setdefault("chapter", payload.get("chapter") or "")
        else:
            metadata["source"] = "dataset"
            metadata["source_kind"] = "respondent_narrative"
            metadata.setdefault("type", payload.get("type", "narrative"))
            if "record_id" in payload:
                metadata["record_id"] = payload.get("record_id")

        metadata["retrieval_method"] = retrieval_method

        return RAGResult(
            content=text,
            source=source_label,
            relevance=round(float(relevance), 4),
            metadata=metadata,
        )

    def _deduplicate_and_filter(
        self,
        candidates: Sequence[RAGResult],
        n_results: int,
        threshold: float,
    ) -> list[RAGResult]:
        """
        Фильтрует результаты ниже порога релевантности, устраняет дубликаты
        (по паре source_kind + нормализованный текст или по original_id/id)
        и возвращает top-n результатов по убыванию релевантности.
        """
        if n_results <= 0 or not candidates:
            return []

        best_by_key: dict[str, RAGResult] = {}

        for item in candidates:
            if not item or not item.content or not item.content.strip():
                continue
            if item.relevance < threshold:
                continue

            norm_text = self._normalize_text_for_dedup(item.content)
            if not norm_text:
                continue

            dedup_key = norm_text
            existing = best_by_key.get(dedup_key)
            if existing is None or item.relevance > existing.relevance:
                best_by_key[dedup_key] = item

        deduped = list(best_by_key.values())
        deduped.sort(key=lambda r: r.relevance, reverse=True)
        return deduped[:n_results]

    def _semantic_search(
        self,
        query: str,
        n_results: int = 5,
        source_filter: Optional[str] = None,
        min_relevance: Optional[float] = None,
    ) -> list[RAGResult]:
        """
        Общий семантический поиск по коллекции Qdrant с поддержкой фильтра источника
        ('book', 'dataset' или None для всех), настраиваемым порогом и дедупликацией.
        """
        cleaned_query = (query or "").strip()
        if not cleaned_query or n_results <= 0:
            return []

        threshold = self.min_relevance if min_relevance is None else float(min_relevance)

        if not self.qdrant_client or not self.embedder:
            logger.info("[RAG] Qdrant or embedder unavailable; using keyword fallback.")
            return self._keyword_search(
                cleaned_query,
                n_results=n_results,
                source_filter=source_filter,
                min_relevance=min_relevance,
            )

        try:
            encoded = self.embedder.encode([cleaned_query], show_progress_bar=False)
            first_vec = encoded[0]
            query_embedding = first_vec.tolist() if hasattr(first_vec, "tolist") else list(first_vec)

            q_filter = None
            if source_filter in {"book", "dataset"} and QDRANT_AVAILABLE:
                q_filter = Filter(
                    must=[FieldCondition(key="source", match=MatchValue(value=source_filter))]
                )

            fetch_limit = max(n_results * 3, 10)
            response = self.qdrant_client.query_points(
                collection_name=self.qdrant_collection,
                query=query_embedding,
                limit=fetch_limit,
                query_filter=q_filter,
                with_payload=True,
            )
            points = getattr(response, "points", []) or []

            raw_results: list[RAGResult] = []
            for scored_point in points:
                payload = getattr(scored_point, "payload", None) or {}
                score = float(getattr(scored_point, "score", 0.0) or 0.0)

                result = self._build_rag_result_from_payload(
                    payload=payload,
                    relevance=score,
                    retrieval_method="semantic",
                )
                if result is None:
                    continue

                # На случай, если кастомный/моковый клиент Qdrant не применил query_filter
                if source_filter == "book" and result.metadata.get("source") != "book":
                    continue
                if source_filter == "dataset" and result.metadata.get("source") != "dataset":
                    continue

                raw_results.append(result)

            return self._deduplicate_and_filter(raw_results, n_results=n_results, threshold=threshold)

        except Exception as exc:
            logger.warning("[RAG] Semantic search failed (%s: %s); falling back to keyword search.", type(exc).__name__, exc)
            return self._keyword_search(
                cleaned_query,
                n_results=n_results,
                source_filter=source_filter,
                min_relevance=min_relevance,
            )

    def search_books(
        self,
        query: str,
        n_results: int = 3,
        min_relevance: Optional[float] = None,
    ) -> list[RAGResult]:
        """Семантический поиск исключительно по теоретическому корпусу книг."""
        return self._semantic_search(
            query=query,
            n_results=n_results,
            source_filter="book",
            min_relevance=min_relevance,
        )

    def search_narratives_only(
        self,
        query: str,
        n_results: int = 5,
        min_relevance: Optional[float] = None,
    ) -> list[RAGResult]:
        """Семантический поиск исключительно по анонимизированным нарративам респондентов."""
        return self._semantic_search(
            query=query,
            n_results=n_results,
            source_filter="dataset",
            min_relevance=min_relevance,
        )

    def search_similar_narratives(
        self,
        query: str,
        n_results: int = 5,
        min_relevance: Optional[float] = None,
        source_filter: Optional[str] = None,
    ) -> list[RAGResult]:
        """
        Семантический поиск релевантных фрагментов базы знаний (книг и нарративов)
        с дедупликацией и фильтрацией по порогу релевантности.
        Сохраняет обратную совместимость с вызовами в therapist_bot.py.
        """
        return self._semantic_search(
            query=query,
            n_results=n_results,
            source_filter=source_filter,
            min_relevance=min_relevance,
        )

    @staticmethod
    def _tokenize_for_keyword_search(text: str) -> list[str]:
        """Извлекает значимые токены (>=3 символов, без стоп-слов, ё -> е)."""
        if not text:
            return []
        normalized = text.lower().replace("ё", "е")
        raw_tokens = re.findall(r"[a-zа-я0-9-]+", normalized)
        tokens: list[str] = []
        for tok in raw_tokens:
            tok = tok.strip("-")
            if len(tok) < 3:
                continue
            if tok in _STOPWORDS_RU or tok in _STOPWORDS_EN:
                continue
            tokens.append(tok)
        return tokens

    @staticmethod
    def _stem_key(token: str) -> str:
        """Приближённый префикс основы для длинных слов русского/английского языка."""
        if len(token) >= 7:
            return token[:6]
        if len(token) >= 5:
            return token[:4]
        return token

    def _keyword_search(
        self,
        query: str,
        n_results: int = 5,
        source_filter: Optional[str] = None,
        min_relevance: Optional[float] = None,
    ) -> list[RAGResult]:
        """
        Лексический fallback-поиск по нарративам и книгам.
        Используется при недоступности Qdrant или модели эмбеддингов.
        Помечает результаты `retrieval_method='keyword_fallback'`, не выдавая
        случайные совпадения стоп-слов или подстрок за семантический поиск.
        """
        cleaned_query = (query or "").strip()
        if not cleaned_query or n_results <= 0:
            return []

        query_tokens = self._tokenize_for_keyword_search(cleaned_query)
        if not query_tokens:
            return []

        threshold = (
            self.keyword_min_relevance
            if min_relevance is None
            else float(min_relevance)
        )

        query_token_set = set(query_tokens)
        query_stems = {self._stem_key(t) for t in query_token_set if len(t) >= 5}
        query_bigrams = {
            f"{query_tokens[i]} {query_tokens[i + 1]}"
            for i in range(len(query_tokens) - 1)
        }

        candidate_records: list[dict[str, Any]] = []
        if source_filter in {None, "dataset"}:
            for chunk in self._get_narrative_source_chunks():
                if isinstance(chunk, dict) and chunk.get("text"):
                    payload: dict[str, Any] = {
                        "text": chunk["text"],
                        "source": "dataset",
                        "source_kind": "respondent_narrative",
                        "type": chunk.get("type", "narrative"),
                        "record_id": chunk.get("record_id", ""),
                        "original_id": chunk.get("id", ""),
                    }
                    if "demographics" in chunk:
                        payload["demographics"] = chunk["demographics"]
                    if "associations" in chunk:
                        payload["associations"] = (
                            chunk["associations"]
                            if isinstance(chunk["associations"], str)
                            else json.dumps(chunk.get("associations", {}), ensure_ascii=False)
                        )
                    candidate_records.append(payload)

        if source_filter in {None, "book"}:
            for chunk in self.book_chunks:
                if isinstance(chunk, dict) and chunk.get("text"):
                    candidate_records.append({
                        "text": chunk["text"],
                        "source": "book",
                        "source_kind": "book_theory",
                        "book_title": chunk.get("book_title", ""),
                        "author": chunk.get("author", ""),
                        "chapter": chunk.get("chapter") or "",
                        "page": chunk.get("page"),
                        "original_id": f"book_{chunk.get('id', '')}",
                    })

        scored_results: list[RAGResult] = []
        denom = max(len(query_token_set), 1)

        for payload in candidate_records:
            doc_tokens = self._tokenize_for_keyword_search(payload["text"])
            if not doc_tokens:
                continue

            doc_token_set = set(doc_tokens)
            exact_matches = query_token_set & doc_token_set
            exact_count = len(exact_matches)

            # Морфологически близкие совпадения по основе для несовпавших токенов
            unmatched_query = query_token_set - exact_matches
            doc_stems = {self._stem_key(t) for t in doc_token_set if len(t) >= 5}
            stem_count = sum(
                1 for t in unmatched_query if len(t) >= 5 and self._stem_key(t) in doc_stems
            )

            if exact_count == 0 and stem_count == 0:
                continue

            # Биграммный бонус только для осмысленных пар токенов
            bigram_bonus = 0.0
            if query_bigrams and len(doc_tokens) > 1:
                doc_bigrams = {
                    f"{doc_tokens[i]} {doc_tokens[i + 1]}"
                    for i in range(len(doc_tokens) - 1)
                }
                matched_bigrams = len(query_bigrams & doc_bigrams)
                if matched_bigrams > 0:
                    bigram_bonus = min(0.25, matched_bigrams * 0.15)

            lexical_coverage = (exact_count + 0.5 * stem_count) / denom
            # Для коротких запросов из 1 слова требуем точного совпадения токена
            if len(query_token_set) == 1 and exact_count == 0:
                continue

            # Ограничиваем оценку сверху (< 0.85), чтобы лексический fallback
            # не маскировался под идеальное векторное сходство
            relevance = min(0.85, lexical_coverage * 0.75 + bigram_bonus)

            if relevance >= threshold:
                rag_res = self._build_rag_result_from_payload(
                    payload=payload,
                    relevance=relevance,
                    retrieval_method="keyword_fallback",
                )
                if rag_res is not None:
                    scored_results.append(rag_res)

        return self._deduplicate_and_filter(
            scored_results,
            n_results=n_results,
            threshold=threshold,
        )

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Аккуратно ограничивает длину текста чанка без обрыва посередине слова."""
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if max_chars <= 0 or len(cleaned) <= max_chars:
            return cleaned
        cut = cleaned[:max_chars].rsplit(" ", 1)[0]
        return (cut if len(cut) >= max_chars // 2 else cleaned[:max_chars]).rstrip(".,;:- ") + "…"

    def get_context_for_query(
        self,
        query: str,
        max_chunks: int = 3,
        max_chars: Optional[int] = None,
        min_relevance: Optional[float] = None,
    ) -> str:
        """
        Формирует контекст для LLM с явным разделением:
        1) теоретического материала из книг;
        2) анонимизированных историй респондентов (с пометкой, что это опыт других людей,
           а не утверждение о психологии текущего пользователя).
        Ограничивает как длину отдельного фрагмента, так и общий объём контекста.
        """
        results = self.search_similar_narratives(
            query,
            n_results=max_chunks,
            min_relevance=min_relevance,
        )
        if not results:
            return ""

        total_budget = self.max_context_chars if max_chars is None else int(max_chars)
        if total_budget <= 0:
            return ""

        book_results = [
            r for r in results
            if r.metadata.get("source") == "book" or r.source == "book"
        ]
        narrative_results = [
            r for r in results
            if r not in book_results
        ]

        sections: list[str] = ["Релевантный контекст:"]
        current_len = len(sections[0])
        item_idx = 1

        def _append_block(block: str) -> bool:
            nonlocal current_len
            if current_len + len(block) + 2 > total_budget:
                remaining = total_budget - current_len - 2
                if remaining < 80:
                    return False
                block = self._truncate_text(block, remaining)
            sections.append(block)
            current_len += len(block) + 1
            return True

        if book_results:
            header = "\nТеоретический материал из книг:"
            if _append_block(header):
                for res in book_results:
                    author = (res.metadata.get("author") or "").strip()
                    book = (res.metadata.get("book_title") or "").strip()
                    chapter = (res.metadata.get("chapter") or "").strip()
                    mode_tag = (
                        " [лексический fallback]"
                        if res.metadata.get("retrieval_method") == "keyword_fallback"
                        else ""
                    )
                    title_part = f"{author} — «{book}»" if (author or book) else "Книжный источник"
                    if chapter:
                        title_part += f" ({chapter})"
                    snippet = self._truncate_text(res.content, self.max_chunk_chars)
                    entry = (
                        f"[{item_idx}] 📚 {title_part}{mode_tag}\n"
                        f"{snippet}\n"
                        f"(релевантность: {res.relevance:.2f})"
                    )
                    if not _append_block(entry):
                        break
                    item_idx += 1

        if narrative_results:
            header = (
                "\nАнонимизированные истории респондентов "
                "(опыт других людей из опроса, не характеристика пользователя):"
            )
            if _append_block(header):
                for res in narrative_results:
                    narrative_type = res.metadata.get("type", "narrative")
                    mode_tag = (
                        " [лексический fallback]"
                        if res.metadata.get("retrieval_method") == "keyword_fallback"
                        else ""
                    )
                    snippet = self._truncate_text(res.content, self.max_chunk_chars)
                    entry = (
                        f"[{item_idx}] 📝 Нарратив респондента ({narrative_type}){mode_tag}\n"
                        f"{snippet}\n"
                        f"(релевантность: {res.relevance:.2f})"
                    )
                    if not _append_block(entry):
                        break
                    item_idx += 1

        if item_idx == 1:
            return ""

        return "\n".join(sections)
