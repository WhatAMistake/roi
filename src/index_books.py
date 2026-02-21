"""
Индексация PDF книг для RAG.
Поддерживает PDF, TXT, DOCX файлы.
"""

import json
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


@dataclass
class BookChunk:
    """Чанк книги для RAG."""
    id: str
    book_title: str
    author: str
    chapter: Optional[str]
    page: Optional[int]
    text: str
    chunk_index: int


class BookIndexer:
    """Индексатор книг для RAG."""
    
    def __init__(
        self,
        books_dir: str = None,
        output_dir: str = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ):
        self.books_dir = Path(books_dir) if books_dir else Path(__file__).parent.parent / "books"
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent / "data"
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        self.output_dir.mkdir(exist_ok=True)
    
    def extract_text_from_pdf(self, file_path: Path) -> tuple[str, list[str]]:
        """Извлечение текста из PDF."""
        if not PYPDF_AVAILABLE:
            print("pypdf не установлен. Установите: pip install pypdf")
            return "", []
        
        text = ""
        pages = []
        
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
                text += page_text + "\n"
        
        return text, pages
    
    def extract_text_from_txt(self, file_path: Path) -> tuple[str, list[str]]:
        """Извлечение текста из TXT."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        
        # Разбиваем на "страницы" по ~3000 символов
        pages = []
        for i in range(0, len(text), 3000):
            pages.append(text[i:i+3000])
        
        return text, pages
    
    def extract_text_from_docx(self, file_path: Path) -> tuple[str, list[str]]:
        """Извлечение текста из DOCX."""
        if not DOCX_AVAILABLE:
            print("python-docx не установлен. Установите: pip install python-docx")
            return "", []
        
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs]
        text = "\n".join(paragraphs)
        
        return text, paragraphs
    
    def extract_text(self, file_path: Path) -> tuple[str, list[str]]:
        """Извлечение текста из файла."""
        suffix = file_path.suffix.lower()
        
        if suffix == '.pdf':
            return self.extract_text_from_pdf(file_path)
        elif suffix == '.txt':
            return self.extract_text_from_txt(file_path)
        elif suffix == '.docx':
            return self.extract_text_from_docx(file_path)
        else:
            print(f"Неподдерживаемый формат: {suffix}")
            return "", []
    
    def detect_chapters(self, text: str) -> list[tuple[str, int, int]]:
        """Детекция глав в тексте."""
        chapters = []
        
        # Паттерны для поиска глав
        patterns = [
            r'(?:Глава|Chapter|Часть|Part)\s*(\d+|[IVXLCDM]+)',
            r'(?:Глава|Chapter|Часть|Part)\s*[\.:]?\s*([^\n]+)',
            r'^\d+\.\s+([^\n]+)$',  # "1. Название"
        ]
        
        for pattern in patterns:
            matches = list(re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE))
            if matches:
                for i, match in enumerate(matches):
                    start = match.start()
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                    title = match.group(0).strip()[:100]  # Ограничиваем длину
                    chapters.append((title, start, end))
                break
        
        return chapters
    
    def clean_text(self, text: str) -> str:
        """Очистка текста."""
        # Убираем лишние пробелы
        text = re.sub(r'[ \t]+', ' ', text)
        # Убираем множественные переносы строк
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Убираем спецсимволы
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text.strip()
    
    def create_chunks(
        self,
        text: str,
        book_title: str,
        author: str,
        chapters: list[tuple[str, int, int]]
    ) -> list[BookChunk]:
        """Создание чанков из текста."""
        chunks = []
        chunk_index = 0
        
        if chapters:
            # Если есть главы — чанки по главам
            for i, (chapter_title, start, end) in enumerate(chapters):
                chapter_text = self.clean_text(text[start:end])
                
                # Разбиваем главу на чанки
                for j in range(0, len(chapter_text), self.chunk_size - self.chunk_overlap):
                    chunk_text = chapter_text[j:j + self.chunk_size]
                    
                    if len(chunk_text) < 100:  # Слишком короткий чанк
                        continue
                    
                    chunks.append(BookChunk(
                        id=f"{book_title}_{chunk_index}",
                        book_title=book_title,
                        author=author,
                        chapter=chapter_title[:50],
                        page=None,
                        text=chunk_text,
                        chunk_index=chunk_index
                    ))
                    chunk_index += 1
        else:
            # Если глав нет — просто чанки по тексту
            clean = self.clean_text(text)
            
            for i in range(0, len(clean), self.chunk_size - self.chunk_overlap):
                chunk_text = clean[i:i + self.chunk_size]
                
                if len(chunk_text) < 100:
                    continue
                
                chunks.append(BookChunk(
                    id=f"{book_title}_{chunk_index}",
                    book_title=book_title,
                    author=author,
                    chapter=None,
                    page=None,
                    text=chunk_text,
                    chunk_index=chunk_index
                ))
                chunk_index += 1
        
        return chunks    
    def parse_filename(self, file_path: Path) -> tuple[str, str]:
        """Парсинг имени файла для извлечения автора и названия."""
        # Примеры: "Ялом - Экзистенциальная психотерапия.pdf"
        #          "Frankl - Man's Search for Meaning.pdf"
        
        name = file_path.stem
        
        # Пытаемся разделить по разделителям
        for sep in [' - ', ' – ', '-', '_', '  ']:
            if sep in name:
                parts = name.split(sep, 1)
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip()
        
        return "Unknown", name
    
    def index_book(self, file_path: Path) -> list[BookChunk]:
        """Индексация одной книги."""
        print(f"Обработка: {file_path.name}")
        
        author, title = self.parse_filename(file_path)
        print(f"  Автор: {author}")
        print(f"  Книга: {title}")
        
        text, pages = self.extract_text(file_path)
        
        if not text:
            print(f"  Ошибка: не удалось извлечь текст")
            return []
        
        print(f"  Извлечено символов: {len(text)}")
        
        chapters = self.detect_chapters(text)
        print(f"  Найдено глав: {len(chapters)}")
        
        chunks = self.create_chunks(text, title, author, chapters)
        print(f"  Создано чанков: {len(chunks)}")
        
        return chunks
    
    def index_all_books(self) -> list[dict]:
        """Индексация всех книг в папке."""
        all_chunks = []
        
        # Поддерживаемые форматы
        extensions = ['.pdf', '.txt', '.docx']
        
        files = []
        for ext in extensions:
            files.extend(self.books_dir.glob(f'*{ext}'))
        
        if not files:
            print(f"Файлы не найдены в: {self.books_dir}")
            print("Добавьте PDF/TXT/DOCX файлы в папку books/")
            return []
        
        print(f"Найдено файлов: {len(files)}\n")
        
        for file_path in files:
            chunks = self.index_book(file_path)
            all_chunks.extend([{
                'id': c.id,
                'book_title': c.book_title,
                'author': c.author,
                'chapter': c.chapter,
                'page': c.page,
                'text': c.text,
                'chunk_index': c.chunk_index
            } for c in chunks])
            print()
        
        return all_chunks
    
    def save_chunks(self, chunks: list[dict], filename: str = "book_chunks.json"):
        """Сохранение чанков в JSON."""
        output_path = self.output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        
        print(f"Сохранено в: {output_path}")
        print(f"Всего чанков: {len(chunks)}")
    
    def run(self):
        """Запуск индексации."""
        print("="*50)
        print("Индексация книг для RAG")
        print("="*50 + "\n")
        
        chunks = self.index_all_books()
        
        if chunks:
            self.save_chunks(chunks)
        
        return chunks


def main():
    """Точка входа."""
    indexer = BookIndexer()
    indexer.run()


if __name__ == "__main__":
    main()    main()