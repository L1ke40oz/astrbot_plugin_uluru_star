"""
乌鲁鲁星 - AstrBot Plugin
这是属于你们的故事。
"""

import asyncio
import json
import re
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.tool import FunctionTool

from .core.session_manager import SessionManager
from .core.book_manager import BookManager
from .core.chat_engine import ChatEngine
from .core.webui_server import WebUIServer
from .core.bot_reader import BotReader

PLUGIN_NAME = "astrbot_plugin_shared_read"

# max archived sessions to include in injection
MAX_ARCHIVE_INJECT = 2

# injection markers for cleanup
BOOKHOUSE_INJECTION_HEADER = "<UluruStar-Memory>"
BOOKHOUSE_INJECTION_FOOTER = "</UluruStar-Memory>"

# read book tool parameters schema
READ_BOOK_TOOL_PARAMS = {
    "type": "object",
    "properties": {
        "book_title": {
            "type": "string",
            "description": "要阅读的书名（模糊匹配即可）",
        },
        "chapter_index": {
            "type": "integer",
            "description": "要阅读的章节序号（从0开始）。不指定则阅读当前进度的下一章。",
        },
    },
    "required": ["book_title"],
}

# recall chat tool parameters schema
RECALL_CHAT_TOOL_PARAMS = {
    "type": "object",
    "properties": {
        "book_title": {
            "type": "string",
            "description": "要回忆哪本书的聊天记录（模糊匹配）。留空则返回所有书的近期对话。",
        },
    },
    "required": [],
}


