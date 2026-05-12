"""
Chat Engine - handles bot responses to reading interactions.

The chat context only lives within a session's lifecycle.
When the session ends, the context is archived and won't be sent to the LLM again.
This keeps conversations fresh and contextual to the current reading moment.

Persona resolution:
  1. If persona_override is set in plugin config, use it
  2. Otherwise, use AstrBot's configured default persona
"""

from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context

from .session_manager import SessionManager
from .book_manager import BookManager


class ChatEngine:
    def __init__(
        self,
        context: Context,
        config: dict[str, Any],
        session_manager: SessionManager,
        book_manager: BookManager | None = None,
    ):
        self.context = context
        self.config = config
        self.session_manager = session_manager
        self.book_manager = book_manager

    async def _get_persona_prompt(self) -> str:
        """Get the persona prompt. Uses plugin override if set, otherwise AstrBot's default."""
        # check manual override first
        special = self.config.get("special", {})
        override = special.get("persona_override", "").strip()
        if override:
            return override

        # check if a specific persona is selected via _special: select_persona
        persona_id = special.get("persona", "").strip()
        if persona_id:
            try:
                persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
                if persona and persona.get("prompt"):
                    return persona["prompt"]
            except Exception as e:
                logger.warning(f"Failed to get selected persona '{persona_id}': {e}")

        # fallback to AstrBot's default persona
        try:
            persona = await self.context.persona_manager.get_default_persona_v3()
            if persona and persona.get("prompt"):
                return persona["prompt"]
        except Exception as e:
            logger.warning(f"Failed to get AstrBot default persona: {e}")

        return "You are a helpful and friendly assistant."

    async def _build_system_prompt(
        self,
        book_title: str = "",
        chapter_title: str = "",
        chapter_text: str = "",
    ) -> str:
        """Build the full system prompt for the reading companion."""
        persona_prompt = await self._get_persona_prompt()

        context_info = ""
        if book_title:
            context_info += f"\n当前正在一起读的书：《{book_title}》"
        if chapter_title:
            context_info += f"\n当前章节：{chapter_title}"

        # inject current chapter content (truncated if too long)
        chapter_injection = ""
        if chapter_text:
            max_len = 5000
            if len(chapter_text) > max_len:
                chapter_text = chapter_text[:max_len] + "\n...(章节内容过长，已截断)"
            chapter_injection = f"\n\n[当前章节内容]\n{chapter_text}\n[/当前章节内容]"

        # append reading-specific instructions
        reading_instructions = """

你现在在乌鲁鲁星里陪伴用户阅读。请注意：
- 回复要简短温暖，像朋友间的即时消息，不要写长篇大论
- 你可以看到用户当前正在阅读的章节内容，可以随时讨论情节、角色、感受
- 如果对方划了一段话，聊聊这段话为什么打动你，或者联想到什么
- 如果对方写了书评，真诚地回应，可以补充自己的想法
- 如果对方留了纸条，像收到朋友便签一样回应
- 如果对方问起前面章节的内容，你可以调用 read_bookhouse_chapter 工具获取
- 保持对话的连贯性，记住这次阅读中聊过的内容"""

        return f"{persona_prompt}{context_info}{reading_instructions}{chapter_injection}"

    async def _call_llm(self, book_id: str, user_message: str, system_prompt: str) -> str:
        """Call the LLM with session context and return the response."""
        # record user message
        self.session_manager.add_chat_message(book_id, "user", user_message)

        # build context from session history (lifecycle-scoped)
        history = self.session_manager.get_context_for_llm(book_id)

        try:
            # get the provider (use configured one or default)
            special = self.config.get("special", {})
            provider_id = special.get("provider", "").strip()

            if provider_id:
                provider = self.context.get_provider_by_id(provider_id)
            else:
                provider = self.context.get_using_provider()

            if not provider:
                logger.warning("No LLM provider available for uluru_star chat")
                return "（我现在有点走神了，等会儿再聊~）"

            # call LLM
            resp = await provider.text_chat(
                prompt=user_message,
                contexts=history[:-1],  # exclude the last one since it's the prompt
                system_prompt=system_prompt,
            )

            reply_text = resp.completion_text or "（嗯...让我想想）"

        except Exception as e:
            logger.error(f"LLM call failed in uluru_star: {e}")
            reply_text = "（抱歉，我刚才走神了，你再说一遍？）"

        # record bot reply
        self.session_manager.add_chat_message(book_id, "bot", reply_text)

        return reply_text

    async def respond_to_highlight(
        self,
        book_id: str,
        _book_id_dup: str = "",
        chapter_index: int | None = None,
        highlighted_text: str = "",
        context_text: str = "",
    ) -> str:
        """Generate a response when user highlights text."""
        if context_text:
            user_msg = f"[划线] 我在读到这段的时候停下来了：\n「{highlighted_text}」\n（上下文：{context_text[:200]}）"
        else:
            user_msg = f"[划线] 我划了这句：\n「{highlighted_text}」"

        system_prompt = await self._build_system_prompt()
        return await self._call_llm(book_id, user_msg, system_prompt)

    async def respond_to_review(
        self,
        book_id: str,
        _book_id_dup: str = "",
        chapter_index: int | None = None,
        review_content: str = "",
    ) -> str:
        """Generate a response when user writes a review."""
        user_msg = f"[书评] 我写了一段感想：\n{review_content}"

        system_prompt = await self._build_system_prompt()
        return await self._call_llm(book_id, user_msg, system_prompt)

    async def respond_to_note(
        self,
        book_id: str,
        _book_id_dup: str = "",
        chapter_index: int | None = None,
        note_content: str = "",
    ) -> str:
        """Generate a response when user leaves a note."""
        user_msg = f"[纸条] 我给你留了张纸条：\n{note_content}"

        system_prompt = await self._build_system_prompt()
        return await self._call_llm(book_id, user_msg, system_prompt)

    async def chat(
        self,
        book_id: str,
        content: str,
        chapter_index: int | None = None,
    ) -> str:
        """Handle a free-form chat message from the user.

        If chapter_index is provided and book_manager is available,
        injects the current chapter text into the system prompt.
        """
        # resolve book title and chapter info
        book_title = ""
        chapter_title = ""
        chapter_text = ""

        if self.book_manager:
            books = self.book_manager.list_books()
            book = next((b for b in books if b["id"] == book_id), None)
            if book:
                book_title = book.get("title", "")

            if chapter_index is not None:
                chapters = self.book_manager.get_chapters(book_id)
                if chapters and 0 <= chapter_index < len(chapters):
                    chapter_title = chapters[chapter_index].get("title", "")
                    # fetch full chapter text for LLM context
                    chapter_text = self.book_manager.get_chapter_text(book_id, chapter_index) or ""

        system_prompt = await self._build_system_prompt(
            book_title=book_title,
            chapter_title=chapter_title,
            chapter_text=chapter_text,
        )
        return await self._call_llm(book_id, content, system_prompt)
