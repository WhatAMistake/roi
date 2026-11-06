from pathlib import Path

from src.index_books import BookIndexer


def test_chapter_chunking_keeps_text_before_first_chapter(tmp_path: Path):
    indexer = BookIndexer(books_dir=str(tmp_path), output_dir=str(tmp_path),
                          chunk_size=200, chunk_overlap=20)
    introduction = "Introduction " * 12
    chapter = "Chapter text " * 12
    text = introduction + chapter
    chunks = indexer.create_chunks(text, "Title", "Author", [("Chapter", len(introduction), len(text))])
    assert any("Introduction" in chunk.text for chunk in chunks)
    assert any("Chapter text" in chunk.text for chunk in chunks)
