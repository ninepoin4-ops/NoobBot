"""Bot 核心调度器 — 串联 NapCat → 引擎 → 记忆 → LLM → 回复"""
from __future__ import annotations
import asyncio
import time
from typing import Any
from loguru import logger

from src.napcat.client import OneBotClient
from src.engine.activator import Activator
from src.engine.scheduler import Scheduler
from src.memory.manager import MemoryManager
from src.llm.client import LLMClient
from src.models.schemas import GroupMessage, ReplyAction
from src.tools.registry import tool_registry, tool_context
from src import state
from skills.manager import skill_manager

# 事件钩子（WebUI 推流用）；import 失败时降级为空操作，不影响 Bot 主流程
try:
    from webui.hooks import emit as _emit_event
except Exception:  # WebUI 未启用或导入失败
    async def _emit_event(_t, **_d): pass


# 这些 CQ 码承载二进制/单段不可拆内容，混在文本里时整条发送，
# 否则 _split_message 的句末标点切分会把图片/base64 撕碎。
# 纯文本类（at/face/reply 等）可正常分段。
_UNCUTTABLE_CQ_PREFIXES = (
    "[CQ:image",       # 图片（含 url 或 base64）
    "[CQ:record",      # 语音
    "[CQ:video",       # 视频
    "[CQ:file",        # 文件
    "[CQ:music",       # 音乐分享
    "[CQ:forward",     # 合并转发
    "[CQ:json",        # json 卡片
    "[CQ:xml",         # xml 卡片
    "[CQ:poke",        # 戳一戳
)


def _has_uncuttable_cq(text: str) -> bool:
    """是否含不能被 _split_message 切坏的 CQ 码（图片/文件/语音等）。"""
    return any(prefix in text for prefix in _UNCUTTABLE_CQ_PREFIXES)


