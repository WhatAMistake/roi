"""
RAG Pipeline for existential therapist.
Integrates books, association dataset and narratives.
"""


import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False


@dataclass
class RAGResult:
    """RAG search result."""
    content: str
    source: str
    relevance: float
    metadata: dict



class ExistentialRAG:
    """RAG system for existential therapy."""
    
    def __init__(self, data_dir: str = None, use_local_embeddings: bool = True):
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.use_local_embeddings = use_local_embeddings
        
        self.dataset = self._load_json("dataset.json")
        self.association_index = self._load_json("association_index.json")
        self.rag_chunks = self._load_json("rag_chunks.json")
        self.book_chunks = self._load_json("book_chunks.json")        
        self.embedder = None
        self.collection = None
        
        if EMBEDDINGS_AVAILABLE and use_local_embeddings:
            self._init_embeddings()

    
    def _load_json(self, filename: str) -> dict | list:
        """Load JSON file."""
        path = self.data_dir / filename
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {} if "index" in filename else []

    
    def _init_embeddings(self):
        """Initialize embedding model."""
        print("Loading embedding model...")
        try:
            self.embedder = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
        except Exception as e:
            print(f"Error loading embedding model: {e}")
            global EMBEDDINGS_AVAILABLE
            EMBEDDINGS_AVAILABLE = False
            self.embedder = None        
        if CHROMADB_AVAILABLE:
            self._init_chroma()

    
    def _init_chroma(self):
        """Initialize ChromaDB."""
        chroma_path = self.data_dir / "chromadb"
        chroma_path.mkdir(exist_ok=True)
        
        client = chromadb.PersistentClient(path=str(chroma_path))
        
        self.collection = client.get_or_create_collection(
            name="existential_therapy",
            metadata={"hnsw:space": "cosine"}
        )
        
        if self.collection.count() == 0:
            self._index_all_chunks()

    def _index_all_chunks(self):
        """Index all chunks (dataset + books) to ChromaDB."""
        all_chunks = []
        
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
            print("No data to index")
            return
        
        print(f"Indexing {len(all_chunks)} chunks...")
        print(f"  - Dataset: {len(self.rag_chunks) if self.rag_chunks else 0}")
        print(f"  - Books: {len(self.book_chunks) if self.book_chunks else 0}")
        
        texts = [chunk["text"] for chunk in all_chunks]
        embeddings = self.embedder.encode(texts, show_progress_bar=True)
        
        batch_size = 5000
        total_chunks = len(all_chunks)
        
        for i in range(0, total_chunks, batch_size):
            end_idx = min(i + batch_size, total_chunks)
            print(f"  Adding to DB: {i} - {end_idx} of {total_chunks}")
            
            batch_ids = [chunk["id"] for chunk in all_chunks[i:end_idx]]
            batch_embeddings = embeddings[i:end_idx].tolist()
            batch_documents = texts[i:end_idx]
            batch_metadatas = [chunk["metadata"] for chunk in all_chunks[i:end_idx]]
            
            self.collection.add(
                ids=batch_ids,
                embeddings=batch_embeddings,
                documents=batch_documents,
                metadatas=batch_metadatas
            )        
        print("Indexing complete")

    def search_associations(self, word: str, givens: str = None) -> list[dict]:
        """Search records by association."""
        word = word.lower().strip()
        results = []
        
        if givens and givens in self.association_index:
            if word in self.association_index[givens]:
                record_ids = self.association_index[givens][word]
                results = [r for r in self.dataset if r["id"] in record_ids]
        else:
            for g in ["freedom", "nonsense", "solitude", "death"]:
                if word in self.association_index.get(g, {}):
                    record_ids = self.association_index[g][word]
                    for rid in record_ids:
                        record = next((r for r in self.dataset if r["id"] == rid), None)
                        if record:
                            results.append({**record, "matched_givens": g, "matched_word": word})
        
        return results

    
    def search_similar_narratives(self, query: str, n_results: int = 5) -> list[RAGResult]:
        """Semantic search for similar narratives."""
        if not self.collection or not self.embedder:
            return self._keyword_search(query, n_results)
        
        query_embedding = self.embedder.encode([query])
        
        results = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=n_results
        )
        
        rag_results = []
        for i, doc in enumerate(results["documents"][0]):
            metadata = results["metadatas"][0][i]
            rag_results.append(RAGResult(
                content=doc,
                source=f"dataset_{metadata.get('type', 'unknown')}",
                relevance=1 - results["distances"][0][i],
                metadata=metadata
            ))        
        return rag_results

    
    def _keyword_search(self, query: str, n_results: int = 5) -> list[RAGResult]:
        """Simple keyword search (fallback)."""
        results = []
        query_words = set(query.lower().split())
        
        for chunk in self.rag_chunks:
            chunk_words = set(chunk["text"].lower().split())
            overlap = len(query_words & chunk_words)
            if overlap > 0:
                results.append(RAGResult(
                    content=chunk["text"],
                    source=f"dataset_{chunk['type']}",
                    relevance=overlap / len(query_words),
                    metadata={"record_id": chunk["record_id"]}
                ))
        
        results.sort(key=lambda x: x.relevance, reverse=True)
        return results[:n_results]

    
    def get_context_for_query(self, query: str, max_chunks: int = 3) -> str:
        """Get context for query."""
        results = self.search_similar_narratives(query, max_chunks)
        
        if not results:
            return ""
        
        context_parts = ["Relevant context:\n"]
        
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
                source_info = f"📝 Dataset story"
            
            context_parts.append(f"[{i}] {source_info}")
            context_parts.append(f"{result.content[:500]}...")
            context_parts.append(f"(relevance: {result.relevance:.2f})\n")
        
        return "\n".join(context_parts)

    def analyze_user_associations(self, associations: dict[str, list[str]]) -> dict:
        """Analyze user associations."""
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
        
        analysis["matched_patterns"].sort(key=lambda x: x["count"], reverse=True)
        
        return analysis
