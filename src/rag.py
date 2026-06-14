"""
RAG Pipeline для экзистенциального терапевта.
Интегрирует книги, датасет ассоциаций и нарративы.
"""

import json
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False



try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False


@dataclass
class RAGResult:
    """Результат поиска в RAG."""
    content: str
    source: str
    relevance: float
    metadata: dict


class ExistentialRAG:
    """RAG система для экзистенциальной терапии."""
    
    def __init__(self, data_dir: str = None, use_local_embeddings: bool = True):
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.use_local_embeddings = use_local_embeddings
        
        # Загружаем данные
        self.dataset = self._load_json("dataset.json")
        self.association_index = self._load_json("association_index.json")
        self.rag_chunks = self._load_json("rag_chunks.json")
        self.book_chunks = self._load_json("book_chunks.json")        
        # Инициализируем эмбеддинги
        self.embedder = None
        self.collection = None
        
        # Qdrant client (инициализируем ДО вызова _init_embeddings)
        self.qdrant_client = None
        self.qdrant_collection = "existential_therapy"
        
        if EMBEDDINGS_AVAILABLE and use_local_embeddings:
            self._init_embeddings()

    
    def _load_json(self, filename: str) -> dict | list:

        """Загрузка JSON файла."""
        path = self.data_dir / filename
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {} if "index" in filename else []
    
    def _init_embeddings(self):
        """Инициализация модели эмбеддингов."""
        print("Загрузка модели эмбеддингов...")
        # Используем многоязычную модель
        # Добавляем trust_remote_code=True и локальные файлы если есть
        try:
            self.embedder = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
        except Exception as e:
            print(f"Ошибка загрузки модели эмбеддингов: {e}")
            print("Попытка использовать локальную модель или альтернативный метод...")
            # Здесь можно добавить логику для загрузки локальной модели, если она скачана вручную
            # Или просто отключить эмбеддинги
            global EMBEDDINGS_AVAILABLE
            EMBEDDINGS_AVAILABLE = False
            self.embedder = None        
        if QDRANT_AVAILABLE:
            self._init_qdrant()
    
    def _init_qdrant(self):
        """Инициализация Qdrant."""
        qdrant_path = self.data_dir / "qdrant_storage"
        qdrant_path.mkdir(exist_ok=True)
        
        self.qdrant_client = QdrantClient(path=str(qdrant_path))
        
        # Проверяем существование коллекции
        collections = self.qdrant_client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if self.qdrant_collection not in collection_names:
            # Создаём коллекцию с cosine distance (384 dims for MiniLM)
            self.qdrant_client.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE)
            )
            print(f"Создана коллекция {self.qdrant_collection}")
            self._index_all_chunks()
        else:
            print(f"Коллекция {self.qdrant_collection} уже существует")

    def _index_all_chunks(self):
        """Индексация всех чанков (датасет + книги) в Qdrant."""
        all_chunks = []
        
        # Чанки из датасета
        if self.rag_chunks:
            for chunk in self.rag_chunks:
                all_chunks.append({
                    "id": chunk["id"],
                    "text": chunk["text"],
                    "metadata": {
                        "source": "dataset",
                        "type": chunk.get("type", "narrative"),
                        "record_id": chunk.get("record_id"),
                        "associations": json.dumps(chunk.get("associations", {}), ensure_ascii=False)
                    }
                })
        
        # Чанки из книг
        if self.book_chunks:
            for chunk in self.book_chunks:
                all_chunks.append({
                    "id": f"book_{chunk['id']}",
                    "text": chunk["text"],
                    "metadata": {
                        "source": "book",
                        "book_title": chunk.get("book_title", ""),
                        "author": chunk.get("author", ""),
                        "chapter": chunk.get("chapter", "")
                    }
                })
        
        if not all_chunks:
            print("Нет данных для индексации")
            return
        
        print(f"Индексация {len(all_chunks)} чанков...")
        print(f"  - Датасет: {len(self.rag_chunks) if self.rag_chunks else 0}")
        print(f"  - Книги: {len(self.book_chunks) if self.book_chunks else 0}")
        
        texts = [chunk["text"] for chunk in all_chunks]
        embeddings = self.embedder.encode(texts, show_progress_bar=True)
        
        # Разбиваем на батчи по 1000 для Qdrant
        batch_size = 1000
        total_chunks = len(all_chunks)
        
        for i in range(0, total_chunks, batch_size):
            end_idx = min(i + batch_size, total_chunks)
            print(f"  Добавление в базу: {i} - {end_idx} из {total_chunks}")
            
            points = []
            for j in range(i, end_idx):
                chunk = all_chunks[j]
                # Generate deterministic UUID from string ID
                point_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(chunk["id"]))
                points.append(PointStruct(
                    id=point_id,
                    vector=embeddings[j].tolist(),
                    payload={
                        "text": chunk["text"],
                        "original_id": chunk["id"],
                        **chunk["metadata"]
                    }
                ))

            
            self.qdrant_client.upload_points(
                collection_name=self.qdrant_collection,
                points=points
            )
        
        print("Индексация завершена")

    def search_associations(self, word: str, givens: str = None) -> list[dict]:
        """Поиск записей по ассоциации."""
        word = word.lower().strip()
        results = []
        
        # Helper function to check if word matches (exact or substring)
        def word_matches(index_word: str, search_word: str) -> bool:
            index_lower = index_word.lower()
            search_lower = search_word.lower()
            # Exact match
            if index_lower == search_lower:
                return True
            # Substring match (if search word is 4+ chars)
            if len(search_word) >= 4 and (search_lower in index_lower or index_lower in search_lower):
                return True
            return False
        
        if givens and givens in self.association_index:
            # Search with fuzzy matching
            for index_word, record_ids in self.association_index[givens].items():
                if word_matches(index_word, word):
                    for rid in record_ids:
                        record = next((r for r in self.dataset if r["id"] == rid), None)
                        if record:
                            results.append({**record, "matched_givens": givens, "matched_word": index_word})
        else:
            # Ищем во всех данностях с fuzzy matching
            for g in ["freedom", "nonsense", "solitude", "death"]:
                for index_word, record_ids in self.association_index.get(g, {}).items():
                    if word_matches(index_word, word):
                        for rid in record_ids:
                            record = next((r for r in self.dataset if r["id"] == rid), None)
                            if record and not any(r["id"] == rid for r in results):  # Avoid duplicates
                                results.append({**record, "matched_givens": g, "matched_word": index_word})
        
        return results

    
    def search_similar_narratives(self, query: str, n_results: int = 5) -> list[RAGResult]:
        """Семантический поиск похожих нарративов."""
        print(f"[RAG] Searching for similar narratives, query length: {len(query)} chars")
        
        if not self.qdrant_client or not self.embedder:
            print(f"[RAG] Qdrant or embedder not available, using keyword fallback")
            return self._keyword_search(query, n_results)
        
        try:
            print(f"[RAG] Using semantic search with embedder: {self.embedder}")
            query_embedding = self.embedder.encode([query])[0].tolist()
            
            results = self.qdrant_client.query_points(
                collection_name=self.qdrant_collection,
                query=query_embedding,
                limit=n_results * 2,  # Get more results for better filtering
                with_payload=True
            ).points

            
            print(f"[RAG] Qdrant returned {len(results)} results")
            
            rag_results = []
            for i, scored_point in enumerate(results):
                payload = scored_point.payload
                relevance = scored_point.score
                
                # Only include results with reasonable relevance
                if relevance > 0.3:
                    rag_results.append(RAGResult(
                        content=payload.get("text", ""),
                        source=f"dataset_{payload.get('type', 'unknown')}",
                        relevance=relevance,
                        metadata={k: v for k, v in payload.items() if k != "text"}
                    ))
                    print(f"[RAG] Result {i}: relevance={relevance:.3f}, source={payload.get('type', 'unknown')}")
            
            # Sort by relevance and return top n_results
            rag_results.sort(key=lambda x: x.relevance, reverse=True)
            final_results = rag_results[:n_results]
            print(f"[RAG] Returning {len(final_results)} results above 0.3 relevance threshold")
            return final_results
            
        except Exception as e:
            print(f"[RAG ERROR] Semantic search failed: {type(e).__name__}: {e}")
            print(f"[RAG] Falling back to keyword search")
            return self._keyword_search(query, n_results)


    
    def _keyword_search(self, query: str, n_results: int = 5) -> list[RAGResult]:
        """Улучшенный ключевой поиск с учётом семантической близости (fallback)."""
        print(f"[RAG] Running keyword fallback search for: {query[:100]}...")
        results = []
        query_lower = query.lower()
        query_words = set(w.strip('.,!?;:"()[]{}') for w in query_lower.split() if len(w) > 3)
        
        print(f"[RAG] Query words: {query_words}")
        
        for chunk in self.rag_chunks:
            chunk_text = chunk["text"].lower()
            chunk_words = set(w.strip('.,!?;:"()[]{}') for w in chunk_text.split() if len(w) > 3)
            
            # Calculate overlap
            overlap = len(query_words & chunk_words)
            
            # Also check for phrase matches (more weight)
            phrase_score = 0
            if len(query) > 10:
                # Check if any significant phrase from query appears in chunk
                query_phrases = [query_lower[i:i+20] for i in range(0, len(query_lower)-20, 10)]
                for phrase in query_phrases:
                    if phrase in chunk_text:
                        phrase_score += 0.5
            
            if overlap > 0 or phrase_score > 0:
                relevance = (overlap / max(len(query_words), 1)) * 0.5 + min(phrase_score, 1.0) * 0.5
                if relevance > 0.1:  # Minimum threshold
                    # Include associations metadata from the chunk
                    metadata = {
                        "record_id": chunk.get("record_id", ""),
                        "associations": json.dumps(chunk.get("associations", {}), ensure_ascii=False),
                        "type": chunk.get("type", "unknown")
                    }
                    results.append(RAGResult(
                        content=chunk["text"],
                        source=f"dataset_{chunk.get('type', 'unknown')}",
                        relevance=relevance,
                        metadata=metadata
                    ))
        
        results.sort(key=lambda x: x.relevance, reverse=True)
        print(f"[RAG] Keyword search found {len(results)} matches, returning top {min(n_results, len(results))}")
        for i, r in enumerate(results[:3]):
            print(f"[RAG] Top match {i+1}: relevance={r.relevance:.3f}, text={r.content[:80]}...")
        
        return results[:n_results]


    
    def get_context_for_query(self, query: str, max_chunks: int = 3) -> str:
        """Получить контекст для запроса."""
        results = self.search_similar_narratives(query, max_chunks)
        
        if not results:
            return ""
        
        context_parts = ["Релевантный контекст:\n"]
        
        for i, result in enumerate(results, 1):
            source = result.metadata.get("source", "unknown")
            
            if source == "book":
                author = result.metadata.get("author", "")
                book = result.metadata.get("book_title", "")
                chapter = result.metadata.get("chapter", "")
                source_info = f"📚 {author} — «{book}»"
                if chapter:
                    source_info += f" ({chapter})"
            else:
                source_info = f"📝 История из датасета"
            
            context_parts.append(f"[{i}] {source_info}")
            context_parts.append(f"{result.content[:500]}...")
            context_parts.append(f"(релевантность: {result.relevance:.2f})\n")
        
        return "\n".join(context_parts)    
    def analyze_user_associations(self, associations: dict[str, list[str]]) -> dict:
        """Анализ ассоциаций пользователя."""
        analysis = {
            "matched_patterns": [],
            "suggested_themes": [],
            "similar_profiles": []
        }
        
        for givens, words in associations.items():
            for word in words:
                matches = self.search_associations(word, givens)
                if matches:
                    analysis["matched_patterns"].append({
                        "givens": givens,
                        "word": word,
                        "count": len(matches)
                    })
        
        # Сортируем паттерны по частоте
        analysis["matched_patterns"].sort(key=lambda x: x["count"], reverse=True)
        
        return analysis
