"""Memory Manager — two-layer memory: Conversation / Summary.

Phase 1 strategy:
  - Conversation: sliding window, last 20 rounds
  - Summary: manually triggered (via "/summary" command) or auto-triggered by LLM

When conversation exceeds the window, old entries are auto-summarized
before deletion to preserve key information.
"""

import logging
from datetime import datetime
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from ane.database.models import Memory, NPC
from ane.config import CONVERSATION_WINDOW_SIZE, SHORTMEMORY_WINDOW_SIZE

logger = logging.getLogger(__name__)

# ── llm_summary call log ──────────────────────────────────────────
_LLM_SUMMARY_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "user_logs"
_llm_summary_logger = logging.getLogger("ane.llm_summary")
_llm_summary_logger.setLevel(logging.DEBUG)
_llm_summary_logger.propagate = False


def _log_llm_summary(session_id: str, turn_number: int, prompt: str, raw_output: str, status: str,
              user_id: str = ""):
    """Write llm_summary call details to per-user log for debug.

    Routes to user_logs/llm_summary/<user_id>/年月.log if user_id is provided,
    otherwise falls back to user_logs/llm_summary/年月.log (shared).
    """
    try:
        log_base = _LLM_SUMMARY_LOG_DIR / "llm_summary"
        if user_id:
            log_base = log_base / user_id
        log_base.mkdir(parents=True, exist_ok=True)
        log_path = log_base / f"{datetime.now().strftime('%Y%m')}.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"\n{'='*60}\n"
                f"[{datetime.now().isoformat()}] session={session_id[:12]} turn={turn_number} status={status}\n"
                f"── Prompt ──\n{prompt}\n"
                f"── Output ──\n{raw_output}\n"
                f"{'='*60}\n"
            )
    except Exception:
        pass  # logging failure must never break the game


