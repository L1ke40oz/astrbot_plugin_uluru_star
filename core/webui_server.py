"""
WebUI Server - serves the reading companion frontend and API.

Template loading priority:
  1. custom_templates_dir (user customizations in plugin_data, survives updates)
  2. plugin_dir/templates/ (default templates shipped with the plugin)
"""

import asyncio
import json
import random
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from astrbot.api import logger

from .book_manager import BookManager
from .chat_engine import ChatEngine
from .session_manager import SessionManager


class WebUIServer:
    def __init__(
        self,
        host: str,
        port: int,
        session_manager: SessionManager,
        book_manager: BookManager,
        chat_engine: ChatEngine,
        custom_templates_dir: Path,
        plugin_dir: Path,
        bot_reader=None,
        plugin=None,
        assets_dir: Path | None = None,
    ):
        self.host = host
        self.port = port
        self.session_manager = session_manager
        self.book_manager = book_manager
        self.chat_engine = chat_engine
        self.custom_templates_dir = custom_templates_dir
        self.default_templates_dir = plugin_dir / "templates"
        self.plugin_dir = plugin_dir
        self.bot_reader = bot_reader
        self.plugin = plugin  # reference to SharedReadPlugin for snapshot
        self.assets_dir = assets_dir

        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None

        self._app = FastAPI(title="乌鲁鲁星", version="1.1.0")
        self._setup_app()

    def _get_template_path(self, filename: str) -> Path | None:
        """Resolve a template file with priority: custom > default."""
        custom = self.custom_templates_dir / filename
        if custom.exists():
            return custom

        default = self.default_templates_dir / filename
        if default.exists():
            return default

        return None

    def _setup_app(self):
        """Configure FastAPI routes and middleware."""
        app = self._app

        # CORS - allow all for local/LAN access
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )

        # --- Static files ---
        # serve custom templates first, then default
        if self.custom_templates_dir.exists():
            app.mount(
                "/custom-static",
                StaticFiles(directory=self.custom_templates_dir),
                name="custom-static",
            )

        # serve user assets (images, etc.) from plugin_data/assets/
        if self.assets_dir and self.assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=self.assets_dir),
                name="assets",
            )

        # --- Pages ---

        @app.get("/", response_class=HTMLResponse)
        async def serve_index():
            path = self._get_template_path("index.html")
            if not path:
                raise HTTPException(404, detail="前端文件缺失")
            return HTMLResponse(path.read_text(encoding="utf-8"))

        @app.get("/style.css")
        async def serve_css():
            path = self._get_template_path("style.css")
            if not path:
                raise HTTPException(404)
            return FileResponse(path, media_type="text/css")

        @app.get("/app.js")
        async def serve_js():
            path = self._get_template_path("app.js")
            if not path:
                raise HTTPException(404)
            return FileResponse(path, media_type="application/javascript")

        # serve planet.png (and other template images) directly
        @app.get("/static/{filename:path}")
        async def serve_static_file(filename: str):
            # check custom_templates first, then default
            custom_path = self.custom_templates_dir / filename
            if custom_path.exists() and custom_path.is_file():
                return FileResponse(custom_path)
            path = self.default_templates_dir / filename
            if path.exists() and path.is_file():
                return FileResponse(path)
            raise HTTPException(404)

        @app.get("/sw.js")
        async def serve_sw():
            path = self._get_template_path("sw.js")
            if not path:
                raise HTTPException(404)
            return FileResponse(path, media_type="application/javascript")

        # --- Frontend config API ---

        @app.get("/api/config/frontend")
        async def api_frontend_config():
            """Return frontend-relevant config values."""
            separator = "\\$"
            pet_house_enabled = True
            if self.plugin and hasattr(self.plugin, "config"):
                separator = self.plugin.config.get("message_separator", "\\$")
            if self.plugin and hasattr(self.plugin, "_pet_house_enabled"):
                pet_house_enabled = self.plugin._pet_house_enabled
            return {
                "success": True,
                "message_separator": separator,
                "pet_house_enabled": pet_house_enabled,
            }

        # --- Profile API (persistent user data) ---

        @app.get("/api/profile")
        async def api_get_profile():
            """Get stored profile data (avatars, nicknames, covers)."""
            profile = self._load_profile()
            return {"success": True, "profile": profile}

        @app.post("/api/profile")
        async def api_save_profile(request: Request):
            """Save profile data."""
            data = await request.json()
            profile = self._load_profile()
            # merge incoming data into existing profile
            for key, value in data.items():
                profile[key] = value
            self._save_profile(profile)
            return {"success": True}

        # --- Session APIs (book-based) ---

        @app.post("/api/session/start")
        async def api_session_start(request: Request):
            """Start or resume a book session.

            Expects: { "book_id": "...", "book_title": "..." }
            If no book_id, just acknowledges connection (no session created).
            """
            data = await request.json()
            book_id = data.get("book_id")
            book_title = data.get("book_title", "")

            if not book_id:
                # no book selected yet, just acknowledge
                return {"success": True, "session_active": False}

            session = self.session_manager.get_or_create_book_session(
                book_id, book_title
            )

            # trigger AstrBot conversation snapshot in background
            if self.plugin and self.bot_reader and self.bot_reader.target_session:
                asyncio.create_task(
                    self.plugin.snapshot_astrbot_conversation(
                        self.bot_reader.target_session
                    )
                )

            return {
                "success": True,
                "session_active": True,
                "book_id": book_id,
                "resumed": session.get("resumed", False),
                "message_count": len(session.get("chat_history", [])),
            }

        @app.post("/api/session/heartbeat")
        async def api_session_heartbeat(request: Request):
            data = await request.json()
            book_id = data.get("book_id")
            if not book_id:
                return {"success": False}

            alive = self.session_manager.heartbeat(book_id)
            return {"success": alive}

        @app.post("/api/session/end")
        async def api_session_end(request: Request):
            data = await request.json()
            book_id = data.get("book_id")
            if book_id:
                self.session_manager.end_book_session(book_id)
            return {"success": True}

        # --- Book APIs ---

        @app.get("/api/books")
        async def api_list_books():
            books = self.book_manager.list_books()
            return {"success": True, "books": books}

        @app.post("/api/books/upload")
        async def api_upload_book(file: UploadFile = File(...)):
            if not file.filename or not (
                file.filename.lower().endswith(".epub")
                or file.filename.lower().endswith(".txt")
                or file.filename.lower().endswith(".pdf")
            ):
                raise HTTPException(
                    400, detail="only .epub, .txt, and .pdf files are supported"
                )

            file_bytes = await file.read()
            try:
                book_info = self.book_manager.add_book(file.filename, file_bytes)
            except ValueError as e:
                raise HTTPException(400, detail=str(e))

            return {"success": True, "book": book_info}

        @app.get("/api/books/{book_id}/chapters")
        async def api_get_chapters(book_id: str):
            chapters = self.book_manager.get_chapters(book_id)
            if chapters is None:
                raise HTTPException(404, detail="book not found")
            return {"success": True, "chapters": chapters}

        @app.get("/api/books/{book_id}/chapters/{chapter_index}")
        async def api_get_chapter_content(book_id: str, chapter_index: int):
            content = self.book_manager.get_chapter_content(book_id, chapter_index)
            if content is None:
                raise HTTPException(404, detail="chapter not found")
            return {"success": True, "content": content}

        @app.delete("/api/books/{book_id}")
        async def api_delete_book(book_id: str):
            self.book_manager.delete_book(book_id)
            # also delete associated session and bot memories
            self.session_manager.delete_book_session(book_id)
            if self.bot_reader:
                # remove bot chapter memories for this book
                if book_id in self.bot_reader.memories:
                    del self.bot_reader.memories[book_id]
                    self.bot_reader._save_memories()
                # remove bot reading progress for this book
                if book_id in self.bot_reader.progress:
                    del self.bot_reader.progress[book_id]
                    self.bot_reader._save_progress()
                # remove user reading progress for this book
                if book_id in self.bot_reader.user_progress:
                    del self.bot_reader.user_progress[book_id]
                    self.bot_reader._save_user_progress()
            return {"success": True}

        # --- Interaction APIs ---

        @app.post("/api/interact/highlight")
        async def api_highlight(request: Request):
            data = await request.json()
            session_token = data.get("session_token")
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")
            text = data.get("text", "")
            context_text = data.get("context", "")
            chapter_title = data.get("chapter_title", "")

            if not all([session_token, book_id, text]):
                raise HTTPException(400, detail="missing required fields")

            highlight = self.book_manager.add_highlight(
                book_id, chapter_index, text, context_text, chapter_title
            )

            # record highlight as a system note in chat history
            # so the LLM knows what user highlighted when they ask about it
            try:
                ch_label = f"第{chapter_index + 1}章" if chapter_index is not None else ""
                if chapter_title:
                    ch_label = f"{ch_label}·{chapter_title}" if ch_label else chapter_title
                ch_prefix = f"（{ch_label}）" if ch_label else ""
                note_content = f"[划线{ch_prefix}] 她划了这句：「{text}」"
                if context_text:
                    note_content += f"\n（上下文：{context_text[:150]}）"
                self.session_manager.add_chat_message(
                    book_id,
                    "user",
                    note_content,
                    metadata={"type": "highlight", "silent": True},
                )
                logger.debug(
                    f"乌鲁鲁星: 划线已记录到对话历史 book={book_id} text={text[:30]}"
                )
            except Exception as e:
                logger.warning(f"乌鲁鲁星: 划线记录到对话历史失败: {e}")

            # Auto-reply on highlight if enabled
            bot_reply = None
            if self.plugin and hasattr(self.plugin, "config"):
                if self.plugin.config.get("auto_reply_on_highlight", True):
                    try:
                        bot_reply = await self.chat_engine.respond_to_highlight(
                            book_id, book_id, chapter_index, text, context_text
                        )
                    except Exception as e:
                        logger.debug(f"乌鲁鲁星: 划线自动回复失败: {e}")

            return {"success": True, "highlight": highlight, "bot_reply": bot_reply}

        @app.post("/api/interact/review")
        async def api_review(request: Request):
            data = await request.json()
            session_token = data.get("session_token")
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")
            content = data.get("content", "")

            if not all([session_token, book_id, content]):
                raise HTTPException(400, detail="missing required fields")

            review = self.book_manager.add_review(book_id, chapter_index, content)

            bot_reply = None
            if self.plugin and hasattr(self.plugin, "config"):
                if self.plugin.config.get("auto_reply_on_review", True):
                    bot_reply = await self.chat_engine.respond_to_review(
                        book_id, book_id, chapter_index, content
                    )
                    # Save bot reply into the review record
                    if bot_reply:
                        reviews = self.book_manager.get_reviews(book_id)
                        for r in reviews:
                            if r.get("id") == review.get("id"):
                                r["bot_reply"] = bot_reply
                                break
                        self.book_manager._save_library()

            # Trigger chapter summary regeneration (force overwrite)
            if self.bot_reader and chapter_index is not None:
                asyncio.create_task(
                    self.bot_reader.generate_chapter_summary(
                        book_id, chapter_index, force=True
                    )
                )

            return {"success": True, "review": review, "bot_reply": bot_reply}

        @app.post("/api/interact/note")
        async def api_note(request: Request):
            data = await request.json()
            session_token = data.get("session_token")
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")
            content = data.get("content", "")

            if not all([session_token, book_id, content]):
                raise HTTPException(400, detail="missing required fields")

            note = self.book_manager.add_note(book_id, chapter_index, content)

            bot_reply = None
            if self.plugin and hasattr(self.plugin, "config"):
                if self.plugin.config.get("auto_reply_on_note", True):
                    bot_reply = await self.chat_engine.respond_to_note(
                        book_id, book_id, chapter_index, content
                    )

            return {"success": True, "note": note, "bot_reply": bot_reply}

        # --- Chat APIs ---

        @app.get("/api/chat/history")
        async def api_chat_history(book_id: str):
            messages = self.session_manager.get_chat_history(book_id)
            return {"success": True, "messages": messages}

        @app.post("/api/chat/send")
        async def api_chat_send(request: Request):
            data = await request.json()
            book_id = data.get("book_id")
            content = data.get("content", "")
            chapter_index = data.get("chapter_index")
            scroll_percent = data.get("scroll_percent")
            bookmark_percent = data.get("bookmark_percent")

            if not book_id or not content:
                raise HTTPException(400, detail="missing required fields")

            bot_reply = await self.chat_engine.chat(
                book_id,
                content,
                chapter_index=chapter_index,
                scroll_percent=scroll_percent,
                bookmark_percent=bookmark_percent,
            )
            return {"success": True, "reply": bot_reply}

        @app.post("/api/chapter/complete")
        async def api_chapter_complete(request: Request):
            """Mark a chapter as complete (打卡) and advance user progress by 1."""
            data = await request.json()
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")

            if not book_id or chapter_index is None:
                raise HTTPException(400, detail="missing required fields")

            # advance user progress by 1 checkin
            if self.bot_reader:
                total_chapters = 0
                book_title = ""
                books = self.book_manager.list_books()
                book = next((b for b in books if b["id"] == book_id), None)
                if book:
                    book_title = book.get("title", "")
                    chapters = self.book_manager.get_chapters(book_id)
                    total_chapters = len(chapters) if chapters else 0

                self.bot_reader.checkin_chapter(
                    book_id, chapter_index, total_chapters, book_title
                )

                # trigger bot summary generation in background
                asyncio.create_task(
                    self.bot_reader.generate_chapter_summary(book_id, chapter_index)
                )

            return {"success": True, "message": "打卡成功"}

        # --- Data query APIs ---

        @app.get("/api/data/{book_id}/highlights")
        async def api_get_highlights(book_id: str):
            return {
                "success": True,
                "highlights": self.book_manager.get_highlights(book_id),
            }

        @app.delete("/api/data/{book_id}/highlights")
        async def api_delete_highlight(book_id: str, request: Request):
            """Delete a highlight by text match or index."""
            data = await request.json()
            text = data.get("text", "")
            chapter_index = data.get("chapter_index")
            success = self.book_manager.remove_highlight(book_id, text, chapter_index)
            return {"success": success}

        @app.get("/api/data/{book_id}/reviews")
        async def api_get_reviews(book_id: str):
            return {"success": True, "reviews": self.book_manager.get_reviews(book_id)}

        @app.get("/api/data/{book_id}/notes")
        async def api_get_notes(book_id: str):
            return {"success": True, "notes": self.book_manager.get_notes(book_id)}

        # --- Bot progress API ---

        @app.get("/api/bot-progress/{book_id}")
        async def api_bot_progress(book_id: str):
            if not self.bot_reader:
                return {"success": True, "percent": 0}
            return {
                "success": True,
                "percent": self.bot_reader.get_bot_progress_percent(book_id),
            }

        @app.get("/api/bot-memories")
        async def api_bot_memories():
            """Get all bot chapter memories for display."""
            if not self.bot_reader:
                return {"success": True, "memories": []}
            all_memories = self.bot_reader.memories
            result = []
            for book_id, chapters in all_memories.items():
                # get book title
                books = self.book_manager.list_books()
                book = next((b for b in books if b["id"] == book_id), None)
                book_title = book.get("title", "未知") if book else "未知"
                for ch_idx, summary in sorted(
                    chapters.items(), key=lambda x: int(x[0])
                ):
                    result.append(
                        {
                            "book_id": book_id,
                            "book_title": book_title,
                            "chapter_index": int(ch_idx),
                            "summary": summary,
                        }
                    )
            return {"success": True, "memories": result}

        # --- User progress API ---

        @app.post("/api/user-progress/report")
        async def api_report_user_progress(request: Request):
            """Report user's reading progress when they open a chapter."""
            data = await request.json()
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")
            total_chapters = data.get("total_chapters", 0)
            book_title = data.get("book_title", "")

            if not book_id or chapter_index is None:
                raise HTTPException(400, detail="missing required fields")

            if self.bot_reader:
                self.bot_reader.report_user_progress(
                    book_id, chapter_index, total_chapters, book_title
                )
            return {"success": True}

        @app.get("/api/user-progress/{book_id}")
        async def api_user_progress(book_id: str):
            if not self.bot_reader:
                return {"success": True, "percent": 0}
            return {
                "success": True,
                "percent": self.bot_reader.get_user_progress_percent(book_id),
            }

        # --- Reading Progress API ---

        @app.get("/api/reading-progress")
        async def api_reading_progress():
            """Get bot and user reading progress for all books."""
            books = self.book_manager.list_books()
            bot_progress_list = []
            user_progress_list = []

            for book in books:
                book_id = book["id"]
                book_title = book.get("title", "未知")
                chapters = self.book_manager.get_chapters(book_id)
                total_chapters = (
                    len(chapters) if chapters else book.get("chapter_count", 0)
                )

                if not total_chapters:
                    continue

                # bot progress
                if self.bot_reader:
                    bot_prog = self.bot_reader.progress.get(book_id)
                    if bot_prog:
                        bot_current = bot_prog.get("current_chapter", 0) + 1
                        bot_pct = round(bot_current / total_chapters * 100, 1)
                        bot_progress_list.append(
                            {
                                "book_title": book_title,
                                "current_chapter": bot_current,
                                "total_chapters": total_chapters,
                                "percentage": min(bot_pct, 100.0),
                            }
                        )

                    # user progress
                    user_prog = self.bot_reader.user_progress.get(book_id)
                    if user_prog:
                        user_current = user_prog.get("current_chapter", 0) + 1
                        user_pct = round(user_current / total_chapters * 100, 1)
                        user_progress_list.append(
                            {
                                "book_title": book_title,
                                "current_chapter": user_current,
                                "total_chapters": total_chapters,
                                "percentage": min(user_pct, 100.0),
                            }
                        )

            return {
                "success": True,
                "bot_progress": bot_progress_list,
                "user_progress": user_progress_list,
            }

        # --- Reading Stats API ---

        @app.get("/api/stats")
        async def api_stats():
            """Get reading statistics."""
            books = self.book_manager.list_books()
            total_books = len(books)
            total_chapters = sum(b.get("chapter_count", 0) for b in books)

            # user reading stats
            user_chapters_read = 0
            bot_chapters_read = 0
            if self.bot_reader:
                for book in books:
                    bid = book["id"]
                    up = self.bot_reader.user_progress.get(bid, {})
                    bp = self.bot_reader.progress.get(bid, {})
                    if up:
                        user_chapters_read += up.get("current_chapter", 0) + 1
                    if bp:
                        bot_chapters_read += bp.get("current_chapter", 0) + 1

            # reading days (from user progress timestamps)
            reading_days = set()
            if self.bot_reader:
                for prog in self.bot_reader.user_progress.values():
                    ts = prog.get("last_read_at", 0)
                    if ts:
                        from datetime import datetime

                        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                        reading_days.add(day)

            # highlights count
            highlights_count = 0
            for book in books:
                highlights_count += len(self.book_manager.get_highlights(book["id"]))

            # sessions count
            sessions = self.session_manager.list_book_sessions()
            total_messages = sum(s.get("message_count", 0) for s in sessions)

            return {
                "success": True,
                "stats": {
                    "total_books": total_books,
                    "total_chapters": total_chapters,
                    "user_chapters_read": user_chapters_read,
                    "bot_chapters_read": bot_chapters_read,
                    "reading_days": len(reading_days),
                    "highlights_count": highlights_count,
                    "total_messages": total_messages,
                },
            }

        # --- Memory Management APIs ---

        @app.get("/api/memory/sessions")
        async def api_list_sessions():
            """List all book sessions with metadata."""
            sessions = self.session_manager.list_book_sessions()
            result = []
            for s in sessions:
                result.append(
                    {
                        "book_id": s["book_id"],
                        "book_title": s["book_title"],
                        "message_count": s["message_count"],
                        "last_active": s["last_active"],
                        "is_active": s["book_id"]
                        == self.session_manager.active_book_id,
                    }
                )
            return {"success": True, "sessions": result}

        @app.get("/api/memory/sessions/{book_id}")
        async def api_get_session_detail(book_id: str):
            """Get full chat history of a book session."""
            session = self.session_manager.get_book_session_detail(book_id)
            if not session:
                raise HTTPException(404, detail="session not found")
            return {"success": True, "session": session}

        @app.delete("/api/memory/sessions/{book_id}")
        async def api_delete_session(book_id: str):
            """Delete a book session entirely."""
            self.session_manager.delete_book_session(book_id)
            return {"success": True}

        @app.delete("/api/memory/sessions/{book_id}/messages/{message_id}")
        async def api_delete_session_message(book_id: str, message_id: str):
            """Delete a single message from a book session."""
            success = self.session_manager.delete_message(book_id, message_id)
            if not success:
                raise HTTPException(404, detail="message not found")
            return {"success": True}

        # Legacy endpoints for compatibility
        @app.get("/api/memory/archives")
        async def api_list_archives():
            """List all book sessions (legacy compatibility)."""
            sessions = self.session_manager.list_book_sessions()
            result = []
            for s in sessions:
                result.append(
                    {
                        "session_id": s["book_id"],
                        "book_title": s["book_title"],
                        "started_at": s["created_at"],
                        "ended_at": s["last_active"],
                        "message_count": s["message_count"],
                        "preview": self._get_session_preview_by_id(s["book_id"]),
                    }
                )
            return {"success": True, "archives": result}

        @app.get("/api/memory/archives/{session_id}")
        async def api_get_archive(session_id: str):
            """Get full session detail (legacy compatibility)."""
            session = self.session_manager.get_book_session_detail(session_id)
            if not session:
                raise HTTPException(404, detail="session not found")
            return {"success": True, "archive": session}

        @app.delete("/api/memory/archives/{session_id}")
        async def api_delete_archive(session_id: str):
            """Delete a session (legacy compatibility)."""
            self.session_manager.delete_book_session(session_id)
            return {"success": True}

        @app.delete("/api/memory/archives/{session_id}/messages/{message_id}")
        async def api_delete_archive_message(session_id: str, message_id: str):
            """Delete a message (legacy compatibility)."""
            success = self.session_manager.delete_message(session_id, message_id)
            if not success:
                raise HTTPException(404, detail="message not found")
            return {"success": True}

        @app.get("/api/memory/active")
        async def api_get_active_sessions():
            """Get currently active session info (legacy compatibility)."""
            sessions = []
            if (
                self.session_manager.active_book_id
                and self.session_manager.active_session
            ):
                s = self.session_manager.active_session
                sessions.append(
                    {
                        "session_id": self.session_manager.active_book_id,
                        "book_title": s.get("book_title", ""),
                        "started_at": s.get("created_at"),
                        "last_active": s.get("last_active"),
                        "message_count": len(s.get("chat_history", [])),
                        "preview": self._get_session_preview(s),
                    }
                )
            return {"success": True, "sessions": sessions}

        @app.delete("/api/memory/active/{session_id}/messages/{message_id}")
        async def api_delete_active_message(session_id: str, message_id: str):
            """Delete a message from active session (legacy compatibility)."""
            success = self.session_manager.delete_message(session_id, message_id)
            if not success:
                raise HTTPException(404, detail="message not found")
            return {"success": True}

        # --- Footprints APIs ---

        @app.get("/api/footprints")
        async def api_get_footprints():
            """Get all footprint items (photos, user notes, bot notes)."""
            profile = self._load_profile()
            items = profile.get("footprints", [])
            # Assign random positions to legacy photos missing pos_x/pos_y
            changed = False
            for item in items:
                if item.get("type") == "photo" and (
                    "pos_x" not in item or "pos_y" not in item
                ):
                    item["pos_x"] = round(random.uniform(5, 75), 1)
                    item["pos_y"] = round(random.uniform(5, 75), 1)
                    changed = True
            if changed:
                profile["footprints"] = items
                self._save_profile(profile)
            # sort by created_at descending
            items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return {"success": True, "items": items}

        @app.post("/api/footprints/upload")
        async def api_upload_footprint(file: UploadFile = File(...)):
            """Upload a photo to the footprints board."""
            if not file.filename:
                raise HTTPException(400, detail="no file provided")

            # validate image type
            allowed_ext = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
            ext = Path(file.filename).suffix.lower()
            if ext not in allowed_ext:
                raise HTTPException(400, detail="unsupported image format")

            file_bytes = await file.read()

            # create directories
            footprints_dir = self.assets_dir / "footprints"
            originals_dir = footprints_dir / "originals"
            thumbs_dir = footprints_dir / "thumbs"
            originals_dir.mkdir(parents=True, exist_ok=True)
            thumbs_dir.mkdir(parents=True, exist_ok=True)

            # generate unique filename
            item_id = uuid.uuid4().hex[:12]
            filename = f"{item_id}.jpg"

            # save original
            original_path = originals_dir / filename
            original_path.write_bytes(file_bytes)

            # create thumbnail using Pillow
            try:
                import io

                from PIL import Image

                img = Image.open(io.BytesIO(file_bytes))
                # convert to RGB if necessary (for PNG with alpha, etc.)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                # resize to max 300px width maintaining aspect ratio
                max_width = 300
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)
                # save thumbnail
                thumb_path = thumbs_dir / filename
                img.save(thumb_path, "JPEG", quality=85)
            except Exception as e:
                # if thumbnail creation fails, copy original as thumb
                logger.warning(f"乌鲁鲁星: thumbnail creation failed: {e}")
                thumb_path = thumbs_dir / filename
                thumb_path.write_bytes(file_bytes)

            # store metadata in profile
            rotation = random.randint(-6, 6)

            # Generate random position (percentage) avoiding overlap
            profile = self._load_profile()
            existing_photos = [
                fp for fp in profile.get("footprints", []) if fp.get("type") == "photo"
            ]
            pos_x = random.uniform(5, 75)
            pos_y = random.uniform(5, 75)
            for _ in range(10):
                overlap = False
                for ep in existing_photos:
                    ex = ep.get("pos_x", 50)
                    ey = ep.get("pos_y", 50)
                    if abs(ex - pos_x) < 15 and abs(ey - pos_y) < 15:
                        overlap = True
                        break
                if not overlap:
                    break
                pos_x = random.uniform(5, 75)
                pos_y = random.uniform(5, 75)

            footprint_item = {
                "id": item_id,
                "type": "photo",
                "filename": filename,
                "caption": "",
                "created_at": int(time.time()),
                "rotation": rotation,
                "pos_x": round(pos_x, 1),
                "pos_y": round(pos_y, 1),
            }

            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            footprints.append(footprint_item)
            profile["footprints"] = footprints
            self._save_profile(profile)

            return {"success": True, "item": footprint_item}

        @app.delete("/api/footprints/{item_id}")
        async def api_delete_footprint(item_id: str):
            """Delete a photo from the footprints board."""
            profile = self._load_profile()
            footprints = profile.get("footprints", [])

            # find the item
            item = None
            for fp in footprints:
                if fp.get("id") == item_id:
                    item = fp
                    break

            if not item:
                raise HTTPException(404, detail="footprint not found")

            # remove files
            footprints_dir = self.assets_dir / "footprints"
            original_path = footprints_dir / "originals" / item["filename"]
            thumb_path = footprints_dir / "thumbs" / item["filename"]
            if original_path.exists():
                original_path.unlink()
            if thumb_path.exists():
                thumb_path.unlink()

            # remove from profile
            footprints = [fp for fp in footprints if fp.get("id") != item_id]
            profile["footprints"] = footprints
            self._save_profile(profile)

            return {"success": True}

        @app.post("/api/footprints/{item_id}/position")
        async def api_update_footprint_position(item_id: str, request: Request):
            """Update a photo's position after drag."""
            data = await request.json()
            pos_x = data.get("pos_x")
            pos_y = data.get("pos_y")
            if pos_x is None or pos_y is None:
                raise HTTPException(400, detail="pos_x and pos_y are required")

            # Clamp values to valid range
            pos_x = max(0, min(95, float(pos_x)))
            pos_y = max(0, min(95, float(pos_y)))

            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            found = False
            for fp in footprints:
                if fp.get("id") == item_id:
                    fp["pos_x"] = round(pos_x, 1)
                    fp["pos_y"] = round(pos_y, 1)
                    found = True
                    break
            if not found:
                raise HTTPException(404, detail="footprint not found")

            profile["footprints"] = footprints
            self._save_profile(profile)
            return {"success": True}

        @app.post("/api/footprints/note")
        async def api_post_footprint_note(request: Request):
            """Post a user note (sticky note). Bot will reply asynchronously."""
            data = await request.json()
            content = data.get("content", "").strip()
            if not content:
                raise HTTPException(400, detail="content is empty")

            profile = self._load_profile()
            notes = profile.get("fp_notes", [])

            # Generate random position (percentage) avoiding overlap
            pos_x = random.uniform(5, 75)
            pos_y = random.uniform(5, 75)
            # Try to avoid overlapping with existing notes
            for _ in range(10):
                overlap = False
                for n in notes:
                    nx = n.get("pos_x", 50)
                    ny = n.get("pos_y", 50)
                    if abs(nx - pos_x) < 15 and abs(ny - pos_y) < 15:
                        overlap = True
                        break
                if not overlap:
                    break
                pos_x = random.uniform(5, 75)
                pos_y = random.uniform(5, 75)

            note_item = {
                "id": f"note_{int(time.time())}_{random.randint(100, 999)}",
                "content": content,
                "created_at": int(time.time()),
                "reply": None,
                "reply_at": None,
                "pos_x": round(pos_x, 1),
                "pos_y": round(pos_y, 1),
            }
            notes.append(note_item)
            profile["fp_notes"] = notes
            self._save_profile(profile)

            # trigger bot reply in background (delayed)
            if self.bot_reader:
                asyncio.create_task(self._generate_note_reply(note_item["id"], content))

            return {"success": True, "item": note_item}

        @app.get("/api/footprints/notes")
        async def api_get_fp_notes():
            """Get all sticky notes with bot replies."""
            profile = self._load_profile()
            notes = profile.get("fp_notes", [])
            # Assign random positions to legacy notes missing pos_x/pos_y
            changed = False
            for n in notes:
                if "pos_x" not in n or "pos_y" not in n:
                    n["pos_x"] = round(random.uniform(5, 75), 1)
                    n["pos_y"] = round(random.uniform(5, 75), 1)
                    changed = True
            if changed:
                profile["fp_notes"] = notes
                self._save_profile(profile)
            # sort newest first
            notes.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return {"success": True, "notes": notes}

        @app.post("/api/footprints/notes/{note_id}/position")
        async def api_update_note_position(note_id: str, request: Request):
            """Update a sticky note's position after drag."""
            data = await request.json()
            pos_x = data.get("pos_x")
            pos_y = data.get("pos_y")
            if pos_x is None or pos_y is None:
                raise HTTPException(400, detail="pos_x and pos_y are required")

            # Clamp values to valid range
            pos_x = max(0, min(95, float(pos_x)))
            pos_y = max(0, min(95, float(pos_y)))

            profile = self._load_profile()
            notes = profile.get("fp_notes", [])
            found = False
            for n in notes:
                if n.get("id") == note_id:
                    n["pos_x"] = round(pos_x, 1)
                    n["pos_y"] = round(pos_y, 1)
                    found = True
                    break
            if not found:
                raise HTTPException(404, detail="note not found")

            profile["fp_notes"] = notes
            self._save_profile(profile)
            return {"success": True}

        @app.delete("/api/footprints/notes/{note_id}")
        async def api_delete_fp_note(note_id: str):
            """Delete a sticky note (and its bot reply)."""
            profile = self._load_profile()
            notes = profile.get("fp_notes", [])
            notes = [n for n in notes if n.get("id") != note_id]
            profile["fp_notes"] = notes
            self._save_profile(profile)
            return {"success": True}

        @app.get("/api/footprints/moments")
        async def api_get_moments():
            """Get all moments (bot and user dynamics)."""
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            moments = [
                fp
                for fp in footprints
                if fp.get("type") in ("bot_note", "user_note")
            ]
            moments.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return {"success": True, "moments": moments}

        @app.post("/api/footprints/moments")
        async def api_post_user_moment(request: Request):
            """Post a user moment (dynamic). Bot will like and reply asynchronously."""
            data = await request.json()
            content = data.get("content", "").strip()
            if not content:
                raise HTTPException(400, detail="content is empty")

            moment_item = {
                "id": f"user_moment_{int(time.time())}_{random.randint(100, 999)}",
                "type": "user_note",
                "content": content,
                "created_at": int(time.time()),
                "rotation": random.randint(-3, 3),
                "liked": False,
                "like_count": 0,
                "replies": [],
            }

            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            footprints.append(moment_item)
            profile["footprints"] = footprints
            self._save_profile(profile)

            # trigger bot like + reply in background
            if self.bot_reader:
                asyncio.create_task(
                    self._bot_react_to_user_moment(moment_item["id"], content)
                )

            return {"success": True, "item": moment_item}

        @app.post("/api/footprints/moments/{moment_id}/like")
        async def api_like_moment(moment_id: str):
            """Toggle user like on a moment. Separate from bot likes."""
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            for fp in footprints:
                if fp.get("id") == moment_id:
                    # Use user_liked to track user's like independently from bot
                    fp["user_liked"] = not fp.get("user_liked", False)
                    # Recalculate total like_count from both sources
                    bot_liked = fp.get("bot_liked", False)
                    user_liked = fp.get("user_liked", False)
                    fp["like_count"] = (1 if bot_liked else 0) + (
                        1 if user_liked else 0
                    )
                    # Keep legacy "liked" field as "anyone liked" for display
                    fp["liked"] = bot_liked or user_liked
                    break
            profile["footprints"] = footprints
            self._save_profile(profile)
            return {"success": True}

        @app.post("/api/footprints/moments/{moment_id}/reply")
        async def api_reply_moment(moment_id: str, request: Request):
            """Reply to a moment or a specific comment. Bot will reply back asynchronously."""
            data = await request.json()
            content = data.get("content", "").strip()
            reply_to = data.get("reply_to", None)  # name of person being replied to
            if not content:
                raise HTTPException(400, detail="content is empty")

            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            user_nick = profile.get("user_nickname", "你")
            for fp in footprints:
                if fp.get("id") == moment_id:
                    if "replies" not in fp:
                        fp["replies"] = []
                    reply_item = {
                        "role": "user",
                        "content": content,
                        "time": int(time.time()),
                    }
                    if reply_to:
                        reply_item["reply_to"] = reply_to
                    fp["replies"].append(reply_item)
                    # trigger bot reply in background
                    if self.bot_reader:
                        asyncio.create_task(
                            self._generate_moment_reply(
                                moment_id,
                                fp.get("content", ""),
                                content,
                                reply_to=reply_to,
                                user_nick=user_nick,
                            )
                        )
                    break
            profile["footprints"] = footprints
            self._save_profile(profile)
            return {"success": True}

        @app.delete("/api/footprints/moments/{moment_id}")
        async def api_delete_moment(moment_id: str):
            """Delete a bot moment (dynamic)."""
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            footprints = [fp for fp in footprints if fp.get("id") != moment_id]
            profile["footprints"] = footprints
            self._save_profile(profile)
            return {"success": True}

        # --- Bot Memory Management ---

        @app.delete("/api/bot-memories/{book_id}/{chapter_index}")
        async def api_delete_bot_memory(book_id: str, chapter_index: str):
            """Delete a specific bot chapter memory.

            After deletion, the next auto-reading tick will regenerate
            the memory for this chapter (since it's no longer in the
            completed chapters list).
            """
            if not self.bot_reader:
                raise HTTPException(500, detail="bot reader not available")

            if book_id in self.bot_reader.memories:
                if chapter_index in self.bot_reader.memories[book_id]:
                    del self.bot_reader.memories[book_id][chapter_index]
                    # remove empty book entry
                    if not self.bot_reader.memories[book_id]:
                        del self.bot_reader.memories[book_id]
                    self.bot_reader._save_memories()
            return {"success": True}

        # --- Pet House APIs ---

        @app.get("/api/pets")
        async def api_list_pets():
            """List all pets with time-based decay applied."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")
            mgr = self.plugin.pet_house_manager
            pets = await mgr.list_pets()

            pets_data = []
            for p in pets:
                d = asdict(p)
                d["animation_state"] = mgr.get_animation_state(p)
                # Normalize customization_data for compatibility fallbacks
                d["customization_data"] = mgr.normalize_customization_data(
                    p.species, p.customization_data
                )
                pets_data.append(d)

            return {"success": True, "pets": pets_data}

        @app.post("/api/pets")
        async def api_create_pet(request: Request):
            """Create a new pet with name and species."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            data = await request.json()
            name = data.get("name", "")
            species = data.get("species", "")

            if not name or not name.strip():
                raise HTTPException(400, detail="name is required")
            if not species:
                raise HTTPException(400, detail="species is required")

            try:
                pet = await self.plugin.pet_house_manager.create_pet(name, species)
            except ValueError as e:
                raise HTTPException(400, detail=str(e))

            pet_data = asdict(pet)
            pet_data["animation_state"] = (
                self.plugin.pet_house_manager.get_animation_state(pet)
            )
            return {"success": True, "pet": pet_data}

        @app.put("/api/pets/{pet_id}")
        async def api_update_pet(pet_id: str, request: Request):
            """Update a pet's name."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            data = await request.json()
            new_name = data.get("name", "")

            if not new_name or not new_name.strip():
                raise HTTPException(400, detail="name is required")

            try:
                pet = await self.plugin.pet_house_manager.update_pet_name(
                    pet_id, new_name
                )
            except KeyError:
                raise HTTPException(404, detail="pet not found")
            except ValueError as e:
                raise HTTPException(400, detail=str(e))

            pet_data = asdict(pet)
            pet_data["animation_state"] = (
                self.plugin.pet_house_manager.get_animation_state(pet)
            )
            return {"success": True, "pet": pet_data}

        @app.delete("/api/pets/{pet_id}")
        async def api_delete_pet(pet_id: str):
            """Delete a pet by ID."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            try:
                await self.plugin.pet_house_manager.delete_pet(pet_id)
            except KeyError:
                raise HTTPException(404, detail="pet not found")

            return {"success": True}

        @app.post("/api/pets/{pet_id}/feed")
        async def api_feed_pet(pet_id: str):
            """Feed a pet, increasing hunger by 30 (capped at 100)."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            try:
                pet, comment = await self.plugin.pet_house_manager.feed_pet(pet_id)
            except KeyError:
                raise HTTPException(404, detail="pet not found")

            pet_data = asdict(pet)
            pet_data["animation_state"] = (
                self.plugin.pet_house_manager.get_animation_state(pet)
            )
            return {"success": True, "pet": pet_data, "comment": comment}

        @app.post("/api/pets/{pet_id}/pet")
        async def api_pet_pet(pet_id: str):
            """Pet (摸摸) a pet, increasing mood."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            try:
                pet, comment = await self.plugin.pet_house_manager.pet_pet(pet_id)
            except KeyError:
                raise HTTPException(404, detail="pet not found")

            pet_data = asdict(pet)
            pet_data["animation_state"] = (
                self.plugin.pet_house_manager.get_animation_state(pet)
            )
            return {"success": True, "pet": pet_data, "comment": comment}

        @app.get("/api/pets/{pet_id}/photo")
        async def api_get_pet_photo(pet_id: str):
            """Serve a pet's ID photo file."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            mgr = self.plugin.pet_house_manager
            pet = mgr._pets.get(pet_id)
            if not pet:
                raise HTTPException(404, detail="pet not found")

            if not pet.photo_filename:
                raise HTTPException(404, detail="no photo uploaded")

            photo_path = mgr._data_dir / pet.photo_filename
            if not photo_path.exists():
                raise HTTPException(404, detail="photo file not found")

            return FileResponse(str(photo_path))

        @app.post("/api/pets/{pet_id}/photo")
        async def api_upload_pet_photo(pet_id: str, file: UploadFile = File(...)):
            """Upload an ID photo for a pet (multipart file upload)."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            if not file.filename:
                raise HTTPException(400, detail="no file provided")

            ext = Path(file.filename).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                raise HTTPException(400, detail="unsupported image format")

            photo_bytes = await file.read()

            try:
                filename = await self.plugin.pet_house_manager.upload_photo(
                    pet_id, photo_bytes, ext
                )
            except KeyError:
                raise HTTPException(404, detail="pet not found")
            except ValueError as e:
                raise HTTPException(400, detail=str(e))

            return {"success": True, "filename": filename}

        # --- Pet Customization APIs ---

        @app.get("/api/pets/{pet_id}/customization")
        async def api_get_pet_customization(pet_id: str):
            """Get a pet's customization data, normalized for compatibility.

            If the pet has no customization_data (legacy pet), returns the
            species default. If stored data references deleted templates,
            colors, patterns, or accessories, they are replaced with safe
            fallback values before returning.
            """
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            mgr = self.plugin.pet_house_manager
            pet = await mgr.get_pet(pet_id)
            if pet is None:
                raise HTTPException(404, detail="pet not found")

            # Normalize handles None (legacy) and invalid values (deleted items)
            customization = mgr.normalize_customization_data(
                pet.species, pet.customization_data
            )

            return {"success": True, "customization_data": customization}

        @app.put("/api/pets/{pet_id}/customization")
        async def api_update_pet_customization(pet_id: str, request: Request):
            """Validate and update a pet's customization data."""
            if not self.plugin or not getattr(self.plugin, "pet_house_manager", None):
                raise HTTPException(500, detail="pet house not available")

            mgr = self.plugin.pet_house_manager

            # Look up the pet (without applying decay for a simple existence check)
            async with mgr._lock:
                pet = mgr._pets.get(pet_id)
                if pet is None:
                    raise HTTPException(404, detail="pet not found")

                # Parse and validate request body
                data = await request.json()
                error = mgr.validate_customization_data(pet.species, data)
                if error is not None:
                    raise HTTPException(400, detail=error)

                # Update customization_data and persist
                pet.customization_data = data
                await mgr._save()

            # Build response with animation state
            pet_data = asdict(pet)
            pet_data["animation_state"] = mgr.get_animation_state(pet)
            return {"success": True, "pet": pet_data}

    async def _generate_note_reply(self, note_id: str, user_content: str):
        """Generate a bot reply to a user's sticky note (async, delayed)."""
        try:
            # small delay to simulate "thinking"
            await asyncio.sleep(random.uniform(3, 8))

            if not self.plugin or not hasattr(self.plugin, "context"):
                return

            provider = self.plugin.context.get_using_provider()
            if not provider:
                return

            # get persona for consistent character voice
            persona = ""
            if self.bot_reader:
                persona = await self.bot_reader._get_persona_prompt()

            default_prompt = (
                "她在便签板上给你留了一张纸条：「{{content}}」\n"
                "请用一两句话回复她，写在另一张便签纸上。"
                "风格要求：简短、温柔、自然，像随手写的回复。不要超过30个字。"
                "直接输出回复内容，不要加引号或前缀。"
                "只使用普通标点符号，不要使用特殊分隔符号。"
            )
            prompt_template = self._get_custom_prompt("prompt_note_reply", default_prompt)
            prompt = prompt_template.replace("{{content}}", user_content)

            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            reply_text = (resp.completion_text or "").strip()
            if not reply_text or len(reply_text) > 80:
                return

            # save reply to the note
            profile = self._load_profile()
            notes = profile.get("fp_notes", [])
            for note in notes:
                if note.get("id") == note_id:
                    note["reply"] = reply_text
                    note["reply_at"] = int(time.time())
                    break
            profile["fp_notes"] = notes
            self._save_profile(profile)

            logger.info(f"乌鲁鲁星: 便签回复已生成: {reply_text[:20]}...")
        except Exception as e:
            logger.debug(f"乌鲁鲁星: 便签回复生成失败: {e}")

    async def _generate_moment_reply(
        self,
        moment_id: str,
        moment_content: str,
        user_reply: str,
        reply_to: str | None = None,
        user_nick: str = "她",
    ):
        """Generate a bot reply to user's comment on a moment (async, delayed).

        Uses the full reply chain as conversation context so the bot can
        continue the thread naturally across multiple exchanges.
        """
        try:
            await asyncio.sleep(random.uniform(3, 8))

            if not self.plugin or not hasattr(self.plugin, "context"):
                return

            provider = self.plugin.context.get_using_provider()
            if not provider:
                return

            # get persona for consistent character voice
            persona = ""
            if self.bot_reader:
                persona = await self.bot_reader._get_persona_prompt()

            # Build conversation context from the full reply chain
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            moment = None
            for fp in footprints:
                if fp.get("id") == moment_id:
                    moment = fp
                    break

            if not moment:
                return

            # Construct the prompt with full reply history
            replies = moment.get("replies", [])
            is_user_moment = moment.get("type") == "user_note"
            bot_nick = profile.get("bot_nickname", "我")

            if is_user_moment:
                context_lines = [f"她发了一条动态：「{moment_content}」"]
            else:
                context_lines = [f"你之前发了一条动态：「{moment_content}」"]

            # Include previous replies as conversation context (skip the latest user reply)
            history_replies = replies[:-1] if replies else []
            if history_replies:
                context_lines.append("之前的评论区对话：")
                for r in history_replies[-10:]:
                    role_label = user_nick if r.get("role") == "user" else bot_nick
                    reply_to_str = r.get("reply_to", "")
                    if reply_to_str:
                        context_lines.append(
                            f"  {role_label} 回复 {reply_to_str}：{r.get('content', '')}"
                        )
                    else:
                        context_lines.append(
                            f"  {role_label}：{r.get('content', '')}"
                        )

            if reply_to:
                context_lines.append(
                    f"她回复了{reply_to}：「{user_reply}」"
                )
            else:
                context_lines.append(f"她评论了：「{user_reply}」")

            context_lines.append(
                self._get_custom_prompt(
                    "prompt_moment_reply",
                    f"请用一句话回复她的评论（你是{bot_nick}）。简短、自然、像朋友圈回复。不要超过20个字。"
                    "直接输出回复内容，只使用普通标点符号。",
                ).replace("{{bot_nick}}", bot_nick)
            )

            prompt = "\n".join(context_lines)

            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            reply_text = (resp.completion_text or "").strip()
            if not reply_text or len(reply_text) > 60:
                return

            # save reply with reply_to indicating it's replying to the user
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            for fp in footprints:
                if fp.get("id") == moment_id:
                    if "replies" not in fp:
                        fp["replies"] = []
                    fp["replies"].append(
                        {
                            "role": "bot",
                            "content": reply_text,
                            "time": int(time.time()),
                            "reply_to": user_nick,
                        }
                    )
                    break
            profile["footprints"] = footprints
            self._save_profile(profile)

            logger.info(f"乌鲁鲁星: 动态回复已生成: {reply_text[:20]}...")
        except Exception as e:
            logger.debug(f"乌鲁鲁星: 动态回复生成失败: {e}")

    async def _bot_react_to_user_moment(self, moment_id: str, user_content: str):
        """Bot reacts to a user's moment: likes it and posts a reply (async, delayed)."""
        try:
            # delay to simulate natural reaction time
            await asyncio.sleep(random.uniform(5, 15))

            if not self.plugin or not hasattr(self.plugin, "context"):
                return

            # Bot likes the moment
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            for fp in footprints:
                if fp.get("id") == moment_id:
                    fp["bot_liked"] = True
                    user_liked = fp.get("user_liked", False)
                    fp["like_count"] = 1 + (1 if user_liked else 0)
                    fp["liked"] = True
                    break
            profile["footprints"] = footprints
            self._save_profile(profile)

            # Small additional delay before replying
            await asyncio.sleep(random.uniform(2, 5))

            provider = self.plugin.context.get_using_provider()
            if not provider:
                return

            # get persona for consistent character voice
            persona = ""
            if self.bot_reader:
                persona = await self.bot_reader._get_persona_prompt()

            default_react_prompt = (
                "她发了一条动态：「{{content}}」\n"
                "请用一句话评论她的动态。简短、自然、像朋友圈评论。不要超过25个字。"
                "可以是夸赞、调侃、共鸣或好奇。"
                "直接输出评论内容，只使用普通标点符号。"
            )
            prompt = self._get_custom_prompt(
                "prompt_react_to_user_moment", default_react_prompt
            ).replace("{{content}}", user_content)

            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            reply_text = (resp.completion_text or "").strip()
            if not reply_text or len(reply_text) > 80:
                return

            # save bot reply
            profile = self._load_profile()
            footprints = profile.get("footprints", [])
            for fp in footprints:
                if fp.get("id") == moment_id:
                    if "replies" not in fp:
                        fp["replies"] = []
                    fp["replies"].append(
                        {
                            "role": "bot",
                            "content": reply_text,
                            "time": int(time.time()),
                        }
                    )
                    break
            profile["footprints"] = footprints
            self._save_profile(profile)

            logger.info(f"乌鲁鲁星: Bot 回应了用户动态: {reply_text[:20]}...")
        except Exception as e:
            logger.debug(f"乌鲁鲁星: Bot 回应用户动态失败: {e}")

    def _get_session_preview_by_id(self, book_id: str) -> str:
        """Get a short preview of a book session's content."""
        session = self.session_manager.get_book_session_detail(book_id)
        if not session:
            return "(空)"
        return self._get_session_preview(session)

    def _get_session_preview(self, session: dict) -> str:
        """Get a short preview of a session."""
        messages = session.get("chat_history", [])
        if not messages:
            return "(暂无对话)"
        last = messages[-1]
        content = last.get("content", "")
        if len(content) > 60:
            content = content[:60] + "..."
        role = "她" if last.get("role") == "user" else "bot"
        return f"{role}: {content}"

    # --- Profile persistence ---

    def _get_profile_path(self) -> Path:
        """Get the path to the profile data file."""
        if self.plugin and hasattr(self.plugin, "data_dir"):
            return self.plugin.data_dir / "profile.json"
        # fallback: store next to custom_templates
        return self.custom_templates_dir.parent / "profile.json"

    def _load_profile(self) -> dict:
        """Load profile data from disk."""
        path = self._get_profile_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _get_custom_prompt(self, key: str, default: str) -> str:
        """Get a custom prompt from config if enabled, otherwise return default."""
        if not self.plugin or not hasattr(self.plugin, "config"):
            return default
        config = self.plugin.config
        special = config.get("special", {})
        if not special.get("custom_prompts_enabled", False):
            return default
        custom = special.get(key, "").strip()
        return custom if custom else default

    def _save_profile(self, profile: dict):
        """Save profile data to disk."""
        path = self._get_profile_path()
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --- Server lifecycle ---

    async def start(self):
        """Start the uvicorn server."""
        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="warning",
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        # wait for startup
        for _ in range(50):
            if getattr(self._server, "started", False):
                return
            if self._server_task.done():
                error = self._server_task.exception()
                raise RuntimeError(f"WebUI 启动失败: {error}") from error
            await asyncio.sleep(0.1)

        logger.warning("乌鲁鲁星 WebUI 启动耗时较长，仍在后台启动中")

    async def stop(self):
        """Stop the uvicorn server."""
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._server = None
        self._server_task = None
        logger.info("乌鲁鲁星 WebUI 已停止")
