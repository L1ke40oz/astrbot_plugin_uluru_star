"""
Book Manager - handles epub parsing, storage, highlights, reviews, and notes.

All persistent data is stored in the plugin_data directory so it survives plugin updates.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger


class BookManager:
    def __init__(self, books_dir: Path):
        self.books_dir = books_dir
        self.epub_dir = books_dir / "epub_files"
        self.cache_dir = books_dir / "cache"
        self.data_file = books_dir / "library.json"

        self.epub_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.library = self._load_library()

    def _load_library(self) -> dict[str, Any]:
        """Load the library index."""
        if self.data_file.exists():
            try:
                return json.loads(self.data_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"books": {}, "highlights": {}, "reviews": {}, "notes": {}}

    def _save_library(self):
        """Persist library data."""
        self.data_file.write_text(
            json.dumps(self.library, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ==================== Book CRUD ====================

    def list_books(self) -> list[dict[str, Any]]:
        """List all books with basic info."""
        books = []
        for book_id, info in self.library.get("books", {}).items():
            books.append(
                {
                    "id": book_id,
                    "title": info.get("title", ""),
                    "author": info.get("author", ""),
                    "chapter_count": info.get("chapter_count", 0),
                    "added_at": info.get("added_at", 0),
                }
            )
        return sorted(books, key=lambda b: b["added_at"], reverse=True)

    def add_book(self, filename: str, file_bytes: bytes) -> dict[str, Any]:
        """Add an epub or txt book to the library."""
        book_id = uuid.uuid4().hex[:10]

        if filename.lower().endswith(".txt"):
            return self._add_txt_book(book_id, filename, file_bytes)
        elif filename.lower().endswith(".epub"):
            return self._add_epub_book(book_id, filename, file_bytes)
        else:
            raise ValueError(
                "Unsupported file format. Only .epub and .txt are supported."
            )

    def _add_txt_book(
        self, book_id: str, filename: str, file_bytes: bytes
    ) -> dict[str, Any]:
        """Parse and add a txt book."""
        import re

        import chardet

        # detect encoding
        detected = chardet.detect(file_bytes)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        try:
            text = file_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = file_bytes.decode("utf-8", errors="replace")

        # save txt file
        txt_path = self.epub_dir / f"{book_id}.txt"
        txt_path.write_bytes(file_bytes)

        title = filename.replace(".txt", "").replace(".TXT", "")

        # split into chapters by common patterns
        chapter_pattern = re.compile(
            r"^(第[零一二三四五六七八九十百千万\d]+[章节回卷]|Chapter\s+\d+|CHAPTER\s+\d+)",
            re.MULTILINE,
        )
        splits = list(chapter_pattern.finditer(text))

        chapters = []
        if splits:
            # add prologue if there's content before first chapter marker
            if splits[0].start() > 100:
                prologue_text = text[: splits[0].start()].strip()
                if prologue_text:
                    chapters.append(
                        {
                            "index": 0,
                            "title": "序",
                            "html": f"<p>{'</p><p>'.join(prologue_text.split(chr(10)))}</p>",
                            "text_preview": prologue_text[:200],
                        }
                    )

            for i, match in enumerate(splits):
                start = match.start()
                end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
                chunk = text[start:end].strip()

                # extract title from first line
                lines = chunk.split("\n", 1)
                ch_title = lines[0].strip()
                ch_body = lines[1].strip() if len(lines) > 1 else ""

                html = f"<p>{'</p><p>'.join(ch_body.split(chr(10)))}</p>"
                chapters.append(
                    {
                        "index": len(chapters),
                        "title": ch_title,
                        "html": html,
                        "text_preview": ch_body[:200],
                    }
                )
        else:
            # no chapter markers found, split by fixed size (~3000 chars)
            chunk_size = 3000
            for i in range(0, len(text), chunk_size):
                chunk = text[i : i + chunk_size].strip()
                if not chunk:
                    continue
                html = f"<p>{'</p><p>'.join(chunk.split(chr(10)))}</p>"
                chapters.append(
                    {
                        "index": len(chapters),
                        "title": f"第{len(chapters) + 1}节",
                        "html": html,
                        "text_preview": chunk[:200],
                    }
                )

        if not chapters:
            txt_path.unlink(missing_ok=True)
            raise ValueError("txt 文件内容为空或无法解析章节")

        # cache chapters
        cache_file = self.cache_dir / f"{book_id}.json"
        cache_file.write_text(
            json.dumps(chapters, ensure_ascii=False),
            encoding="utf-8",
        )

        # save to library index
        book_info = {
            "title": title,
            "author": "Unknown",
            "filename": filename,
            "chapter_count": len(chapters),
            "added_at": time.time(),
        }
        self.library.setdefault("books", {})[book_id] = book_info
        self._save_library()

        return {"id": book_id, **book_info}

    def _add_epub_book(
        self, book_id: str, filename: str, file_bytes: bytes
    ) -> dict[str, Any]:
        """Parse and add an epub book."""

        import ebooklib
        from bs4 import BeautifulSoup
        from ebooklib import epub

        # save epub file
        epub_path = self.epub_dir / f"{book_id}.epub"
        epub_path.write_bytes(file_bytes)

        # parse epub
        try:
            book = epub.read_epub(str(epub_path))
        except Exception as e:
            logger.error(f"Failed to parse epub: {e}")
            epub_path.unlink(missing_ok=True)
            raise ValueError(f"Failed to parse epub: {e}")

        title = book.get_metadata("DC", "title")
        title = title[0][0] if title else filename.replace(".epub", "")

        author = book.get_metadata("DC", "creator")
        author = author[0][0] if author else "Unknown"

        # extract chapters
        chapters = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")
            text = soup.get_text(strip=True)
            if len(text) < 50:
                continue

            # try to get chapter title
            heading = soup.find(["h1", "h2", "h3"])
            ch_title = (
                heading.get_text(strip=True)
                if heading
                else f"Chapter {len(chapters) + 1}"
            )

            chapters.append(
                {
                    "index": len(chapters),
                    "title": ch_title,
                    "html": str(soup),
                    "text_preview": text[:200],
                }
            )

        # cache chapters
        cache_file = self.cache_dir / f"{book_id}.json"
        cache_file.write_text(
            json.dumps(chapters, ensure_ascii=False),
            encoding="utf-8",
        )

        # save to library index
        book_info = {
            "title": title,
            "author": author,
            "filename": filename,
            "chapter_count": len(chapters),
            "added_at": time.time(),
        }
        self.library.setdefault("books", {})[book_id] = book_info
        self._save_library()

        return {"id": book_id, **book_info}

    def get_chapters(self, book_id: str) -> list[dict[str, Any]] | None:
        """Get chapter list (title + index, no full content)."""
        cache_file = self.cache_dir / f"{book_id}.json"
        if not cache_file.exists():
            return None

        chapters = json.loads(cache_file.read_text(encoding="utf-8"))
        # return without full html for listing
        return [
            {
                "index": ch["index"],
                "title": ch["title"],
                "text_preview": ch.get("text_preview", ""),
            }
            for ch in chapters
        ]

    def get_chapter_content(self, book_id: str, chapter_index: int) -> str | None:
        """Get the HTML content of a specific chapter."""
        cache_file = self.cache_dir / f"{book_id}.json"
        if not cache_file.exists():
            return None

        chapters = json.loads(cache_file.read_text(encoding="utf-8"))
        if chapter_index < 0 or chapter_index >= len(chapters):
            return None

        return chapters[chapter_index].get("html", "")

    def get_chapter_text(self, book_id: str, chapter_index: int) -> str | None:
        """Get plain text of a chapter (for LLM context)."""
        cache_file = self.cache_dir / f"{book_id}.json"
        if not cache_file.exists():
            return None

        chapters = json.loads(cache_file.read_text(encoding="utf-8"))
        if chapter_index < 0 or chapter_index >= len(chapters):
            return None

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(chapters[chapter_index].get("html", ""), "html.parser")
        return soup.get_text(strip=True)

    def delete_book(self, book_id: str):
        """Remove a book and all its associated data."""
        # remove from library
        self.library.get("books", {}).pop(book_id, None)
        self.library.get("highlights", {}).pop(book_id, None)
        self.library.get("reviews", {}).pop(book_id, None)
        self.library.get("notes", {}).pop(book_id, None)
        self._save_library()

        # remove files
        epub_path = self.epub_dir / f"{book_id}.epub"
        epub_path.unlink(missing_ok=True)
        cache_file = self.cache_dir / f"{book_id}.json"
        cache_file.unlink(missing_ok=True)

    # ==================== Highlights ====================

    def add_highlight(
        self,
        book_id: str,
        chapter_index: int | None,
        text: str,
        context: str = "",
        chapter_title: str = "",
    ) -> dict[str, Any]:
        """Save a text highlight."""
        highlight = {
            "id": uuid.uuid4().hex[:8],
            "chapter_index": chapter_index,
            "text": text,
            "context": context,
            "chapter_title": chapter_title,
            "created_at": time.time(),
        }
        self.library.setdefault("highlights", {}).setdefault(book_id, []).append(
            highlight
        )
        self._save_library()
        return highlight

    def get_highlights(self, book_id: str) -> list[dict[str, Any]]:
        """Get all highlights for a book."""
        return self.library.get("highlights", {}).get(book_id, [])

    def remove_highlight(
        self, book_id: str, text: str, chapter_index: int | None = None
    ) -> bool:
        """Remove a highlight by text match (and optionally chapter_index)."""
        highlights = self.library.get("highlights", {}).get(book_id, [])
        if not highlights:
            return False

        for i, h in enumerate(highlights):
            if h.get("text") == text:
                if (
                    chapter_index is not None
                    and h.get("chapter_index") != chapter_index
                ):
                    continue
                highlights.pop(i)
                self._save_library()
                return True
        return False

    # ==================== Reviews ====================

    def add_review(
        self, book_id: str, chapter_index: int | None, content: str
    ) -> dict[str, Any]:
        """Save a book review / paragraph comment."""
        review = {
            "id": uuid.uuid4().hex[:8],
            "chapter_index": chapter_index,
            "content": content,
            "created_at": time.time(),
        }
        self.library.setdefault("reviews", {}).setdefault(book_id, []).append(review)
        self._save_library()
        return review

    def get_reviews(self, book_id: str) -> list[dict[str, Any]]:
        """Get all reviews for a book."""
        return self.library.get("reviews", {}).get(book_id, [])

    # ==================== Notes ====================

    def add_note(
        self, book_id: str, chapter_index: int | None, content: str
    ) -> dict[str, Any]:
        """Save a sticky note."""
        note = {
            "id": uuid.uuid4().hex[:8],
            "chapter_index": chapter_index,
            "content": content,
            "created_at": time.time(),
        }
        self.library.setdefault("notes", {}).setdefault(book_id, []).append(note)
        self._save_library()
        return note

    def get_notes(self, book_id: str) -> list[dict[str, Any]]:
        """Get all notes for a book."""
        return self.library.get("notes", {}).get(book_id, [])
