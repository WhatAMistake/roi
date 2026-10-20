"""
Rebuild the separate Qdrant vector index for existential associations (v2).

Features:
- Separate collection: existential_associations_v2
- One point = one (given, term) record from association_items_v2.json
- Embedding text contains ONLY the association term (zero given leakage)
- Given, respondent_count, given_share, cross_given_counts in payload
- Deterministic UUIDv5 point IDs
- Safe execution: requires --force to recreate existing collection
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer

DEFAULT_COLLECTION = "existential_associations_v2"
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
NAMESPACE_UUID = uuid.UUID("a74f877d-78be-406a-a63b-010e9f45f9e1")


def get_point_id(given: str, term: str) -> str:
    """Deterministic UUIDv5 for a (given, term) pair."""
    return str(uuid.uuid5(NAMESPACE_UUID, f"{given}:{term}"))


def rebuild_association_index(
    data_dir: str | Path = "data",
    collection_name: str = DEFAULT_COLLECTION,
    model_name: str = DEFAULT_MODEL,
    force: bool = False,
    embedder: Optional[SentenceTransformer] = None,
    client: Optional[QdrantClient] = None,
) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    items_file = data_dir / "association_items_v2.json"
    if not items_file.exists():
        raise FileNotFoundError(f"Missing association items artifact: {items_file}")

    with open(items_file, "r", encoding="utf-8") as f:
        items: List[Dict[str, Any]] = json.load(f)

    if not items:
        raise ValueError(f"No items found in {items_file}")

    print(f"[REBUILD] Loaded {len(items)} association items from {items_file}")

    # Initialize embedder
    if embedder is None:
        print(f"[REBUILD] Loading embedding model: {model_name}...")
        embedder = SentenceTransformer(model_name)

    dim = embedder.get_sentence_embedding_dimension()
    print(f"[REBUILD] Embedding dimension: {dim}")

    # Initialize Qdrant
    if client is None:
        qdrant_dir = data_dir / "qdrant_storage"
        qdrant_dir.mkdir(parents=True, exist_ok=True)
        print(f"[REBUILD] Initializing local Qdrant at {qdrant_dir}...")
        client = QdrantClient(path=str(qdrant_dir))

    # Check collection status
    collections = [c.name for c in client.get_collections().collections]
    if collection_name in collections:
        if force:
            print(f"[REBUILD] Collection '{collection_name}' exists. Recreating due to --force...")
            client.delete_collection(collection_name=collection_name)
        else:
            info = client.get_collection(collection_name=collection_name)
            print(f"[REBUILD] Collection '{collection_name}' already exists with {info.points_count} points.")
            print("[REBUILD] Use --force to drop and rebuild the collection.")
            return {
                "collection_name": collection_name,
                "points_count": info.points_count,
                "status": "already_exists",
            }

    client.create_collection(
        collection_name=collection_name,
        vectors_config=qmodels.VectorParams(
            size=dim,
            distance=qmodels.Distance.COSINE,
        ),
    )
    print(f"[REBUILD] Created collection '{collection_name}'")

    # Generate embeddings: ONLY the association term (crucial thesis rule)
    texts_to_embed = [item["term"] for item in items]
    print(f"[REBUILD] Generating embeddings for {len(texts_to_embed)} unique terms...")
    vectors = embedder.encode(
        texts_to_embed,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    # Prepare Qdrant points
    points = []
    for idx, (item, vec) in enumerate(zip(items, vectors)):
        point_id = get_point_id(item["given"], item["term"])
        payload = {
            "source": item.get("source", "crowd_association"),
            "term": item["term"],
            "display_term": item.get("display_term", item["term"]),
            "given": item["given"],
            "respondent_count": item["respondent_count"],
            "given_share": item["given_share"],
            "cross_given_counts": item["cross_given_counts"],
            "respondent_ids": item.get("respondent_ids", []),
        }
        points.append(
            qmodels.PointStruct(
                id=point_id,
                vector=vec.tolist(),
                payload=payload,
            )
        )

    # Batch upsert points
    batch_size = 250
    print(f"[REBUILD] Upserting {len(points)} points in batches of {batch_size}...")
    for i in range(0, len(points), batch_size):
        client.upsert(
            collection_name=collection_name,
            points=points[i : i + batch_size],
        )

    info = client.get_collection(collection_name=collection_name)
    print(f"[REBUILD] Successfully indexed {info.points_count} points in '{collection_name}'")

    return {
        "collection_name": collection_name,
        "points_count": info.points_count,
        "status": "rebuilt",
        "items_count": len(items),
    }


def main():
    parser = argparse.ArgumentParser(description="Rebuild Qdrant existential associations v2 collection")
    parser.add_argument("--data-dir", default="data", help="Directory with artifacts and qdrant storage")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant collection name")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Embedding model name")
    parser.add_argument("--force", action="store_true", help="Force recreate collection")
    args = parser.parse_args()

    result = rebuild_association_index(
        data_dir=args.data_dir,
        collection_name=args.collection,
        model_name=args.model,
        force=args.force,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
