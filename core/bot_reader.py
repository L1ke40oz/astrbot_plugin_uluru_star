"""
Bot Reader - simulates the bot reading books on its own schedule.

The bot periodically advances its reading progress and occasionally
sends proactive messages to the user about what it just read.
"""

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from .book_manager import BookManager


class BotReader:
    def __init__(
        self,
        context: Context,
        config: dict[str, Any],
        book_manager: BookManager,
        data_dir: Path,
    ):
        self.context = context
        self.config = config
        self.book_manager = book_manager
        self.data_dir = data_dir
        self.progress_file = data_dir / "bot_reading_progress.json"
        self.user_progress_file = data_dir / "user_reading_progress.json"
        self.target_session_file = data_dir / "target_session.json"
        self.memories_file = data_dir / "bot_chapter_memories.json"

        self._task: asyncio.Task | None = None
        self._running = False

        # load state
        self.progress: dict[str, Any] = self._load_progress()
        self.user_progress: dict[str, Any] = self._load_user_progress()
        self.target_session: str | None = self._load_target_session()
        self.memories: dict[str, Any] = self._load_memories()
        self.last_user_activity: float = 0  # timestamp of last user message

    async def _get_persona_prompt(self) -> str:
        """Get the persona prompt for LLM calls (same logic as chat_engine)."""
        special = self.config.get("special", {})
        override = special.get("persona_override", "").strip()
        if override:
            return override

        persona_id = special.get("persona", "").strip()
        if persona_id:
            try:
                persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
                if persona and persona.get("prompt"):
                    return persona["prompt"]
            except Exception:
                pass

        try:
            persona = await self.context.persona_manager.get_default_persona_v3()
            if persona and persona.get("prompt"):
                return persona["prompt"]
        except Exception:
            pass

        return ""

    def _load_progress(self) -> dict[str, Any]:
        """Load bot's reading progress from disk."""
        if self.progress_file.exists():
            try:
                return json.loads(self.progress_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_progress(self):
        """Persist bot's reading progress."""
        self.progress_file.write_text(
            json.dumps(self.progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_user_progress(self) -> dict[str, Any]:
        """Load user's reading progress from disk."""
        if self.user_progress_file.exists():
            try:
                return json.loads(self.user_progress_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_user_progress(self):
        """Persist user's reading progress."""
        self.user_progress_file.write_text(
            json.dumps(self.user_progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def report_user_progress(
        self,
        book_id: str,
        chapter_index: int,
        total_chapters: int,
        book_title: str = "",
    ):
        """Record user's reading progress (high-water-mark mode).

        Only advances if user opened a chapter beyond the current record.
        Progress never goes backwards.
        """
        current = self.user_progress.get(book_id, {})
        current_chapter = current.get("current_chapter", -1)
        # only update if user advanced (or first time)
        if chapter_index >= current_chapter:
            current["current_chapter"] = chapter_index
            current["total_chapters"] = total_chapters
            if book_title:
                current["book_title"] = book_title
            current["last_read_at"] = time.time()
            self.user_progress[book_id] = current
            self._save_user_progress()

    def checkin_chapter(
        self,
        book_id: str,
        chapter_index: int,
        total_chapters: int,
        book_title: str = "",
    ):
        """Record a chapter checkin (打卡). Purely for triggering summary + marking.

        Does NOT affect progress calculation. Progress is based on high-water-mark.
        Duplicate checkins for the same chapter are ignored.
        """
        current = self.user_progress.get(book_id, {})
        checked_set = set(current.get("checked_chapters_list", []))

        # ignore duplicate checkin for same chapter
        if chapter_index in checked_set:
            return

        # add this chapter to checked set
        checked_set.add(chapter_index)
        current["checked_chapters_list"] = sorted(checked_set)
        current["total_chapters"] = total_chapters
        if book_title:
            current["book_title"] = book_title
        current["last_checkin_at"] = time.time()
        self.user_progress[book_id] = current
        self._save_user_progress()

    def get_user_progress_percent(self, book_id: str) -> int:
        """Get user's reading progress as a percentage (high-water-mark)."""
        prog = self.user_progress.get(book_id)
        if not prog:
            return 0
        total = prog.get("total_chapters", 1)
        if total <= 0:
            return 0
        current = prog.get("current_chapter", 0)
        # progress = (current + 1) / total since user has at least opened this chapter
        return min(99, round(((current + 1) / total) * 100))

    def _load_target_session(self) -> str | None:
        """Load the target session ID for proactive messages."""
        if self.target_session_file.exists():
            try:
                data = json.loads(self.target_session_file.read_text(encoding="utf-8"))
                return data.get("session_id")
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _load_memories(self) -> dict[str, Any]:
        """Load bot chapter memories from disk."""
        if self.memories_file.exists():
            try:
                return json.loads(self.memories_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_memories(self):
        """Persist bot chapter memories."""
        self.memories_file.write_text(
            json.dumps(self.memories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_chapter_memory(self, book_id: str, chapter_index: int) -> str | None:
        """Get the bot's memory summary for a specific chapter."""
        book_memories = self.memories.get(book_id, {})
        return book_memories.get(str(chapter_index))

    def get_recent_memories(self, book_id: str, count: int = 5) -> list[str]:
        """Get the most recent chapter memory summaries for a book."""
        book_memories = self.memories.get(book_id, {})
        if not book_memories:
            return []
        # sort by chapter index (numeric) and take the last N
        sorted_items = sorted(book_memories.items(), key=lambda x: int(x[0]))
        return [v for _, v in sorted_items[-count:]]

    def _get_completed_chapters(self, book_id: str) -> list[int]:
        """Get list of completed chapter indices for a book (those with memories)."""
        book_memories = self.memories.get(book_id, {})
        return sorted(int(k) for k in book_memories.keys())

    def save_target_session(self, session_id: str):
        """Save the target session ID (called when user uses /乌鲁鲁星 command)."""
        self.target_session = session_id
        self.target_session_file.write_text(
            json.dumps(
                {"session_id": session_id, "saved_at": time.time()}, ensure_ascii=False
            ),
            encoding="utf-8",
        )

    def record_user_activity(self):
        """Record that the user just sent a message (called from on_llm_request hook)."""
        self.last_user_activity = time.time()

    def _is_user_recently_active(self, threshold_seconds: int = 300) -> bool:
        """Check if user sent a message within the last N seconds."""
        if self.last_user_activity == 0:
            return False
        bot_config = self.config.get("bot_reading", {})
        threshold = bot_config.get("no_interrupt_minutes", 5) * 60
        return (time.time() - self.last_user_activity) < threshold

    def get_bot_progress(self, book_id: str) -> dict[str, Any] | None:
        """Get bot's reading progress for a specific book."""
        return self.progress.get(book_id)

    def get_bot_progress_percent(self, book_id: str) -> int:
        """Get bot's reading progress as a percentage.

        Progress is strictly based on completed chapters (those with memories).
        No memory = no progress.
        """
        completed = self._get_completed_chapters(book_id)
        if not completed:
            return 0
        chapters = self.book_manager.get_chapters(book_id)
        total = len(chapters) if chapters else 0
        if total <= 0:
            return 0
        return min(99, round((len(completed) / total) * 100))

    async def generate_chapter_summary(self, book_id: str, chapter_index: int):
        """Generate a bot memory summary for a completed chapter.

        Combines chapter text, highlights, and chat history to produce
        a short summary from the bot's perspective.
        Skips if a memory already exists for this chapter (no duplicate summaries).
        """
        try:
            # skip if memory already exists for this chapter
            existing = self.get_chapter_memory(book_id, chapter_index)
            if existing:
                logger.debug(
                    f"乌鲁鲁星: 章节记忆已存在，跳过 book={book_id} ch={chapter_index}"
                )
                return

            # get chapter text
            chapter_text = self.book_manager.get_chapter_text(book_id, chapter_index)
            if not chapter_text:
                logger.warning(
                    f"乌鲁鲁星: 无法获取章节文本 book={book_id} ch={chapter_index}"
                )
                return

            # get book title and chapter title
            books = self.book_manager.list_books()
            book = next((b for b in books if b["id"] == book_id), None)
            book_title = book.get("title", "未知") if book else "未知"

            chapters = self.book_manager.get_chapters(book_id)
            chapter_title = ""
            if chapters and 0 <= chapter_index < len(chapters):
                chapter_title = chapters[chapter_index].get(
                    "title", f"第{chapter_index + 1}章"
                )

            # get highlights for this chapter
            all_highlights = self.book_manager.get_highlights(book_id)
            chapter_highlights = [
                h for h in all_highlights if h.get("chapter_index") == chapter_index
            ]
            highlights_text = ""
            if chapter_highlights:
                hl_lines = [f"「{h.get('text', '')}」" for h in chapter_highlights[:10]]
                highlights_text = "\n她的划线：\n" + "\n".join(hl_lines)

            # truncate chapter text for prompt
            snippet = chapter_text[:1500] if len(chapter_text) > 1500 else chapter_text

            # build LLM prompt
            prompt = (
                f"你刚和她一起读完了《{book_title}》的{chapter_title}。"
                f"以下是章节内容、她的划线和你们的互动。"
                f"请用150-200字，以你的视角总结这章的故事和你们的互动记忆。\n\n"
                f"[章节内容]\n{snippet}\n[/章节内容]"
                f"{highlights_text}"
            )

            # call LLM with persona
            provider = self.context.get_using_provider()
            if not provider:
                logger.warning("乌鲁鲁星: 无 LLM provider，跳过章节总结生成")
                return

            persona = await self._get_persona_prompt()
            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            summary = resp.completion_text
            if not summary:
                return

            # save memory
            if book_id not in self.memories:
                self.memories[book_id] = {}
            self.memories[book_id][str(chapter_index)] = summary
            self._save_memories()

            logger.info(
                f"乌鲁鲁星: 已生成章节记忆 book={book_id} ch={chapter_index} "
                f"len={len(summary)}"
            )

        except Exception as e:
            logger.error(f"乌鲁鲁星: 生成章节总结失败: {e}", exc_info=True)

    def start(self):
        """Start the background reading task."""
        bot_config = self.config.get("bot_reading", {})
        if not bot_config.get("enabled", True):
            logger.info("乌鲁鲁星: Bot 自主阅读已禁用")
            return
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._reading_loop())
        logger.info("乌鲁鲁星: 阅读任务已启动")

    async def stop(self):
        """Stop the background reading task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _reading_loop(self):
        """Background loop: periodically advance bot's reading progress."""
        # wait longer after startup before first read (10 minutes)
        await asyncio.sleep(600)

        while self._running:
            try:
                await self._do_reading_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"乌鲁鲁星: Bot 阅读循环出错: {e}", exc_info=True)

            # wait based on config
            bot_config = self.config.get("bot_reading", {})
            min_minutes = bot_config.get("reading_interval_min", 120)
            max_minutes = bot_config.get("reading_interval_max", 300)
            wait_minutes = random.randint(min_minutes, max_minutes)
            await asyncio.sleep(wait_minutes * 60)

    async def _do_reading_tick(self):
        """Advance bot's reading progress on one book and maybe send a message.

        Only advances progress if memory generation succeeds.
        """
        books = self.book_manager.list_books()
        if not books:
            return

        # pick a book to read (prefer ones with chapters not yet memorized)
        candidates = []
        for book in books:
            chapters = self.book_manager.get_chapters(book["id"])
            if not chapters:
                continue
            completed = self._get_completed_chapters(book["id"])
            if len(completed) < len(chapters):
                candidates.append(book)

        if not candidates:
            return

        book = random.choice(candidates)
        book_id = book["id"]

        # find the next chapter to read (first one without memory)
        chapters = self.book_manager.get_chapters(book_id)
        if not chapters:
            return

        completed = self._get_completed_chapters(book_id)
        next_chapter = None
        for i in range(len(chapters)):
            if i not in completed:
                next_chapter = i
                break

        if next_chapter is None:
            return

        # generate memory for this chapter (this is the actual "reading")
        await self.generate_chapter_summary(book_id, next_chapter)

        # verify memory was actually created before considering it progress
        if not self.get_chapter_memory(book_id, next_chapter):
            logger.warning(
                f"乌鲁鲁星: 自动阅读生成记忆失败 book={book_id} ch={next_chapter}"
            )
            return

        # update progress dict for compatibility
        self.progress[book_id] = {
            "current_chapter": next_chapter,
            "total_chapters": len(chapters),
            "book_title": book.get("title", ""),
            "last_read_at": time.time(),
            "current_offset": 0,
        }
        self._save_progress()

        logger.info(
            f"乌鲁鲁星: 自动阅读完成 《{book.get('title', '')}》第{next_chapter + 1}章"
        )

        # maybe send a proactive message based on configured probability
        bot_config = self.config.get("bot_reading", {})
        probability = bot_config.get("message_probability", 30) / 100.0
        if random.random() < probability:
            await self._send_reading_message(book_id)

    async def _send_reading_message(self, book_id: str):
        """Send a proactive message about what the bot just read."""
        if not self.target_session:
            logger.debug("乌鲁鲁星: 没有目标 session，跳过主动消息")
            return

        # check if user is currently active (chatting) — don't interrupt
        if self._is_user_recently_active():
            logger.debug("乌鲁鲁星: 用户最近在聊天，跳过主动消息避免打断")
            return

        prog = self.progress.get(book_id)
        if not prog:
            return

        chapter_idx = prog.get("current_chapter", 0)
        book_title = prog.get("book_title", "")

        # get chapter text for context
        chapter_text = self.book_manager.get_chapter_text(book_id, chapter_idx)
        if not chapter_text:
            return

        # truncate for LLM context
        chapter_snippet = chapter_text[:500]

        # generate a message using LLM
        try:
            provider = self.context.get_using_provider()
            if not provider:
                return

            prompt = (
                f"你刚刚读完了《{book_title}》的一个章节，以下是这章的部分内容：\n"
                f"「{chapter_snippet}」\n\n"
                f"请用你的风格（简短、随性、像发消息一样）跟她分享一下你读到的感受或者印象深刻的地方。"
                f"不要太长，一两句话就好，像是随手发的消息。不要提到'章节'、'内容'这种词。"
            )

            persona = await self._get_persona_prompt()
            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            message_text = resp.completion_text

            if not message_text:
                return

        except Exception as e:
            logger.error(f"乌鲁鲁星: 生成阅读消息失败: {e}")
            return

        # send the message (supports segmented sending via message_separator)
        try:
            separator = self.config.get("message_separator", "")
            segments = [message_text]

            # split by separator if configured
            if separator:
                try:
                    import re as _re

                    parts = _re.split(separator, message_text)
                    parts = [p.strip() for p in parts if p.strip()]
                    if parts:
                        segments = parts
                except Exception:
                    pass  # invalid regex, send as single message

            for i, seg in enumerate(segments):
                chain = MessageChain([Plain(text=seg)])
                await self.context.send_message(self.target_session, chain)
                # brief pause between segments to feel natural
                if i < len(segments) - 1:
                    await asyncio.sleep(random.uniform(1.0, 2.5))

            logger.info(
                f"乌鲁鲁星: 已发送阅读主动消息到 {self.target_session} "
                f"({len(segments)} 段)"
            )

            # save to conversation history (full text)
            await self._save_to_conversation_history(message_text)

        except Exception as e:
            logger.error(f"乌鲁鲁星: 发送阅读消息失败: {e}")

    async def _save_to_conversation_history(self, bot_message: str):
        """Save the proactive message to AstrBot's conversation history."""
        if not self.target_session:
            return

        try:
            conv_mgr = self.context.conversation_manager
            conv_id = await conv_mgr.get_curr_conversation_id(self.target_session)
            if not conv_id:
                return

            # add as a message pair (fake user message + bot response)
            # use a minimal user message to indicate this was proactive
            user_msg = {"role": "user", "content": "[主动分享]", "_no_save": True}
            assistant_msg = {"role": "assistant", "content": bot_message}

            await conv_mgr.add_message_pair(conv_id, user_msg, assistant_msg)

        except Exception as e:
            logger.debug(f"乌鲁鲁星: 保存对话历史失败: {e}")

    # ==================== Bot Dynamics (足迹动态) ====================

    def _get_profile_path(self) -> Path:
        """Get the path to profile.json for storing bot notes."""
        return self.data_dir / "profile.json"

    def _load_profile(self) -> dict:
        """Load profile data."""
        path = self._get_profile_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_profile(self, profile: dict):
        """Save profile data."""
        path = self._get_profile_path()
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def generate_bot_dynamic(self):
        """Generate a short bot dynamic/status note and save to footprints.

        This creates a casual, in-character note from the bot's perspective,
        like a status update or a thought he wants to share.
        """
        try:
            provider = self.context.get_using_provider()
            if not provider:
                return

            # build context for the dynamic
            context_parts = []

            # what time is it?
            from datetime import datetime

            now = datetime.now()
            hour = now.hour
            if hour < 6:
                time_context = "现在是深夜"
            elif hour < 9:
                time_context = "现在是早晨"
            elif hour < 12:
                time_context = "现在是上午"
            elif hour < 14:
                time_context = "现在是中午"
            elif hour < 18:
                time_context = "现在是下午"
            elif hour < 22:
                time_context = "现在是晚上"
            else:
                time_context = "现在是深夜"
            context_parts.append(time_context)

            # recent reading activity
            books = self.book_manager.list_books()
            if books and self.progress:
                recent_book = None
                for book in books:
                    prog = self.progress.get(book["id"])
                    if prog:
                        recent_book = prog.get("book_title", book.get("title", ""))
                        break
                if recent_book:
                    context_parts.append(f"最近在读《{recent_book}》")

            # recent memories
            recent_memory = ""
            for book_id, chapters in self.memories.items():
                if chapters:
                    sorted_chs = sorted(chapters.items(), key=lambda x: int(x[0]))
                    if sorted_chs:
                        recent_memory = sorted_chs[-1][1][:100]
                        break
            if recent_memory:
                context_parts.append(f"最近的阅读感受：{recent_memory}")

            context_str = "。".join(context_parts)

            prompt = (
                f"背景：{context_str}\n\n"
                "请用一两句话写一条随手记录的动态/碎碎念。"
                "风格要求：像是随手写在便签纸上的话，简短、自然、有生活气息。"
                "可以是：对刚读的书的一句感想、此刻的心情、想对她说的一句话、"
                "一个有趣的念头、或者日常的小事。"
                "不要太正式，不要用引号，不要超过40个字。"
                "直接输出内容，不要加任何前缀或解释。"
            )

            persona = await self._get_persona_prompt()
            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            note_text = (resp.completion_text or "").strip()

            if not note_text or len(note_text) > 100:
                return

            # save to profile footprints
            profile = self._load_profile()
            footprints = profile.get("footprints", [])

            bot_note = {
                "id": f"bot_{int(time.time())}_{random.randint(100, 999)}",
                "type": "bot_note",
                "content": note_text,
                "created_at": int(time.time()),
                "rotation": random.randint(-6, 6),
            }
            footprints.append(bot_note)
            profile["footprints"] = footprints
            self._save_profile(profile)

            logger.info(f"乌鲁鲁星: Bot 动态已生成: {note_text[:30]}...")

        except Exception as e:
            logger.error(f"乌鲁鲁星: 生成 Bot 动态失败: {e}", exc_info=True)

    def start_dynamics(self):
        """Start the bot dynamics background task."""
        bot_config = self.config.get("bot_reading", {})
        if not bot_config.get("enabled", True):
            return
        self._dynamics_task = asyncio.create_task(self._dynamics_loop())
        logger.info("乌鲁鲁星: Bot 动态任务已启动")

    async def _dynamics_loop(self):
        """Background loop: periodically generate bot dynamics."""
        # wait 30 minutes after startup before first dynamic
        await asyncio.sleep(1800)

        while self._running:
            try:
                await self.generate_bot_dynamic()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"乌鲁鲁星: Bot 动态循环出错: {e}", exc_info=True)

            # wait between dynamics (configurable, default 4-8 hours)
            bot_config = self.config.get("bot_reading", {})
            interval_min = bot_config.get("dynamics_interval_min", 4)
            interval_max = bot_config.get("dynamics_interval_max", 8)
            if interval_min > interval_max:
                interval_min, interval_max = interval_max, interval_min
            wait_hours = random.uniform(interval_min, interval_max)
            await asyncio.sleep(wait_hours * 3600)
