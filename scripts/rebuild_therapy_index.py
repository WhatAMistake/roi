"""Explicitly build the book and narrative Qdrant collection.

An existing collection is never changed unless --force is supplied. This does
not modify the separate association collection.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag import DEFAULT_COLLECTION, DEFAULT_EMBEDDING_MODEL, ExistentialRAG


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--force", action="store_true", help="Replace an existing therapy collection")
    args = parser.parse_args()

    for name in ("narrative_chunks.json", "book_chunks.json"):
        path = args.data_dir / name
        if not path.is_file():
            parser.error(f"Missing or empty source: {path}")
        chunks = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(chunks, list) or not chunks:
            parser.error(f"Missing or empty source: {path}")
        ids = []
        for chunk in chunks:
            if not isinstance(chunk, dict) or not chunk.get("id") or not str(chunk.get("text") or "").strip():
                parser.error(f"Source contains a missing ID or text: {path}")
            ids.append(str(chunk["id"]))
        if len(ids) != len(set(ids)):
            parser.error(f"Source contains duplicate IDs: {path}")
    client = QdrantClient(path=str(args.data_dir / "qdrant_storage"))
    try:
        names = {item.name for item in client.get_collections().collections}
        if DEFAULT_COLLECTION in names and not args.force:
            parser.error(f"Collection {DEFAULT_COLLECTION} already exists; use --force to replace it")
        embedder = SentenceTransformer(DEFAULT_EMBEDDING_MODEL)
        rag = ExistentialRAG(data_dir=args.data_dir, qdrant_client=client, embedder=embedder)
        if DEFAULT_COLLECTION in names:
            client.delete_collection(DEFAULT_COLLECTION)
        rag._index_all_chunks()
        status = rag.check_index_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if status["needs_reindex"] or not status["collection_exists"]:
            raise RuntimeError("Therapy collection did not match its source data")
    finally:
        client.close()


if __name__ == "__main__":
    main()
