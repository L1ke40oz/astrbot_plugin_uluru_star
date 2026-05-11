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

        if self.default_templates_dir.exists():
            app.mount(
                "/static",
                StaticFiles(directory=self.default_templates_dir),
                name="static",
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
            if not file.filename or not file.filename.endswith(".epub"):
                raise HTTPException(400, detail="only .epub files are supported")

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

            bot_reply = await self.chat_engine.respond_to_highlight(
                book_id, book_id, chapter_index, text, context_text
            )

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

            if not book_id or not content:
                raise HTTPException(400, detail="missing required fields")

            bot_reply = await self.chat_engine.chat(book_id, content, book_id)
            return {"success": True, "reply": bot_reply}

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
