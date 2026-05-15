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

from .core.book_manager import BookManager
from .core.bot_reader import BotReader
from .core.chat_engine import ChatEngine
from .core.pet_house_manager import PetHouseManager
from .core.session_manager import SessionManager
from .core.webui_server import WebUIServer

PLUGIN_NAME = "astrbot_plugin_uluru_star"

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
    "astrbot_plugin_uluru_star",
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
            session_manager=self.session_manager,
            data_dir=self.data_dir,
        )

        # Pet house manager (conditional)
        interactions_config = config.get("interactions", {})
        pet_house_config = interactions_config.get("pet_house", {})
        self._pet_house_enabled = pet_house_config.get("enabled", True)
        if self._pet_house_enabled:
            self.pet_house_manager = PetHouseManager(data_dir=self.data_dir)
        else:
            self.pet_house_manager = None

        # WebUI server
        self.webui_server: WebUIServer | None = None
        self._server_task: asyncio.Task | None = None

        # start webui if enabled
        webui_config = config.get("webui", {})
        if webui_config.get("enabled", True):
            self._server_task = asyncio.create_task(self._start_webui(webui_config))

        # start bot reader background task
        self.bot_reader.start()
        self.bot_reader.start_dynamics()

        # start pet notification background task (only if pet house enabled)
        self._pet_notifier_task: asyncio.Task | None = None
        if self._pet_house_enabled:
            self._pet_notifier_task = asyncio.create_task(self._pet_notification_loop())

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
            yield event.plain_result(
                "📚 乌鲁鲁星的网页服务未启用，请在插件配置中开启。"
            )
            return

        host = webui_config.get("host", "0.0.0.0")
        port = webui_config.get("port", 1016)

        if host == "0.0.0.0":
            display_host = "你的设备IP"
            hint = f"（局域网内其他设备可通过 http://设备IP:{port} 访问）"
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
    async def inject_bookhouse_context(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """Inject lightweight bookshelf hint into LLM requests.

        Only injects a short list of book titles + progress (~200 chars).
        Detailed content (chapter text, chat history, memories) is accessed
        on-demand via tool calls (read_bookhouse_chapter, recall_bookhouse_chat).

        This avoids polluting the system prompt with large memory blocks
        that distract the model during non-book conversations.
        """
        # record user activity so bot reader won't interrupt active conversations
        self.bot_reader.record_user_activity()

        try:
            # Step 1: clean up old injected memories from conversation history
            # (handles legacy injections from before this refactor)
            self._remove_old_injection(req)

            # Step 2: build lightweight bookshelf hint
            books = self.book_manager.list_books()
            if not books:
                return

            book_lines = []
            for book in books[:10]:
                title = book.get("title", "未知")
                book_id = book.get("id", "")
                bot_progress = self.bot_reader.get_bot_progress_percent(book_id)
                user_progress = self.bot_reader.get_user_progress_percent(book_id)
                progress_parts = []
                if bot_progress > 0:
                    progress_parts.append(f"你{bot_progress}%")
                if user_progress > 0:
                    progress_parts.append(f"她{user_progress}%")
                suffix = f" ({'/'.join(progress_parts)})" if progress_parts else ""
                book_lines.append(f"  《{title}》{suffix}")

            # Step 3: build footprint context (recent notes and moments)
            footprint_hint = ""
            try:
                profile_path = self.data_dir / "profile.json"
                if profile_path.exists():
                    import json

                    profile = json.loads(profile_path.read_text(encoding="utf-8"))

                    # Recent sticky notes (last 3)
                    fp_notes = profile.get("fp_notes", [])
                    recent_notes = sorted(
                        fp_notes, key=lambda x: x.get("created_at", 0), reverse=True
                    )[:3]
                    if recent_notes:
                        note_lines = []
                        for n in recent_notes:
                            content = n.get("content", "")[:50]
                            reply = n.get("reply", "")
                            line = f"  她写：「{content}」"
                            if reply:
                                line += f" → 你回：「{reply[:30]}」"
                            note_lines.append(line)
                        footprint_hint += (
                            "最近的便签：\n" + "\n".join(note_lines) + "\n"
                        )

                    # Recent moments (last 3)
                    footprints = profile.get("footprints", [])
                    moments = [
                        fp
                        for fp in footprints
                        if fp.get("type") in ("bot_note", "user_note")
                    ]
                    moments.sort(key=lambda x: x.get("created_at", 0), reverse=True)
                    recent_moments = moments[:3]
                    if recent_moments:
                        moment_lines = []
                        for m in recent_moments:
                            who = "她" if m.get("type") == "user_note" else "你"
                            content = m.get("content", "")[:50]
                            moment_lines.append(f"  {who}发了动态：「{content}」")
                        footprint_hint += (
                            "最近的动态：\n" + "\n".join(moment_lines) + "\n"
                        )
            except Exception:
                pass

            # Step 4: inject minimal hint
            injection = (
                f"{BOOKHOUSE_INJECTION_HEADER}\n"
                f"乌鲁鲁星是你们一起看书的地方。书架上有：\n"
                + "\n".join(book_lines)
                + "\n"
                + (footprint_hint + "\n" if footprint_hint else "")
                + f"需要回忆书的内容时调用 read_bookhouse_chapter 工具；"
                f"需要回忆聊天记录时调用 recall_bookhouse_chat 工具。\n"
                f"{BOOKHOUSE_INJECTION_FOOTER}"
            )

            # Apply injection based on configured method
            special = self.config.get("special", {})
            injection_method = special.get("injection_method", "system_prompt")

            if injection_method == "user_message_before":
                req.prompt = injection + "\n\n" + (req.prompt or "")
            elif injection_method == "user_message_after":
                req.prompt = (req.prompt or "") + "\n\n" + injection
            else:
                # default: system_prompt (invisible)
                req.system_prompt = (req.system_prompt or "") + "\n\n" + injection

            logger.debug(
                f"乌鲁鲁星: 注入书架提示到 LLM 上下文 "
                f"(书架={len(books)}本, 方式={injection_method})"
            )

        except Exception as e:
            logger.error(f"乌鲁鲁星: 注入书架提示失败: {e}", exc_info=True)

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
        if (
            hasattr(req, "prompt")
            and req.prompt
            and BOOKHOUSE_INJECTION_HEADER in req.prompt
        ):
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
                        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
                        if not cleaned:
                            removed += 1
                            continue
                        if cleaned != msg:
                            removed += 1
                            filtered.append(cleaned)
                            continue
                elif isinstance(msg, dict):
                    content = msg.get("content", "")

                    # Handle string content
                    if isinstance(content, str):
                        if (
                            BOOKHOUSE_INJECTION_HEADER in content
                            and BOOKHOUSE_INJECTION_FOOTER in content
                        ):
                            cleaned = self._cleanup_pattern.sub("", content).strip()
                            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
                            if not cleaned:
                                removed += 1
                                continue
                            if cleaned != content:
                                removed += 1
                                msg_copy = msg.copy()
                                msg_copy["content"] = cleaned
                                filtered.append(msg_copy)
                                continue

                    # Handle list content (multimodal format)
                    elif isinstance(content, list):
                        cleaned_parts = []
                        has_changes = False
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part.get("text", "")
                                if (
                                    isinstance(text, str)
                                    and BOOKHOUSE_INJECTION_HEADER in text
                                    and BOOKHOUSE_INJECTION_FOOTER in text
                                ):
                                    cleaned_text = self._cleanup_pattern.sub(
                                        "", text
                                    ).strip()
                                    cleaned_text = re.sub(
                                        r"\n{3,}", "\n\n", cleaned_text
                                    )
                                    if not cleaned_text:
                                        has_changes = True
                                        removed += 1
                                        continue
                                    if cleaned_text != text:
                                        has_changes = True
                                        removed += 1
                                        part_copy = part.copy()
                                        part_copy["text"] = cleaned_text
                                        cleaned_parts.append(part_copy)
                                        continue
                            cleaned_parts.append(part)

                        if not cleaned_parts:
                            removed += 1
                            continue
                        if has_changes:
                            msg_copy = msg.copy()
                            msg_copy["content"] = cleaned_parts
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

        # Fix handler_module_path: add_llm_tools resolves it from
        # FunctionTool.__module__ (which is astrbot.core.agent.tool),
        # not from the plugin module. We override it to the correct path
        # so AstrBot can properly associate these tools with our plugin.
        correct_module_path = self.__class__.__module__
        read_tool.handler_module_path = correct_module_path
        recall_tool.handler_module_path = correct_module_path

        logger.info(
            "乌鲁鲁星: 已注册 LLM 工具 read_bookhouse_chapter, recall_bookhouse_chat"
            f" (module_path={correct_module_path})"
        )

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
            if (
                book_title.lower() in title.lower()
                or title.lower() in book_title.lower()
            ):
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
        # Only advance current_chapter when reading sequentially (no explicit jump).
        # Jump reading just generates a memory without moving the sequential pointer.
        is_jump_read = (
            chapter_index is not None and target_chapter != current_chapter + 1
        )
        if is_jump_read:
            # don't move the sequential progress pointer
            # just record that we read this chapter (for offset tracking if needed)
            pass
        else:
            self.bot_reader.progress[book_id] = {
                "current_chapter": target_chapter,
                "total_chapters": len(chapters),
                "book_title": book_name,
                "last_read_at": time.time(),
                "current_offset": new_offset,
            }
            self.bot_reader._save_progress()

        # format response
        chapter_title = chapters[target_chapter].get(
            "title", f"第{target_chapter + 1}章"
        )
        progress_pct = self.bot_reader.get_bot_progress_percent(book_id)

        status_parts = [
            f"你正在阅读《{book_name}》- {chapter_title}",
            f"(第{target_chapter + 1}/{len(chapters)}章，总进度{progress_pct}%)",
        ]

        if current_offset > 0:
            status_parts.append("(续读)")

        if not chapter_done:
            status_parts.append("\n...(本章未读完，下次调用将继续)")
        else:
            # chapter finished - trigger summary generation in background
            asyncio.create_task(
                self.bot_reader.generate_chapter_summary(book_id, target_chapter)
            )

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
                if (
                    book_title.lower() in title.lower()
                    or title.lower() in book_title.lower()
                ):
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
            history = (
                json.loads(conv.history)
                if isinstance(conv.history, str)
                else conv.history
            )
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

                    chat_history.append(
                        {
                            "id": f"snap_{len(chat_history):04d}",
                            "role": chat_role,
                            "content": content[:500],
                            "timestamp": conv.updated_at or 0,
                            "metadata": {"source": "astrbot_snapshot"},
                        }
                    )

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

    # ==================== Pet Notification Loop ====================

    async def _pet_notification_loop(self):
        """Background loop: check pet mood thresholds every 10 minutes."""
        # wait 10 minutes before first check
        await asyncio.sleep(600)

        while True:
            try:
                await self._check_pet_notifications()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"乌鲁鲁星: 宠物通知循环出错: {e}", exc_info=True)

            await asyncio.sleep(600)  # check every 10 minutes

    async def _check_pet_notifications(self):
        """Load pets, apply decay, check mood thresholds, send QQ notifications."""
        pets = await self.pet_house_manager.list_pets()
        for pet in pets:
            # reset notification state if mood recovered
            if pet.mood >= 20 and pet.notified:
                await self.pet_house_manager.reset_notification(pet.id)

            # check if notification is needed
            if self.pet_house_manager.check_notification_needed(pet):
                await self._send_pet_notification(pet)
                await self.pet_house_manager.mark_notified(pet.id)

    async def _send_pet_notification(self, pet):
        """Send a QQ notification about an unhappy pet.

        Uses the stored target_session from BotReader. Skips if no session
        is available. Handles send failures gracefully without crashing.
        """
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain

        if not self.bot_reader.target_session:
            logger.debug(f"乌鲁鲁星: 没有目标 session，跳过宠物通知 (pet={pet.name})")
            return

        message_text = f"你家{pet.name}好像不太开心，去看看它？"

        try:
            chain = MessageChain([Plain(text=message_text)])
            await self.context.send_message(self.bot_reader.target_session, chain)
            logger.info(
                f"乌鲁鲁星: 已发送宠物低心情通知 pet={pet.name} "
                f"mood={pet.mood} session={self.bot_reader.target_session}"
            )
        except Exception as e:
            logger.error(f"乌鲁鲁星: 发送宠物通知失败 pet={pet.name}: {e}")

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

        if self._pet_notifier_task and not self._pet_notifier_task.done():
            self._pet_notifier_task.cancel()
            try:
                await self._pet_notifier_task
            except asyncio.CancelledError:
                pass

        self.session_manager.save_all()
        logger.info("乌鲁鲁星插件已停止")