class QQBot:
    """群聊 Bot 主类"""

    def __init__(self, config: dict):
        self.config = config
        self.bot_name = config["bot"]["name"]
        llm_config = config.get("llm", {})
        self.context_window = llm_config.get("context_window", 1_000_000)
        self.context_compression_threshold = llm_config.get(
            "context_compression_threshold", 180_000
        )

        # 各模块
        self.napcat = OneBotClient(config)
        self.activator = Activator(config)
        self.scheduler = Scheduler()
        self.llm = LLMClient(config)
        self.memory = MemoryManager(config, self.llm)

        # 人格预设管理器（config/personalities/*.yaml），注入到 LLM
        from src.personalities.manager import PersonalityManager
        self.personalities = PersonalityManager(config)
        self.personalities.load_all()
        self.llm.set_personality_manager(self.personalities)

        # 注册全局调度器引用
        state.scheduler_instance = self.scheduler

        # 注册事件回调
        self.napcat.on_group_message(self._on_group_message)

        # 设置调度器的发送函数
        self.scheduler.set_send_fn(self._send_group_msg_by_id)

        # NapCat 自动愈合（仅 forward 模式 + 扫码登录用户需要）。
        # import/构造失败不影响主流程。
        self._auto_heal = None
        self._heal_task = None
        try:
            from src.napcat.auto_heal import NapCatAutoHeal
            self._auto_heal = NapCatAutoHeal(self.napcat, config)
        except Exception as e:
            logger.debug(f"自动愈合初始化失败（已忽略）: {e}")

    async def start(self):
        """启动所有模块"""
        logger.info(f"Bot [{self.bot_name}] 启动中...")
        # 加载技能（传入 config 让 manager 应用触发词/启停覆盖）
        skill_manager.load_all(self.config)
        await self.scheduler.start()
        # napcat.start() 会永久阻塞（重连循环），后台任务必须先启动。
        # 自动愈合：持续连不上时，扫磁盘找已登录 QQ 并补写 WS 配置。
        if self._auto_heal is not None:
            self._heal_task = asyncio.create_task(self._auto_heal.run())
        await self.napcat.start()

    async def stop(self):
        # 先停自动愈合循环，再关连接（避免愈合循环在连接被拆后还在跑）
        if self._auto_heal is not None:
            self._auto_heal.stop()
        if self._heal_task is not None:
            self._heal_task.cancel()
            try:
                # 带超时等待，避免任务意外忽略取消导致 Bot 永远退不出
                await asyncio.wait_for(self._heal_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        await self.napcat.stop()
        await self.scheduler.stop()
        logger.info("Bot 已停止")

    # ── 消息处理 ──

    async def _on_group_message(self, msg: GroupMessage):
        """处理一条群消息"""
        # 群黑名单守卫：未启用的群完全静默（不回复、不入记忆、不推流）
        if not self.activator.is_group_enabled(msg.group_id):
            return

        logger.info(
            f"[群{msg.group_id}] {msg.user_id}: {(msg.raw_message or '')[:60]}"
        )

        # WebUI 事件：收到消息
        await _emit_event(
            "msg_received",
            group_id=msg.group_id,
            user_id=msg.user_id,
            raw_message=msg.raw_message or "",
        )

        # 所有群消息先入短期记忆，保证 Bot 能感知完整群聊氛围
        # （而不是只有"被回复的消息"才进 buffer）
        await self.memory.on_message(msg.group_id, msg.user_id, msg.raw_message)

        # 活跃引擎决策
        decision = await self.activator.decide(msg)

        # WebUI 事件：决策结果
        await _emit_event(
            "decision",
            group_id=msg.group_id,
            should_reply=decision.should_reply,
            reason=decision.reason,
            priority=decision.priority,
            action=decision.action.value if decision.action else None,
        )

        if not decision.should_reply:
            return

        logger.info(f"决定回复: {decision.reason} (priority={decision.priority})")

        # 检查技能触发
        matched_skill = skill_manager.match(msg.raw_message)
        if matched_skill:
            logger.info(f"技能触发: [{matched_skill.name}]")
            await _emit_event(
                "skill_trigger",
                group_id=msg.group_id,
                skill_name=matched_skill.name,
            )
            skill_reply = await skill_manager.execute(
                matched_skill, self, msg.group_id, msg.user_id, msg.raw_message
            )
            if skill_reply:
                await self._send_group_msg_by_id(msg.group_id, skill_reply)
            self.activator.update_cooldown(msg)
            await self.memory.on_interaction(msg.group_id, msg.user_id,
                                             msg.raw_message,
                                             skill_reply or f"[技能: {matched_skill.name}]")
            return

        # 构造回复
        reply = await self._generate_reply(msg)
        logger.info(f"[群{msg.group_id}] {self.bot_name}: {reply[:120] if reply else '(空)'}")

        if not reply or reply.isspace():
            # LLM 调用失败返回空串。对高优先级触发（@/quote/名字命中），
            # 完全沉默会让用户以为 Bot 挂了；这里给一句简短兜底再走正常发送流程。
            # 低优先级（随机插话等）失败则保持安静，不打扰群里。
            if decision.priority >= 8:
                reply = "呜，我刚刚走神了，能再说一遍吗 (´•̥ω•̥`)"
            else:
                logger.info("回复内容为空（可能是 LLM 调用失败），低优先级跳过")
                return

        # 更新冷却
        self.activator.update_cooldown(msg)

        # 发送
        if decision.action == ReplyAction.DELAYED:
            self.scheduler.add_delayed_reply(msg.group_id, reply, decision.delay)
        else:
            await self._send_reply(msg, reply)

        # 存入长期记忆
        await self.memory.on_interaction(msg.group_id, msg.user_id,
                                         msg.raw_message, reply)

    # ── LLM 回复生成 ──

    async def _generate_reply(self, msg: GroupMessage) -> str:
        """生成回复"""
        messages = await self._build_reply_messages(msg, compress_context=False)
        context_chars = self._estimate_context_chars(messages)
        if context_chars >= self.context_compression_threshold:
            logger.info(
                f"[群{msg.group_id}] 上下文约 {context_chars} 字符，达到压缩阈值 "
                f"{self.context_compression_threshold}，开始压缩"
            )
            messages = await self._build_reply_messages(msg, compress_context=True)
            messages = await self._compress_messages_if_needed(messages)
            context_chars = self._estimate_context_chars(messages)

        # 4. 获取工具 schema
        enabled_tools = self.config.get("tools", {}).get("enabled")
        tools = tool_registry.get_openai_schemas(enabled_tools)

        # 5. 调用 LLM
        system_len = len(messages[0].get("content", "")) if messages else 0
        logger.info(f"[群{msg.group_id}] 思考中... 上下文: "
                    f"system({system_len}ch) + "
                    f"{len(messages)-1}轮对话，约 {context_chars} 字符")

        # WebUI 事件：开始思考
        await _emit_event(
            "llm_thinking_start",
            group_id=msg.group_id,
            context_chars=context_chars,
            tool_count=len(tools),
        )

        _t0 = time.time()
        with tool_context(group_id=msg.group_id, user_id=msg.user_id):
            reply = await self.llm.chat(messages, tools=tools)
        _elapsed = time.time() - _t0
        logger.info(f"[群{msg.group_id}] LLM 回复 ({_elapsed:.1f}s): {reply[:100]}")

        # WebUI 事件：LLM 回复完成
        await _emit_event(
            "llm_reply",
            group_id=msg.group_id,
            reply_preview=reply[:200],
            elapsed_ms=int(_elapsed * 1000),
        )

        return reply

    async def _build_reply_messages(
        self, msg: GroupMessage, compress_context: bool = False
    ) -> list[dict]:
        """构建 LLM messages；必要时使用压缩后的近期上下文。"""
        # 1. 组装上下文
        memory_context = await self.memory.build_prompt_context(
            msg.group_id, msg.user_id, msg.raw_message,
            compress_recent=compress_context,
        )

        # 2. 系统提示
        system_prompt = await self.llm.generate_system_prompt(
            msg.group_id, self.bot_name, memory_context,
            master_id=self.config["bot"].get("master_id", ""),
        )

        # 3. 构建 messages
        messages = [{"role": "system", "content": system_prompt}]

        # 注入近期上下文（含当前消息——on_message 阶段已入 buffer）
        short_context = self.memory.buffer.get_group_context(msg.group_id, max_turns=10)
        if compress_context and self.memory.compressor:
            short_context = await self.memory.compressor.compress(
                short_context, max_turns=6, force=True
            )
        for turn in short_context:
            if turn.role == "system":
                # 压缩摘要等 system turn 单独作为 system 消息注入，
                # 避免被误当作 Bot 自己说过的话
                messages.append({"role": "system", "content": turn.content})
            else:
                messages.append({
                    "role": "user" if turn.role == "user" else "assistant",
                    "content": turn.content,
                })

        # 当前消息已在 short_context 中（on_message 已入 buffer），无需重复 append
        return messages

    def _estimate_context_chars(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
        return total

    async def _compress_messages_if_needed(self, messages: list[dict]) -> list[dict]:
        # 防御性早返回：当前调用方只在超阈值时进入，但保留此判断作为
        # 安全网，避免日后调用路径变化导致无谓的 LLM 摘要调用
        if self._estimate_context_chars(messages) < self.context_compression_threshold:
            return messages
        if len(messages) <= 2:
            return messages

        system_msg = messages[0]
        current_msg = messages[-1]
        history = messages[1:-1]
        history_text = "\n".join(
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')}" for m in history
        )
        summary = await self.llm.summarize(
            "压缩以下上下文，保留事实、偏好、承诺、未完成事项和最近话题，去掉闲聊和重复内容：\n\n"
            + history_text,
            max_tokens=2048,
        )
        return [
            system_msg,
            {"role": "system", "content": f"[压缩上下文]: {summary}"},
            current_msg,
        ]

    # ── 消息发送 ──

    async def send_group_msg_by_id(self, group_id: int, text: str):
        """公开的发送接口（供 skills 调用）"""
        await self._send_group_msg_by_id(group_id, text)

    async def _send_reply(self, msg: GroupMessage, text: str):
        """回复群消息"""
        # 含"不可切"的 CQ 码（图片/base64/文件/语音等二进制载体）时整条发送，
        # 避免被 _split_message 切坏。普通文本（哪怕含 [CQ:at]）仍可分段。
        if _has_uncuttable_cq(text):
            response = await self.napcat.send_group_msg(msg.group_id, text)
            self._remember_sent_message(msg.group_id, response)
            self.activator.commit_reply(msg.group_id)
            await _emit_event("msg_sent", group_id=msg.group_id, segment_count=1)
            return
        segments = self._split_message(text)
        sent_any = False
        for segment in segments:
            response = await self.napcat.send_group_msg(msg.group_id, segment)
            self._remember_sent_message(msg.group_id, response)
            sent_any = True
            await asyncio.sleep(0.5)
        if sent_any:
            self.activator.commit_reply(msg.group_id)
            await _emit_event(
                "msg_sent",
                group_id=msg.group_id,
                segment_count=len(segments),
            )

    async def _send_group_msg_by_id(self, group_id: int, text: str):
        """按群 ID 发送（供调度器调用）"""
        # 群黑名单守卫：未启用的群不发送任何消息（含 scheduler 主动消息）
        if not self.activator.is_group_enabled(group_id):
            logger.debug(f"群 {group_id} 已禁用，跳过主动发送")
            return
        # 含"不可切"的 CQ 码（图片/base64/文件/语音等二进制载体）时整条发送，
        # 避免被 _split_message 切坏。普通文本（哪怕含 [CQ:at]）仍可分段。
        if _has_uncuttable_cq(text):
            response = await self.napcat.send_group_msg(group_id, text)
            self._remember_sent_message(group_id, response)
            self.activator.commit_reply(group_id)
            await _emit_event("msg_sent", group_id=group_id, segment_count=1)
            return
        sent_any = False
        segments = self._split_message(text)
        for segment in segments:
            response = await self.napcat.send_group_msg(group_id, segment)
            self._remember_sent_message(group_id, response)
            sent_any = True
            await asyncio.sleep(0.5)
        if sent_any:
            self.activator.commit_reply(group_id)
            await _emit_event("msg_sent", group_id=group_id, segment_count=len(segments))

    def _split_message(self, text: str, max_len: int = 200) -> list[str]:
        """每两句一切，短句模式"""
        import re

        # 按句末标点分割句子（去掉英文句号，避免URL/base64被切碎）
        sentences = re.split(r'(?<=[。！？!?\n])', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) <= 2:
            segments = [text]
        else:
            # 每两句一组
            segments = []
            for i in range(0, len(sentences), 2):
                group = sentences[i:i+2]
                segments.append("".join(group))

        bounded = []
        for segment in segments:
            if len(segment) <= max_len:
                bounded.append(segment)
                continue
            for i in range(0, len(segment), max_len):
                bounded.append(segment[i:i + max_len])

        return bounded

    def _remember_sent_message(self, group_id: int, response: dict | None):
        if not response:
            return
        data = response.get("data") if isinstance(response, dict) else None
        message_id = None
        if isinstance(data, dict):
            message_id = data.get("message_id")
        message_id = message_id or response.get("message_id")
        self.activator.remember_bot_message(group_id, message_id)