@register(
    "astrbot_plugin_shared_read",
    "You",
    "乌鲁鲁星 - 这是属于你们的故事",
    "1.1.0",
    "",
)
class SharedReadPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.context = context
        self.config = config

        # data directory (persistent across plugin updates)
        self.data_dir = StarTools.get_data_dir()
        self.books_dir = self.data_dir / "books"
        self.sessions_dir = self.data_dir / "sessions"
        self.custom_templates_dir = self.data_dir / "custom_templates"
        self.assets_dir = self.data_dir / "assets"

        # ensure directories exist
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.custom_templates_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        # core components
        self.session_manager = SessionManager(
            sessions_dir=self.sessions_dir,
            timeout_minutes=config.get("session_timeout_minutes", 30),
        )
        self.book_manager = BookManager(books_dir=self.books_dir)
        self.chat_engine = ChatEngine(
            context=context,
            config=config,
            session_manager=self.session_manager,
            book_manager=self.book_manager,
        )

        # Bot reader (simulates bot reading books)
        self.bot_reader = BotReader(
            context=context,
            config=config,
            book_manager=self.book_manager,
            data_dir=self.data_dir,
        )

        # WebUI server
        self.webui_server: WebUIServer | None = None
        self._server_task: asyncio.Task | None = None

        # start webui if enabled
        webui_config = config.get("webui", {})
        if webui_config.get("enabled", True):
            self._server_task = asyncio.create_task(self._start_webui(webui_config))

        # start bot reader background task
        self.bot_reader.start()

        # register LLM tool for reading books on demand
        self._register_read_tool()

        # regex pattern for cleaning up injected memories
        self._cleanup_pattern = re.compile(
            re.escape(BOOKHOUSE_INJECTION_HEADER)
            + r".*?"
            + re.escape(BOOKHOUSE_INJECTION_FOOTER),
            flags=re.DOTALL,
        )

    async def _start_webui(self, webui_config: dict):
        """Start the WebUI server."""
        try:
            host = webui_config.get("host", "0.0.0.0")
            port = webui_config.get("port", 1016)

            self.webui_server = WebUIServer(
                host=host,
                port=port,
                session_manager=self.session_manager,
                book_manager=self.book_manager,
                chat_engine=self.chat_engine,
                custom_templates_dir=self.custom_templates_dir,
                plugin_dir=self._get_plugin_dir(),
                bot_reader=self.bot_reader,
                plugin=self,
                assets_dir=self.assets_dir,
            )
            await self.webui_server.start()
            display_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
            logger.info(f"乌鲁鲁星 WebUI 已启动: http://{display_host}:{port}")
        except Exception as e:
            logger.error(f"乌鲁鲁星 WebUI 启动失败: {e}", exc_info=True)

    def _get_plugin_dir(self):
        """Get the plugin's own directory (where default templates live)."""
        from pathlib import Path
        return Path(__file__).resolve().parent

    # ==================== Commands ====================

    @filter.command("乌鲁鲁星")
    async def cmd_bookhouse(self, event: AstrMessageEvent):
        """乌鲁鲁星入口指令"""
        # save this session as the target for proactive messages
        self.bot_reader.save_target_session(event.unified_msg_origin)

        webui_config = self.config.get("webui", {})
        if not webui_config.get("enabled", True):
            yield event.plain_result("📚 乌鲁鲁星的网页服务未启用，请在插件配置中开启。")
            return

        host = webui_config.get("host", "0.0.0.0")
        port = webui_config.get("port", 1016)

        if host == "0.0.0.0":
            display_host = "你的设备IP"
            hint = "（局域网内其他设备可通过 http://设备IP:{} 访问）".format(port)
        else:
            display_host = host
            hint = "（仅本机可访问）"

        yield event.plain_result(
            f"📚 乌鲁鲁星已到达！\n"
            f"访问地址: http://{display_host}:{port}\n"
            f"{hint}\n\n"
            f"在那里你可以上传书籍、划线、写书评，我会陪你一起读~"
        )

    # ==================== LLM Request Hook ====================

    @filter.on_llm_request()
    async def inject_bookhouse_context(self, event: AstrMessageEvent, req: ProviderRequest):
        """Inject reading memory into LLM requests (fake tool call pattern).

        On every LLM request:
        1. Remove previously injected memory from context history
        2. Build fresh memory from active sessions + recent archives
        3. Inject into system_prompt with markers for next-round cleanup

        This ensures reading memory only exists in the current request,
        never accumulates in conversation history.
        """
        # record user activity so bot reader won't interrupt active conversations
        self.bot_reader.record_user_activity()

        try:
            # Step 1: clean up old injected memories from conversation history
            self._remove_old_injection(req)

            # Step 2: build reading memory content (lightweight summaries)
            memory_parts = []

            # 2a: summaries of all book sessions (compact)
            summaries = self.session_manager.get_summaries_for_injection()
            if summaries:
                lines = ["[乌鲁鲁星对话摘要]"]
                for s in summaries:
                    lines.append(f"  《{s['book_title']}》({s['message_count']}条对话): {s['summary']}")
                lines.append("[/乌鲁鲁星对话摘要]")
                memory_parts.append("\n".join(lines))

            # 2b: book shelf info (so bot knows what books exist)
            books = self.book_manager.list_books()
            if books:
                book_lines = ["[书架]"]
                for book in books[:10]:
                    title = book.get("title", "未知")
                    book_id = book.get("id", "")
                    bot_prog = self.bot_reader.progress.get(book_id, {})
                    user_prog = self.bot_reader.user_progress.get(book_id, {})
                    bot_progress = self.bot_reader.get_bot_progress_percent(book_id)
                    user_progress = self.bot_reader.get_user_progress_percent(book_id)
                    parts = [f"  《{title}》"]
                    progress_info = []
                    if bot_progress > 0:
                        bot_ch = bot_prog.get("current_chapter", 0) + 1
                        bot_total = bot_prog.get("total_chapters", 0)
                        progress_info.append(f"你读到第{bot_ch}/{bot_total}章({bot_progress}%)")
                    if user_progress > 0:
                        user_ch = user_prog.get("current_chapter", 0) + 1
                        user_total = user_prog.get("total_chapters", 0)
                        progress_info.append(f"她读到第{user_ch}/{user_total}章({user_progress}%)")
                    if progress_info:
                        parts.append(f" ({', '.join(progress_info)})")
                    book_lines.append("".join(parts))
                book_lines.append("[/书架]")
                memory_parts.append("\n".join(book_lines))

            if not memory_parts:
                return

            # Step 3: format and inject
            memory_content = "\n\n".join(memory_parts)
            injection = (
                f"{BOOKHOUSE_INJECTION_HEADER}\n"
                f"以下是你和她在乌鲁鲁星里的记忆摘要。乌鲁鲁星是你们一起看书、聊书的地方。\n"
                f"如果她想详细讨论某本书的内容，你可以调用 read_bookhouse_chapter 工具获取章节原文。\n"
                f"如果想回忆你们在乌鲁鲁星里聊过什么，可以调用 recall_bookhouse_chat 工具获取完整对话。\n"
                f"注意：调用工具时不要同时输出回复文字，等工具返回结果后再统一回答。\n\n"
                f"{memory_content}\n"
                f"{BOOKHOUSE_INJECTION_FOOTER}"
            )

            req.system_prompt = (req.system_prompt or "") + "\n\n" + injection

            logger.debug(
                f"乌鲁鲁星: 注入摘要到 LLM 上下文 "
                f"(摘要={len(summaries)}条, 书架={len(books)}本)"
            )

        except Exception as e:
            logger.error(f"乌鲁鲁星: 注入记忆失败: {e}", exc_info=True)

    def _remove_old_injection(self, req: ProviderRequest):
        """Remove previously injected reading memory from request context.

        Cleans up system_prompt, prompt, and conversation history (contexts)
        to prevent memory accumulation across turns.
        """
        removed = 0

        # clean system_prompt
        if req.system_prompt and BOOKHOUSE_INJECTION_HEADER in req.system_prompt:
            cleaned = self._cleanup_pattern.sub("", req.system_prompt)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            if cleaned != req.system_prompt:
                req.system_prompt = cleaned
                removed += 1

        # clean prompt
        if hasattr(req, "prompt") and req.prompt and BOOKHOUSE_INJECTION_HEADER in req.prompt:
            cleaned = self._cleanup_pattern.sub("", req.prompt)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            if cleaned != req.prompt:
                req.prompt = cleaned
                removed += 1

        # clean conversation history (contexts)
        if hasattr(req, "contexts") and req.contexts:
            filtered = []
            for msg in req.contexts:
                if isinstance(msg, str):
                    if BOOKHOUSE_INJECTION_HEADER in msg:
                        cleaned = self._cleanup_pattern.sub("", msg).strip()
                        if not cleaned:
                            removed += 1
                            continue
                        if cleaned != msg:
                            removed += 1
                            filtered.append(cleaned)
                            continue
                elif isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, str) and BOOKHOUSE_INJECTION_HEADER in content:
                        cleaned = self._cleanup_pattern.sub("", content).strip()
                        if not cleaned:
                            removed += 1
                            continue
                        if cleaned != content:
                            removed += 1
                            msg_copy = msg.copy()
                            msg_copy["content"] = cleaned
                            filtered.append(msg_copy)
                            continue
                filtered.append(msg)
            req.contexts = filtered

        if removed > 0:
            logger.debug(f"乌鲁鲁星: 清理了 {removed} 处旧注入内容")

    def _register_read_tool(self):
        """Register LLM tools for reading books and recalling chat."""
        # Tool 1: read book chapters
        read_tool = FunctionTool(
            name="read_bookhouse_chapter",
            description=(
                "阅读乌鲁鲁星书架上的书籍章节。"
                "当她让你去看书、读某本书、或者问你某本书某章讲了什么时，调用这个工具获取章节内容。"
                "调用后你会获得该章节的文字内容，同时你的阅读进度会更新。"
            ),
            parameters=READ_BOOK_TOOL_PARAMS,
            handler=self._handle_read_book,
        )

        # Tool 2: recall chat history
        recall_tool = FunctionTool(
            name="recall_bookhouse_chat",
            description=(
                "回忆你和她在乌鲁鲁星里关于某本书的完整聊天记录。"
                "当她提到你们之前在乌鲁鲁星里聊过的内容、想继续之前的讨论、"
                "或者你需要回忆具体聊了什么时，调用这个工具。"
            ),
            parameters=RECALL_CHAT_TOOL_PARAMS,
            handler=self._handle_recall_chat,
        )

        self.context.add_llm_tools(read_tool, recall_tool)
        logger.info("乌鲁鲁星: 已注册 LLM 工具 read_bookhouse_chapter, recall_bookhouse_chat")

    async def _handle_read_book(self, *args, **kwargs) -> str:
        """Handler for the read_bookhouse_chapter tool.

        Called by the LLM when user asks bot to read a book.
        Fetches chapter content and updates bot reading progress.
        Supports reading within a chapter (offset-based continuation).

        Note: Due to functools.partial wrapping by star_manager, extra positional
        args (self_extra, event) are passed. We use *args to absorb them and
        extract tool parameters from **kwargs.
        """
        book_title = kwargs.get("book_title", "")
        chapter_index = kwargs.get("chapter_index")
        if not book_title:
            return "请告诉我要读哪本书。"

        # find the book by fuzzy title match
        books = self.book_manager.list_books()
        if not books:
            return "书架上还没有书。"

        matched_book = None
        for book in books:
            title = book.get("title", "")
            if book_title.lower() in title.lower() or title.lower() in book_title.lower():
                matched_book = book
                break

        if not matched_book:
            # try partial character match
            for book in books:
                title = book.get("title", "")
                if any(c in title for c in book_title if c.strip()):
                    matched_book = book
                    break

        if not matched_book:
            book_list = "、".join(f"《{b.get('title', '')}》" for b in books[:5])
            return f"没有找到《{book_title}》。书架上有: {book_list}"

        book_id = matched_book["id"]
        book_name = matched_book.get("title", "")

        # get chapters
        chapters = self.book_manager.get_chapters(book_id)
        if not chapters:
            return f"《{book_name}》的章节数据还没有解析好。"

        # get current progress
        prog = self.bot_reader.progress.get(book_id, {})
        current_chapter = prog.get("current_chapter", -1)
        current_offset = prog.get("current_offset", 0)

        # determine which chapter to read
        if chapter_index is not None:
            target_chapter = int(chapter_index)
            # if jumping to a different chapter, reset offset
            if target_chapter != current_chapter:
                current_offset = 0
        else:
            # continue reading: if we have an offset, stay on same chapter
            # otherwise advance to next chapter
            if current_offset > 0 and current_chapter >= 0:
                target_chapter = current_chapter
            else:
                target_chapter = current_chapter + 1
                current_offset = 0

        # clamp to valid range
        target_chapter = max(0, min(target_chapter, len(chapters) - 1))

        # get chapter text
        chapter_text = self.book_manager.get_chapter_text(book_id, target_chapter)
        if not chapter_text:
            return f"无法读取《{book_name}》第{target_chapter + 1}章的内容。"

        # apply offset for continuation
        remaining_text = chapter_text[current_offset:]
        chunk_size = 2000

        if len(remaining_text) <= chunk_size:
            # chapter finished, next call will advance to next chapter
            display_text = remaining_text
            new_offset = 0  # reset offset, next call goes to next chapter
            chapter_done = True
        else:
            # still more to read in this chapter
            display_text = remaining_text[:chunk_size]
            new_offset = current_offset + chunk_size
            chapter_done = False

        # update bot reading progress
        self.bot_reader.progress[book_id] = {
            "current_chapter": target_chapter,
            "total_chapters": len(chapters),
            "book_title": book_name,
            "last_read_at": time.time(),
            "current_offset": new_offset,
        }
        self.bot_reader._save_progress()

        # format response
        chapter_title = chapters[target_chapter].get("title", f"第{target_chapter + 1}章")
        progress_pct = self.bot_reader.get_bot_progress_percent(book_id)

        status_parts = [
            f"你正在阅读《{book_name}》- {chapter_title}",
            f"(第{target_chapter + 1}/{len(chapters)}章，总进度{progress_pct}%)",
        ]

        if current_offset > 0:
            status_parts.append("(续读)")

        if not chapter_done:
            status_parts.append("\n...(本章未读完，下次调用将继续)")

        return "\n".join(status_parts) + f"\n\n以下是内容：\n{display_text}"

    async def _handle_recall_chat(self, *args, **kwargs) -> str:
        """Handler for the recall_bookhouse_chat tool.

        Returns full chat history for a specific book or all books.
        Called by LLM when user wants to discuss past reading conversations.
        """
        book_title = kwargs.get("book_title", "")

        sessions = self.session_manager.list_book_sessions()
        if not sessions:
            return "乌鲁鲁星里还没有任何对话记录。"

        # filter by book title if provided
        if book_title:
            matched = []
            for s in sessions:
                title = s.get("book_title", "")
                if book_title.lower() in title.lower() or title.lower() in book_title.lower():
                    matched.append(s)
            if not matched:
                titles = "、".join(f"《{s['book_title']}》" for s in sessions[:5])
                return f"没有找到关于《{book_title}》的对话记录。有记录的书: {titles}"
            target_sessions = matched
        else:
            target_sessions = sessions[:3]  # limit to 3 most recent

        result_parts = []
        for s in target_sessions:
            detail = self.session_manager.get_book_session_detail(s["book_id"])
            if not detail:
                continue
            messages = detail.get("chat_history", [])
            if not messages:
                continue

            # take last 15 messages
            recent = messages[-15:]
            lines = [f"--- 《{s['book_title']}》的对话 ({len(messages)}条) ---"]
            for msg in recent:
                role = "她" if msg.get("role") == "user" else "你"
                lines.append(f"  {role}: {msg.get('content', '')[:200]}")
            result_parts.append("\n".join(lines))

        if not result_parts:
            return "找到了记录但没有对话内容。"

        return "以下是你们在乌鲁鲁星的对话记录：\n\n" + "\n\n".join(result_parts)

    # ==================== Session Snapshot (AstrBot → Reading Memory) ====================

    async def snapshot_astrbot_conversation(self, unified_msg_origin: str) -> bool:
        """Snapshot the current AstrBot conversation into a book session.

        Called when user opens the WebUI, this captures the recent AstrBot
        conversation (from QQ etc.) as a memory file so it appears
        in the memory management panel.

        Saves to sessions/books/astrbot_chat.json as a virtual "book" session.
        """
        try:
            conv_mgr = self.context.conversation_manager
            conv_id = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            if not conv_id:
                return False

            conv = await conv_mgr.get_conversation(unified_msg_origin, conv_id)
            if not conv or not conv.history:
                return False

            # parse conversation history
            history = json.loads(conv.history) if isinstance(conv.history, str) else conv.history
            if not history:
                return False

            # filter to recent messages only (last 20)
            recent = history[-20:]

            # convert to chat_history format
            chat_history = []
            for msg in recent:
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if not content or not isinstance(content, str):
                        continue
                    if role == "system":
                        continue
                    if BOOKHOUSE_INJECTION_HEADER in content:
                        continue
                    if role in ("user", "human"):
                        chat_role = "user"
                    elif role in ("assistant", "ai"):
                        chat_role = "bot"
                    else:
                        continue

                    chat_history.append({
                        "id": f"snap_{len(chat_history):04d}",
                        "role": chat_role,
                        "content": content[:500],
                        "timestamp": conv.updated_at or 0,
                        "metadata": {"source": "astrbot_snapshot"},
                    })

            if not chat_history:
                return False

            # save as a virtual book session
            books_dir = self.sessions_dir / "books"
            books_dir.mkdir(parents=True, exist_ok=True)
            snapshot_file = books_dir / "astrbot_chat.json"

            # check if we need to update
            if snapshot_file.exists():
                try:
                    existing = json.loads(snapshot_file.read_text(encoding="utf-8"))
                    if len(existing.get("chat_history", [])) >= len(chat_history):
                        return False
                except (json.JSONDecodeError, OSError):
                    pass

            snapshot_data = {
                "book_id": "astrbot_chat",
                "book_title": "QQ 对话记录",
                "created_at": conv.created_at or time.time(),
                "last_active": time.time(),
                "chat_history": chat_history,
            }
            snapshot_file.write_text(
                json.dumps(snapshot_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"乌鲁鲁星: 已生成对话快照 ({len(chat_history)} 条消息)")
            return True

        except Exception as e:
            logger.error(f"乌鲁鲁星: 生成对话快照失败: {e}", exc_info=True)
            return False

    async def terminate(self):
        """Plugin cleanup on shutdown."""
        if self.webui_server:
            await self.webui_server.stop()

        await self.bot_reader.stop()

        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        self.session_manager.save_all()
        logger.info("乌鲁鲁星插件已停止")
