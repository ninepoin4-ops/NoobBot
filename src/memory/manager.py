"""
深度记忆系统 — 4层架构，设计源自 ChatLuna 的 memory pipeline

层级：
  1. BufferMemory  — 当前会话的短期上下文（纯内存）
  2. VectorMemory  — 跨会话的长期记忆（chroma 向量检索，持久化到磁盘）
  3. LoreBook      — 关键词触发的世界书/设定注入
  4. Compressor    — 超长上下文 LLM 压缩
"""
from __future__ import annotations
import json
import time
import hashlib
import re
from collections import defaultdict
from typing import Optional
from loguru import logger

from src.models.schemas import ConversationTurn, MemoryEntry


def limit_utf8_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断，避免切坏中文字符。"""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    raw = text.encode("utf-8")[:max_bytes]
    return raw.decode("utf-8", errors="ignore").rstrip()


def normalize_memory_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# CQ 码清洗：把 raw_message 里的协议标记转成 LLM 易理解的纯文本，
# 避免一堆 [CQ:at,qq=12345] 进了短期记忆后又被原样拼回 messages，
# 干扰 LLM 理解。@someone 转成 @12345，其余 CQ 码转成中文占位。
def strip_cq_for_memory(text: str) -> str:
    if "[CQ:" not in text:
        return text
    text = re.sub(r"\[CQ:at,qq=(\d+)[^\]]*\]", r"@\1", text)
    text = re.sub(r"\[CQ:reply,[^\]]*\]", "", text)
    text = re.sub(r"\[CQ:image,[^\]]*\]", "[图片]", text)
    text = re.sub(r"\[CQ:face,[^\]]*\]", "[表情]", text)
    text = re.sub(r"\[CQ:[^\]]*\]", "", text)
    return text.strip()


# ════════════════════════════════════════════
# 第1层: BufferMemory — 短期上下文
# ════════════════════════════════════════════

class BufferMemory:
    """
    会话内部短期记忆。
    每个 group_id+user_id 维护一个对话轮次列表。
    """

    def __init__(self, config: dict):
        mc = config["memory"]["short_term"]
        self.max_turns = mc["max_turns"]
        self.threshold = mc["compression_threshold"]

        # key: f"{group_id}:{user_id}" -> list[ConversationTurn]
        self._sessions: dict[str, list[ConversationTurn]] = defaultdict(list)
        # 群聊上下文（不区分用户，多轮混排）
        self._group_contexts: dict[int, list[ConversationTurn]] = defaultdict(list)

    def add_turn(self, group_id: int, user_id: int, role: str, content: str):
        """添加一轮对话"""
        turn = ConversationTurn(
            role=role, content=content, time=time.time()
        )
        key = f"{group_id}:{user_id}"
        self._sessions[key].append(turn)
        self._group_contexts[group_id].append(turn)

        # 超出长度时截断（丢弃最旧的）
        if len(self._sessions[key]) > self.max_turns:
            self._sessions[key] = self._sessions[key][-self.max_turns:]
        if len(self._group_contexts[group_id]) > self.max_turns * 3:
            self._group_contexts[group_id] = \
                self._group_contexts[group_id][-self.max_turns * 3:]

    def get_user_context(self, group_id: int, user_id: int,
                         max_turns: int = 10) -> list[ConversationTurn]:
        """获取某用户的最近对话历史"""
        key = f"{group_id}:{user_id}"
        return self._sessions.get(key, [])[-max_turns:]

    def get_group_context(self, group_id: int,
                          max_turns: int = 20) -> list[ConversationTurn]:
        """获取群聊最近上下文（所有用户混排）"""
        return self._group_contexts.get(group_id, [])[-max_turns:]

    def get_user_history(self, user_id: int,
                         max_turns: int = 20) -> list[ConversationTurn]:
        """跨群获取某用户的所有历史（便于个性化）"""
        turns = []
        for key, t in self._sessions.items():
            if key.endswith(f":{user_id}"):
                turns.extend(t[-max_turns:])
        turns.sort(key=lambda x: x.time, reverse=True)
        return turns[:max_turns]


# ════════════════════════════════════════════
# 第2层: VectorMemory — 向量长期记忆
# ════════════════════════════════════════════

class VectorMemory:
    """
    基于 chromadb 的长期记忆。
    自动将对话摘要向量化存储，按语义检索。
    """

    def __init__(self, config: dict):
        mc = config["memory"]["long_term"]
        self._max_entry_bytes = mc.get("max_entry_bytes", 2400)
        if not mc["enabled"]:
            self._enabled = False
            return

        self._enabled = True
        self._min_save_len = mc["min_save_length"]
        self._retrieval_k = mc["retrieval_k"]
        self._collection_name = mc["collection_name"]

        # 延迟导入（chroma 不是所有人都要装）
        import chromadb
        self._client = chromadb.PersistentClient(
            path="data/memories/chroma"
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"向量记忆已初始化: {self._collection_name}")

    async def save(self, content: str, group_id: int, user_id: int,
                   metadata: dict | None = None):
        """保存一条长期记忆"""
        content = limit_utf8_bytes(normalize_memory_text(content), self._max_entry_bytes)
        if not self._enabled or len(content) < self._min_save_len:
            return

        doc_id = hashlib.md5(
            f"{content}:{group_id}:{user_id}:{time.time()}".encode()
        ).hexdigest()

        meta = {
            "group_id": str(group_id),
            "user_id": str(user_id),
            "timestamp": str(time.time()),
            "bytes": str(len(content.encode("utf-8"))),
        }
        if metadata:
            meta.update({k: str(v) for k, v in metadata.items()})

        self._collection.add(
            documents=[content],
            metadatas=[meta],
            ids=[doc_id],
        )

    async def search(self, query: str, group_id: int | None = None,
                     k: int | None = None) -> list[MemoryEntry]:
        """语义检索长期记忆"""
        if not self._enabled:
            return []

        k = k or self._retrieval_k
        where = {"group_id": str(group_id)} if group_id else None

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(k, 50),
                where=where,
            )
        except Exception as e:
            logger.warning(f"向量检索失败: {e}")
            return []

        entries = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            meta = results.get("metadatas", [[]])[0][i] if results.get("metadatas") else {}
            entry = MemoryEntry(
                id=results.get("ids", [[]])[0][i] if results.get("ids") else "",
                content=doc,
                timestamp=float(meta.get("timestamp", 0)),
                group_id=int(meta.get("group_id", 0)),
                user_id=int(meta.get("user_id", 0)),
                metadata=meta,
            )
            entries.append(entry)

        return entries


# ════════════════════════════════════════════
# 第3层: LoreBook — 世界书/关键词设定
# ════════════════════════════════════════════

class LoreBook:
    """
    世界书（Lore Book）。
    关键词驱动的内容注入 — 当消息包含特定关键词时，注入相关设定。
    设计来源 ChatLuna 的 LoreBookMatcher。
    """

    def __init__(self):
        # name -> {"keywords": [...], "content": "...", "constant": bool}
        self._entries: dict[str, dict] = {}

    def add_entry(self, name: str, keywords: list[str],
                  content: str, constant: bool = False):
        """添加一个世界书条目"""
        self._entries[name] = {
            "keywords": [kw.lower() for kw in keywords],
            "content": content,
            "constant": constant,
        }

    def match(self, text: str) -> list[str]:
        """匹配消息中的关键词，返回命中的世界书内容"""
        text_lower = text.lower()
        hits = []
        for entry in self._entries.values():
            if entry["constant"]:
                hits.append(entry["content"])
            elif any(kw in text_lower for kw in entry["keywords"]):
                hits.append(entry["content"])
        return hits

    def get_all_constant(self) -> list[str]:
        return [
            e["content"] for e in self._entries.values() if e["constant"]
        ]


# ════════════════════════════════════════════
# 第4层: Compressor — 上下文压缩
# ════════════════════════════════════════════

class ContextCompressor:
    """
    超长上下文压缩。
    当 token 数接近模型上限时，对早期对话做 LLM 摘要压缩。
    设计来源 ChatLuna 的 infinite_context.ts → compressIfNeeded()。
    """

    def __init__(self, llm_client):
        self._llm = llm_client

    async def compress(self, turns: list[ConversationTurn],
                       max_turns: int = 30,
                       force: bool = False) -> list[ConversationTurn]:
        """压缩超长上下文"""
        if len(turns) <= max_turns and not force:
            return turns
        if len(turns) <= 1:
            return turns

        # 保留最后几轮完整对话
        keep = min(len(turns) - 1, max(3, max_turns // 3))
        recent = turns[-keep:]
        to_compress = turns[:-keep]

        # 构造需要压缩的文本
        compressed_text = "\n".join(
            f"[{t.role}]: {t.content}" for t in to_compress
        )

        # 用 LLM 压缩
        summary = await self._llm.summarize(
            f"请简要总结以下对话的核心信息和关键事件（保留人名、群名、重要事实）：\n\n{compressed_text}"
        )

        return [
            ConversationTurn(role="system", content=f"[历史摘要]: {summary}"),
            *recent,
        ]


# ════════════════════════════════════════════
# 总管理器
# ════════════════════════════════════════════

class MemoryManager:
    """统一记忆管理器，协调4层记忆"""

    def __init__(self, config: dict, llm_client=None):
        self._llm = llm_client
        # max_entry_bytes 由 VectorMemory 单一持有，避免 manager 与 vector 各存一份
        # 导致热更新时只改一份的不一致问题
        self.buffer = BufferMemory(config)
        self.vector = VectorMemory(config)
        self.lorebook = LoreBook()
        self.compressor = ContextCompressor(llm_client) if llm_client else None

    async def on_message(self, group_id: int, user_id: int,
                         message: str, is_bot: bool = False):
        """消息经过时自动存入短期记忆。

        入记忆前清洗 CQ 码：@提及转成 @QQ 号、图片/表情转成占位符，
        避免协议层标记被原样拼回 LLM messages 干扰理解。
        （决策链 activator 仍用原始 raw_message 判定 @ / quote，互不影响）
        """
        role = "assistant" if is_bot else "user"
        clean = strip_cq_for_memory(message)
        self.buffer.add_turn(group_id, user_id, role, clean)

    async def on_interaction(self, group_id: int, user_id: int,
                             user_msg: str, bot_reply: str):
        """一次完整交互后，存入长期记忆。

        注意：用户消息已在 on_message() 阶段入短期记忆，这里只补 Bot 的回复，
        并把整次互动写入向量长期记忆。
        """
        self.buffer.add_turn(group_id, user_id, "assistant", bot_reply)
        memory_text = await self._build_concise_memory(user_msg, bot_reply)
        await self.vector.save(
            memory_text,
            group_id, user_id,
        )

    async def _build_concise_memory(self, user_msg: str, bot_reply: str) -> str:
        """生成尽量短的长期记忆，并强制不超过 max_entry_bytes。"""
        user_msg = normalize_memory_text(user_msg)
        bot_reply = normalize_memory_text(bot_reply)
        raw = f"用户: {user_msg}\n回答: {bot_reply}"

        max_entry_bytes = getattr(self.vector, "_max_entry_bytes", 2400)
        # 小记录直接保存；较长记录先让 LLM 提炼事实，再做字节上限兜底。
        if (
            self._llm
            and len(raw.encode("utf-8")) > max(400, max_entry_bytes // 2)
        ):
            prompt = (
                "把下面群聊互动压缩成一条长期记忆，只保留对后续对话有用的事实、偏好、约定和待办。"
                "不要寒暄，不要复述无意义闲聊，尽量短。\n\n"
                f"{raw}"
            )
            raw = await self._llm.summarize(prompt, max_tokens=300)

        return limit_utf8_bytes(normalize_memory_text(raw), max_entry_bytes)

    async def build_prompt_context(self, group_id: int, user_id: int,
                                   user_message: str,
                                   compress_recent: bool = False) -> str:
        """组装完整的 prompt 上下文（记忆注入）。

        注意：近期群聊对话不在这里注入，而是由调用方以标准
        user/assistant 消息形式注入 messages，避免同一份历史被重复塞入。
        """
        parts = []

        # 1. 世界书常驻设定
        constants = self.lorebook.get_all_constant()
        if constants:
            parts.append("[设定]:\n" + "\n".join(constants))

        # 2. 世界书关键词匹配
        lore_hits = self.lorebook.match(user_message)
        if lore_hits:
            parts.append("[相关设定]:\n" + "\n".join(lore_hits))

        # 3. 长期记忆检索
        memories = await self.vector.search(user_message, group_id)
        if memories:
            mem_text = "\n".join(m.content for m in memories[:3])
            parts.append(f"[相关记忆]:\n{mem_text}")

        return "\n\n".join(parts)
