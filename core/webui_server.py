"""
WebUI Server - serves the reading companion frontend and API.

Template loading priority:
  1. custom_templates_dir (user customizations in plugin_data, survives updates)
  2. plugin_dir/templates/ (default templates shipped with the plugin)
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from astrbot.api import logger

from .session_manager import SessionManager
from .book_manager import BookManager
from .chat_engine import ChatEngine


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
            if self.plugin and hasattr(self.plugin, "config"):
                separator = self.plugin.config.get("message_separator", "\\$")
            return {
                "success": True,
                "message_separator": separator,
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

            session = self.session_manager.get_or_create_book_session(book_id, book_title)

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
                file.filename.endswith(".epub") or file.filename.endswith(".txt")
            ):
                raise HTTPException(400, detail="only .epub and .txt files are supported")

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

            if not all([session_token, book_id, text]):
                raise HTTPException(400, detail="missing required fields")

            highlight = self.book_manager.add_highlight(
                book_id, chapter_index, text, context_text
            )

            # record highlight as a system note in chat history
            # so the LLM knows what user highlighted when they ask about it
            try:
                note_content = f"[系统提示] 她划线了：「{text}」"
                if context_text:
                    note_content += f"\n（上下文：{context_text[:150]}）"
                self.session_manager.add_chat_message(
                    book_id, "user", note_content, metadata={"type": "highlight", "silent": True}
                )
                logger.debug(f"乌鲁鲁星: 划线已记录到对话历史 book={book_id} text={text[:30]}")
            except Exception as e:
                logger.warning(f"乌鲁鲁星: 划线记录到对话历史失败: {e}")

            return {"success": True, "highlight": highlight}

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

            bot_reply = await self.chat_engine.respond_to_review(
                book_id, book_id, chapter_index, content
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
            """Mark a chapter as complete (打卡) and trigger bot summary."""
            data = await request.json()
            book_id = data.get("book_id")
            chapter_index = data.get("chapter_index")

            if not book_id or chapter_index is None:
                raise HTTPException(400, detail="missing required fields")

            # trigger bot summary generation in background
            if self.bot_reader:
                asyncio.create_task(
                    self.bot_reader.generate_chapter_summary(book_id, chapter_index)
                )

            return {"success": True, "message": "打卡成功"}

        # --- Data query APIs ---

        @app.get("/api/data/{book_id}/highlights")
        async def api_get_highlights(book_id: str):
            return {"success": True, "highlights": self.book_manager.get_highlights(book_id)}

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
            return {"success": True, "percent": self.bot_reader.get_bot_progress_percent(book_id)}

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
                for ch_idx, summary in sorted(chapters.items(), key=lambda x: int(x[0])):
                    result.append({
                        "book_id": book_id,
                        "book_title": book_title,
                        "chapter_index": int(ch_idx),
                        "summary": summary,
                    })
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
            return {"success": True, "percent": self.bot_reader.get_user_progress_percent(book_id)}

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
                result.append({
                    "book_id": s["book_id"],
                    "book_title": s["book_title"],
                    "message_count": s["message_count"],
                    "last_active": s["last_active"],
                    "is_active": s["book_id"] == self.session_manager.active_book_id,
                })
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
                result.append({
                    "session_id": s["book_id"],
                    "book_title": s["book_title"],
                    "started_at": s["created_at"],
                    "ended_at": s["last_active"],
                    "message_count": s["message_count"],
                    "preview": self._get_session_preview_by_id(s["book_id"]),
                })
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
            if self.session_manager.active_book_id and self.session_manager.active_session:
                s = self.session_manager.active_session
                sessions.append({
                    "session_id": self.session_manager.active_book_id,
                    "book_title": s.get("book_title", ""),
                    "started_at": s.get("created_at"),
                    "last_active": s.get("last_active"),
                    "message_count": len(s.get("chat_history", [])),
                    "preview": self._get_session_preview(s),
                })
            return {"success": True, "sessions": sessions}

        @app.delete("/api/memory/active/{session_id}/messages/{message_id}")
        async def api_delete_active_message(session_id: str, message_id: str):
            """Delete a message from active session (legacy compatibility)."""
            success = self.session_manager.delete_message(session_id, message_id)
            if not success:
                raise HTTPException(404, detail="message not found")
            return {"success": True}

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
