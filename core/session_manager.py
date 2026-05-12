"""
Session Manager - manages book-based reading sessions.

Each book has its own persistent session file containing chat history.
Sessions are stored in `sessions/books/{book_id}.json`.

Lifecycle:
  - User selects a book → session loaded (or created if new)
  - User chats in the reader → messages appended to the book's session
  - User switches books → previous session saved, new one loaded
  - User closes page → current session saved (empty sessions auto-deleted)
  - User can delete session files directly from the folder
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger


class SessionManager:
    def __init__(self, sessions_dir: Path, timeout_minutes: int = 30):
        self.sessions_dir = sessions_dir
        self.books_dir = sessions_dir / "books"
        self.timeout_seconds = timeout_minutes * 60

        # ensure directories exist
        self.books_dir.mkdir(parents=True, exist_ok=True)

        # clean up legacy archives folder (no longer used)
        self._cleanup_legacy_archives()

        # currently active book session (loaded into memory for fast access)
        self.active_book_id: str | None = None
        self.active_session: dict[str, Any] | None = None

    def _cleanup_legacy_archives(self):
        """Remove legacy archives folder and active_sessions.json on startup."""
        archives_dir = self.sessions_dir / "archives"
        if archives_dir.exists():
            try:
                import shutil
                shutil.rmtree(archives_dir)
                logger.info("乌鲁鲁星: 已清理旧版 archives 文件夹")
            except Exception as e:
                logger.warning(f"乌鲁鲁星: 清理 archives 失败: {e}")

        active_file = self.sessions_dir / "active_sessions.json"
        if active_file.exists():
            try:
                active_file.unlink()
                logger.debug("乌鲁鲁星: 已清理旧版 active_sessions.json")
            except Exception:
                pass

    # ==================== Book Session CRUD ====================

    def get_or_create_book_session(self, book_id: str, book_title: str = "") -> dict[str, Any]:
        """Get existing session for a book, or create a new one.

        This is the main entry point when user selects a book.
        New sessions are NOT saved to disk until they have messages.
        """
        # if already active, just return it
        if self.active_book_id == book_id and self.active_session:
            self.active_session["last_active"] = time.time()
            # only save if has messages
            if self.active_session.get("chat_history"):
                self._save_book_session(book_id, self.active_session)
            return self.active_session

        # save previous active session before switching
        if self.active_book_id and self.active_session:
            if self.active_session.get("chat_history"):
                self._save_book_session(self.active_book_id, self.active_session)
            else:
                # empty session, don't persist
                self._delete_book_session(self.active_book_id)

        # try to load existing session for this book
        session = self._load_book_session(book_id)
        if session:
            session["last_active"] = time.time()
            session["resumed"] = True
            # update title if it was missing
            if book_title and not session.get("book_title"):
                session["book_title"] = book_title
        else:
            # create new session (not saved to disk yet)
            session = {
                "book_id": book_id,
                "book_title": book_title,
                "created_at": time.time(),
                "last_active": time.time(),
                "resumed": False,
                "chat_history": [],
            }

        self.active_book_id = book_id
        self.active_session = session
        return session

    def end_book_session(self, book_id: str | None = None):
        """End a book session. Deletes if empty, saves if has messages."""
        target_id = book_id or self.active_book_id
        if not target_id:
            return

        if target_id == self.active_book_id:
            session = self.active_session
            self.active_book_id = None
            self.active_session = None
        else:
            session = self._load_book_session(target_id)

        if not session:
            return

        # if no chat history, delete the file
        if not session.get("chat_history"):
            self._delete_book_session(target_id)
        else:
            session["last_active"] = time.time()
            self._save_book_session(target_id, session)

    # ==================== Chat History ====================

    def get_chat_history(self, book_id: str) -> list[dict[str, Any]]:
        """Get chat messages for a book's session."""
        if book_id == self.active_book_id and self.active_session:
            return self.active_session.get("chat_history", [])

        session = self._load_book_session(book_id)
        if not session:
            return []
        return session.get("chat_history", [])

    def add_chat_message(self, book_id: str, role: str, content: str, metadata: dict | None = None):
        """Add a message to a book's chat history.

        Args:
            book_id: the book this message belongs to
            role: "user" or "bot"
            content: message text
            metadata: optional extra info
        """
        # ensure session exists
        if book_id != self.active_book_id or not self.active_session:
            self.get_or_create_book_session(book_id)

        message = {
            "id": uuid.uuid4().hex[:8],
            "role": role,
            "content": content,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self.active_session.setdefault("chat_history", []).append(message)
        # mark summary as stale when new messages arrive
        self.active_session["summary_stale"] = True
        self._save_book_session(book_id, self.active_session)

    def get_context_for_llm(self, book_id: str) -> list[dict[str, str]]:
        """Get chat history formatted for LLM context."""
        history = self.get_chat_history(book_id)
        context = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            context.append({"role": role, "content": msg["content"]})
        return context

    # ==================== Memory Injection ====================

    def get_summaries_for_injection(self) -> list[dict[str, str]]:
        """Get summaries of all book sessions for lightweight LLM injection.

        Returns a list of {book_title, summary, message_count} for each book
        that has chat history. This is much cheaper than injecting full history.
        """
        results = []
        for session_file in self.books_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            chat_history = data.get("chat_history", [])
            if not chat_history:
                continue

            book_title = data.get("book_title", "未知")
            summary = data.get("summary", "")

            # if no summary yet, generate a quick one from last few messages
            if not summary:
                recent = chat_history[-3:]
                parts = []
                for msg in recent:
                    role = "她" if msg.get("role") == "user" else "你"
                    text = msg.get("content", "")[:50]
                    parts.append(f"{role}: {text}")
                summary = "; ".join(parts)

            results.append({
                "book_title": book_title,
                "summary": summary,
                "message_count": len(chat_history),
            })

        return results

    def update_summary(self, book_id: str, summary: str):
        """Update the summary for a book session."""
        if book_id == self.active_book_id and self.active_session:
            self.active_session["summary"] = summary
            self.active_session["summary_stale"] = False
            self._save_book_session(book_id, self.active_session)
        else:
            session = self._load_book_session(book_id)
            if session:
                session["summary"] = summary
                session["summary_stale"] = False
                self._save_book_session(book_id, session)

    def get_stale_sessions(self) -> list[dict[str, Any]]:
        """Get sessions that need summary regeneration."""
        results = []
        for session_file in self.books_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("summary_stale") and data.get("chat_history"):
                results.append(data)
        return results

    def get_recent_messages_for_injection(self, max_messages: int = 10) -> list[dict[str, Any]]:
        """Get recent messages from the active book session for LLM injection.

        Returns the most recent messages from the currently active book session.
        """
        if not self.active_session:
            return []

        chat_history = self.active_session.get("chat_history", [])
        if not chat_history:
            return []

        return chat_history[-max_messages:]

    # ==================== Listing & Search ====================

    def list_book_sessions(self) -> list[dict[str, Any]]:
        """List all book sessions with metadata (for the tools panel).

        Returns list of session summaries sorted by last_active (most recent first).
        """
        results = []
        for session_file in self.books_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            results.append({
                "book_id": data.get("book_id", session_file.stem),
                "book_title": data.get("book_title", "未知"),
                "message_count": len(data.get("chat_history", [])),
                "last_active": data.get("last_active", 0),
                "created_at": data.get("created_at", 0),
            })

        results.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return results

    def get_book_session_detail(self, book_id: str) -> dict[str, Any] | None:
        """Get full session data for a book (for viewing in tools panel)."""
        if book_id == self.active_book_id and self.active_session:
            return self.active_session
        return self._load_book_session(book_id)

    def delete_book_session(self, book_id: str):
        """Delete a book's session entirely."""
        if book_id == self.active_book_id:
            self.active_book_id = None
            self.active_session = None
        self._delete_book_session(book_id)

    def delete_message(self, book_id: str, message_id: str) -> bool:
        """Delete a single message from a book's session."""
        if book_id == self.active_book_id and self.active_session:
            session = self.active_session
        else:
            session = self._load_book_session(book_id)

        if not session:
            return False

        history = session.get("chat_history", [])
        original_len = len(history)
        session["chat_history"] = [m for m in history if m.get("id") != message_id]

        if len(session["chat_history"]) == original_len:
            return False

        self._save_book_session(book_id, session)
        return True

    def search_archives(self, keyword: str = "") -> list[dict[str, Any]]:
        """Search all book sessions, optionally filtering by keyword.

        This replaces the old archive-based search. Now searches across
        all book session files.
        """
        results = []
        for session_file in self.books_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            chat_history = data.get("chat_history", [])
            if not chat_history:
                continue

            if keyword:
                keyword_lower = keyword.lower()
                found = any(
                    keyword_lower in msg.get("content", "").lower()
                    for msg in chat_history
                )
                if not found:
                    continue

            # format as archive-compatible structure
            results.append({
                "session_id": data.get("book_id", session_file.stem),
                "book_title": data.get("book_title", ""),
                "started_at": data.get("created_at"),
                "ended_at": data.get("last_active"),
                "chat_history": chat_history,
            })

        results.sort(key=lambda x: x.get("ended_at", 0), reverse=True)
        return results

    # ==================== Legacy Compatibility ====================

    def heartbeat(self, book_id: str) -> bool:
        """Update last_active for the active session. Returns False if no session."""
        if book_id == self.active_book_id and self.active_session:
            self.active_session["last_active"] = time.time()
            # don't save on every heartbeat to reduce I/O
            return True
        return False

    # ==================== Internal ====================

    def _session_filename(self, book_id: str, book_title: str = "") -> str:
        """Generate a human-readable session filename.

        Format: {book_id}_{sanitized_title}.json
        Falls back to just {book_id}.json if title is empty.
        """
        if book_title:
            # sanitize title for filesystem: remove invalid chars, limit length
            safe_title = "".join(
                c for c in book_title if c not in r'\/:*?"<>|'
            ).strip()[:40]
            if safe_title:
                return f"{book_id}_{safe_title}.json"
        return f"{book_id}.json"

    def _find_session_file(self, book_id: str) -> Path | None:
        """Find the session file for a book_id (handles both old and new naming)."""
        # try new naming pattern first: {book_id}_*.json
        matches = list(self.books_dir.glob(f"{book_id}_*.json"))
        if matches:
            return matches[0]
        # fallback to old naming: {book_id}.json
        old_path = self.books_dir / f"{book_id}.json"
        if old_path.exists():
            return old_path
        return None

    def _load_book_session(self, book_id: str) -> dict[str, Any] | None:
        """Load a book session from disk."""
        session_file = self._find_session_file(book_id)
        if not session_file:
            return None
        try:
            return json.loads(session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save_book_session(self, book_id: str, session: dict[str, Any]):
        """Save a book session to disk with human-readable filename."""
        book_title = session.get("book_title", "")
        filename = self._session_filename(book_id, book_title)
        session_file = self.books_dir / filename

        # if old-style file exists with different name, remove it
        old_file = self.books_dir / f"{book_id}.json"
        if old_file.exists() and old_file != session_file:
            old_file.unlink()

        # also check if there's a different named file for this book_id
        existing = self._find_session_file(book_id)
        if existing and existing != session_file:
            existing.unlink()

        session_file.write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _delete_book_session(self, book_id: str):
        """Delete a book session file."""
        session_file = self._find_session_file(book_id)
        if session_file and session_file.exists():
            session_file.unlink()
            logger.debug(f"乌鲁鲁星: 已删除空会话 {book_id}")

    def save_all(self):
        """Save active session (called on plugin shutdown)."""
        if self.active_book_id and self.active_session:
            # only save if has messages
            if self.active_session.get("chat_history"):
                self._save_book_session(self.active_book_id, self.active_session)
            else:
                self._delete_book_session(self.active_book_id)