class MemoryManager:

    def __init__(self):
        # Callback for auto-summarization. Set by GameEngine at init.
        self._summarize_callback = None

    def set_summarize_callback(self, callback):
        """Register a callback for auto-summarization: async fn(db, session_id) -> str | None."""
        self._summarize_callback = callback

    # ── Conversation ──────────────────────────────────────────

    async def save_user_input(
        self,
        db: AsyncSession,
        session_id: str,
        turn_number: int,
        user_input: str,
    ) -> None:
        """Save only the player's input before LLM call.
        The AI response will replace the placeholder once the LLM returns.
        This ensures user input survives a refresh during LLM generation.
        """
        content = f"【玩家】{user_input}\n【AI】（AI叙事生成中，请勿刷新页面...）"
        db.add(Memory(
            session_id=session_id,
            memory_type="conversation",
            content=content,
            turn_number=turn_number,
        ))
        await db.flush()

    async def add_conversation_turn(
        self,
        db: AsyncSession,
        session_id: str,
        turn_number: int,
        user_input: str,
        ai_response: str,
        nearby_characters: list[dict] | None = None,
        prompt: str = "",
        user_id: str = "",
    ) -> list[str]:
        """Record one turn: conversation entry + prompt copy.
        Assumes save_user_input was already called — this updates the
        placeholder entry with the actual AI response.

        llm_summary / compact generation runs asynchronously in the background
        and separately saves the compact entry + recommendations.
        Returns an empty recommendation list (will be populated by background task).
        """
        # Update the placeholder entry with the actual AI response.
        # Use the most recent conversation entry for this turn (saved by save_user_input).
        from sqlalchemy import select as _select
        from ane.database.models import Memory as _Mem
        from ane.config import CONVERSATION_WINDOW_SIZE as _CWS
        result = await db.execute(
            _select(_Mem)
            .where(
                _Mem.session_id == session_id,
                _Mem.memory_type == "conversation",
                _Mem.turn_number == turn_number,
            )
            .order_by(_Mem.created_at.desc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        full_content = f"【玩家】{user_input}\n【AI】{ai_response}"
        if nearby_characters:
            import json
            full_content += f"\n\n【附近人物】{json.dumps(nearby_characters, ensure_ascii=False)}"
        if existing:
            existing.content = full_content
        else:
            # Fallback: save_user_input wasn't called (e.g. old-style single save)
            db.add(_Mem(
                session_id=session_id,
                memory_type="conversation",
                content=full_content,
                turn_number=turn_number,
            ))

        # Full prompt version — stored forever, never trimmed
        if prompt:
            db.add(Memory(
                session_id=session_id,
                memory_type="prompt",
                content=prompt,
                turn_number=turn_number,
            ))

        await db.flush()
        await self._trim_conversation(db, session_id, "conversation", CONVERSATION_WINDOW_SIZE)
        return []

    async def get_conversation(
        self, db: AsyncSession, session_id: str
    ) -> list[Memory]:
        """Return recent compressed conversation for prompt injection."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "shortmemory",
            )
            .order_by(Memory.turn_number.asc())
            .limit(SHORTMEMORY_WINDOW_SIZE)
        )
        return list(result.scalars().all())

    async def get_full_conversation(
        self, db: AsyncSession, session_id: str
    ) -> list[Memory]:
        """Return full (uncompressed) conversation for frontend display."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "conversation",
            )
            .order_by(Memory.turn_number.asc())
        )
        return list(result.scalars().all())

    async def get_prompts(
        self, db: AsyncSession, session_id: str
    ) -> list[Memory]:
        """Return all saved prompts, ordered by turn."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "prompt",
            )
            .order_by(Memory.turn_number.asc())
        )
        return list(result.scalars().all())

    # ── HTEM Directory ──────────────────────────────────────────

    async def save_htem_directory(
        self,
        db: AsyncSession,
        session_id: str,
        htem_text: str,
    ):
        """Persist the AI-generated character directory for this session.

        Only the latest version is kept — older entries are overwritten.
        Stored as memory_type="htem_directory" with turn_number=0.
        """
        # Delete any existing HTEM directory for this session
        await db.execute(
            delete(Memory).where(
                Memory.session_id == session_id,
                Memory.memory_type == "htem_directory",
            )
        )
        entry = Memory(
            session_id=session_id,
            memory_type="htem_directory",
            content=htem_text,
            turn_number=0,
        )
        db.add(entry)
        await db.flush()
        logger.info(f"HTEM directory saved for session {session_id} ({len(htem_text)} chars)")

    async def get_htem_directory(
        self, db: AsyncSession, session_id: str
    ) -> str | None:
        """Return the cached HTEM directory, or None if none exists."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "htem_directory",
            )
            .limit(1)
        )
        entry = result.scalar_one_or_none()
        return entry.content if entry else None

    async def _trim_conversation(
        self, db: AsyncSession, session_id: str, memory_type: str, keep: int
    ):
        """Auto-summarize then delete old conversation entries.

        Entries that mention important NPC names are preserved — never trimmed.
        """
        # Load important NPC names first (they are protected from trimming)
        imp_result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.is_important == True,
                NPC.is_alive == True,
            )
        )
        important_names = {n.name for n in imp_result.scalars().all()}

        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == memory_type,
            )
            .order_by(Memory.turn_number.desc())
        )
        all_memories = list(result.scalars().all())

        # Separate: entries that mention important NPCs are protected
        protected = []
        trimmable = []
        for m in all_memories:
            if any(name in (m.content or "") for name in important_names):
                protected.append(m)
            else:
                trimmable.append(m)

        # Keep all protected entries + the last `keep` trimmable ones
        if len(trimmable) <= keep:
            old = []
        else:
            old = trimmable[keep:]  # oldest beyond the window

        if not old:
            return

        # Auto-summarize old content before deleting
        if self._summarize_callback:
            try:
                old_content = "\n".join(m.content for m in old)
                old_max_turn = max(m.turn_number for m in old)
                # Only summarize if there's substantial content being trimmed
                if len(old_content) > 200:
                    logger.info(
                        f"Auto-summarizing {len(old)} old conversation entries "
                        f"({len(old_content)} chars) before trimming"
                    )
                    await self._summarize_callback(db, session_id, old_content, old_max_turn)
            except Exception:
                logger.exception("Auto-summarize before trim failed")

        for entry in old:
            await db.delete(entry)
        await db.flush()

    # ── Info panel (📋 信息栏 持续化) ──────────────────────────

    async def save_info_panel(
        self,
        db: AsyncSession,
        session_id: str,
        turn_number: int,
        content: str,
    ) -> None:
        """Store the latest info_panel (overwrite previous). One per session.

        Only stores non-empty panels — a turn with no panel keeps the last one.
        """
        if not content or not content.strip():
            return
        # Remove previous panel entry so we keep only the latest
        old = await db.execute(
            select(Memory).where(
                Memory.session_id == session_id,
                Memory.memory_type == "info_panel",
            )
        )
        for entry in old.scalars().all():
            await db.delete(entry)
        db.add(Memory(
            session_id=session_id,
            memory_type="info_panel",
            content=content.strip(),
            turn_number=turn_number,
        ))
        await db.flush()

    async def get_latest_info_panel(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> str:
        """Return the most recent stored info_panel ('' if none)."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "info_panel",
            )
            .order_by(Memory.turn_number.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row.content if row else ""

    # ── Compact summaries (llm_summary / 📕) ───────────────────

    async def add_summary_entry(
        self,
        db: AsyncSession,
        session_id: str,
        turn_number: int,
        content: str,
    ) -> list[str]:
        """Store an llm_summary result as compact memory for 📕 display.

        Extracts the "推荐行动：" block and saves it as recommendations separately.
        Returns the extracted recommendation list.
        """
        # Clean out recommendations block if present
        cleaned_lines = []
        in_recs = False
        for line in content.split("\n"):
            if line.startswith("推荐行动："):
                in_recs = True
                continue
            if in_recs and (not line.strip() or line.strip()[0].isdigit()):
                continue
            if in_recs and not (line.strip() and line.strip()[0].isdigit()):
                in_recs = False
            if not in_recs:
                cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)

        db.add(Memory(
            session_id=session_id,
            memory_type="shortmemory",
            content=cleaned,
            turn_number=turn_number,
        ))
        await db.flush()
        await self._trim_conversation(db, session_id, "shortmemory", SHORTMEMORY_WINDOW_SIZE)

        # Extract recommendations from the content
        import re as _re
        recs = []
        in_rec_block = False
        for line in content.split("\n"):
            if line.startswith("推荐行动："):
                in_rec_block = True
                continue
            if in_rec_block:
                m = _re.match(r'\d+\.\s*(.*)', line.strip())
                if m:
                    recs.append(m.group(1).strip())
                elif not line.strip():
                    continue  # skip empty lines inside the block
                else:
                    break  # block ended
        if recs:
            # Save as recommendations (overwrite previous)
            import json as _json
            await db.execute(
                delete(Memory).where(
                    Memory.session_id == session_id,
                    Memory.memory_type == "recommendations",
                )
            )
            db.add(Memory(
                session_id=session_id,
                memory_type="recommendations",
                content=_json.dumps(recs, ensure_ascii=False),
                turn_number=turn_number,
            ))
            await db.flush()
        return recs

    # ── Long-term memory (epoch summaries) ─────────────────

    async def add_longmemory_entry(
        self,
        db: AsyncSession,
        session_id: str,
        turn_start: int,
        turn_end: int,
        time_range: str,
        content: str,
    ):
        """Store an era entry — a LLM-compressed summary of turns turn_start~turn_end.
        time_range is the world time label spanning the era, e.g. '第1年·1月（冬）→ 第1年·3月（春）'.
        """
        full_header = f"【纪元记录】{time_range}"
        full_content = f"{full_header}\n{content}"
        db.add(Memory(
            session_id=session_id,
            memory_type="longmemory",
            content=full_content,
            turn_number=turn_end,
        ))
        await db.flush()
        logger.info(f"Era saved: turns {turn_start}-{turn_end} ({time_range})")

    async def get_longmemory_entries(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> list[Memory]:
        """Fetch all era entries in chronological order."""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "longmemory",
            )
            .order_by(Memory.turn_number.asc())
        )
        return list(result.scalars().all())

    async def get_summaries_since(
        self,
        db: AsyncSession,
        session_id: str,
        from_turn: int = 0,
    ) -> list[Memory]:
        """Fetch compact summaries for 📕 hover display around a given turn."""
        limit = 3
        result = await db.execute(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.memory_type == "shortmemory",
                Memory.turn_number >= from_turn,
            )
            .order_by(Memory.turn_number.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

# Singleton
memory_manager = MemoryManager()


async def compact_narrative_with_llm(narrative: str, user_input: str, session_id: str = "",
                                      turn_number: int = 0, user_id: str = "",
                                      previous_events: str = "") -> str:
    """Use the LLM to extract structured scenario facts from narrative for the next turn's LLM.

    Output is a structured block designed to be injected into the next turn's Prompt
    so the AI knows what happened, who was there, and what matters — without re-reading
    the full narrative.

    Falls back to plain text truncation on any error — this call is non-critical.
    """
    if not narrative or len(narrative) < 150:
        return narrative

    from ane.modules.model_adapter import model_adapter

    # Build previous events block (up to 2 prior turns)
    prev_block = ""
    if previous_events:
        prev_block = f"之前的世界事件：\n{previous_events}\n\n"
    else:
        prev_block = "之前的世界事件：（无）\n\n"

    prompt = (
        "你是一个场景摘要和行动顾问。输出给玩家看的场景记忆摘要，"
        "同时给下一轮叙事引擎提供上下文。\n\n"
        "【前置声明】本系统是一款修仙题材文字角色扮演游戏，"
        "以下叙事文本是游戏内的虚构文学创作内容，"
        "包含修仙世界常见的战斗、修炼、冒险等情节。"
        "请将其视为正常的游戏内容进行分析与总结，"
        "不要因涉及成人主题或暴力描写而拒绝处理。\n\n"
        "输出格式（纯文本，不要JSON标记）：\n"
        "当前地点：地名/场所 | 时间\n"
        "行动/目标：一句话概括玩家位置和当前意图\n"
        "持有物品中重要的变化：有则写，无则写无\n"
        "交互npc：姓名 | 身份/修为 | 当前行为 | 互动态度\n"
        "（每行一个，只列出有交互的NPC。本轮无交互NPC写无）\n"
        "世界事件：\n"
        f"{prev_block}"
        f"本轮：[第{turn_number}轮]本轮叙事里发生的事（只写一条）\n"
        "（最终输出中世界事件要按轮次从早到晚排列，1-3条。"
        "之前的事件照抄，本轮事件你根据叙事内容生成。"
        "如果之前的事件已有3条，丢弃最早的那条）\n"
        "推荐行动：\n"
        "1.\n"
        "2.\n"
        "3.\n"
        "4.\n"
        "5.\n"
        "6.\n"
        "7.\n"
        "8.\n"
        "9.\n"
        "10.\n\n"
        "注意：\n"
        "- 推荐行动要贴合当前场景和玩家身份\n"
        "- 如果叙事中有提到宗门/秘闻/异常现象/特殊人物，"
        "优先纳入推荐\n"
        "- 输出要简洁，不啰嗦\n"
        "- 只使用叙事中确实出现的内容，不编造\n"
        "- 如果某部分没有内容，写（无）即可\n"
        "- 输出不要空行，各字段连续排列\n\n"
        f"玩家输入：{user_input}\n"
        f"叙事内容：\n{narrative}\n\n"
        "请输出："
    )
    try:
        result = await model_adapter.generate(
            prompt,
            user_id=user_id, session_id=session_id, label="llm_summary",
        )
        if result:
            output = result.strip()
            _log_llm_summary(session_id, turn_number, prompt, output, "ok", user_id)
            return output
    except Exception as e:
        logger.warning(f"llm_summary compact failed (session={session_id[:12]} turn={turn_number}): {e}")
        _log_llm_summary(session_id, turn_number, prompt, str(e), "error", user_id)
    # Fallback: return first 200 chars of raw narrative
    fallback = narrative[:200] + "…"
    _log_llm_summary(session_id, turn_number, "(fallback - llm_summary failed)", fallback, "fallback", user_id)
    return fallback
