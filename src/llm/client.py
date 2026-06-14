"""LLM API 客户端 — OpenAI 兼容接口"""
from __future__ import annotations
import json
from typing import AsyncIterator
from openai import AsyncOpenAI
from loguru import logger

# WebUI 工具调用事件钩子；导入失败时降级为空操作
try:
    from webui.hooks import emit as _emit_event
except Exception:
    async def _emit_event(_t, **_d): pass


# summarize 失败时的兜底摘要：用固定短占位而非原文切片，
# 否则把未压缩的原文当"摘要"塞回上下文反而会撑爆窗口。
_SUMMARY_FALLBACK = "（摘要生成失败，已跳过此段历史）"


class LLMClient:
    """大模型 API 客户端"""

    def __init__(self, config: dict):
        lc = config["llm"]
        self.model = lc["model"]
        self.max_tokens = lc["max_tokens"]
        self.temperature = lc["temperature"]

        self._client = AsyncOpenAI(
            api_key=lc["api_key"],
            base_url=lc["base_url"],
        )

        # 人格预设管理器（由 bot.py 注入；未注入时走下方硬编码兜底）
        self._personality_manager = None

    # 工具调用最大轮数，防止 LLM 反复触发工具导致无限递归
    MAX_TOOL_ROUNDS = 4

    def set_personality_manager(self, pm) -> None:
        """注入人格预设管理器（由 QQBot 构造时调用）。"""
        self._personality_manager = pm

    async def chat(self, messages: list[dict],
                   tools: list[dict] | None = None,
                   _tool_round: int = 0) -> str:
        """基础对话，返回文本回复。

        _tool_round 内部使用，限制连续工具调用轮数。
        """
        kwargs = dict(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = await self._client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message

            # 处理工具调用
            if msg.tool_calls:
                if _tool_round >= self.MAX_TOOL_ROUNDS:
                    logger.warning(
                        f"工具调用达到上限 {self.MAX_TOOL_ROUNDS}，"
                        "强制返回当前文本，避免无限递归"
                    )
                    return msg.content or ""
                return await self._handle_tool_calls(
                    messages, msg, tools, _tool_round
                )

            return msg.content or ""

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 返回空串让上层判断后跳过，避免故障时反复刷屏兜底文案
            return ""

    async def chat_raw(self, messages: list[dict],
                       temperature: float = 0.7,
                       max_tokens: int = 4096) -> str:
        """原始 LLM 调用，返回 API 原始文本（不对工具调用做处理）"""
        kwargs = dict(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"chat_raw 调用失败: {e}")
            return ""

    async def _handle_tool_calls(self, messages: list[dict],
                                 msg, tools: list[dict] | None = None,
                                 _tool_round: int = 0) -> str:
        """处理 LLM 发起的工具调用"""
        from src.tools.registry import tool_registry

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "function": {"name": tc.function.name,
                                 "arguments": tc.function.arguments},
                    "type": "function",
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}

            logger.info(f"工具调用: {tool_name}({args})")

            # WebUI 事件：工具调用
            await _emit_event(
                "tool_call",
                name=tool_name,
                args=args,
                round=_tool_round + 1,
            )

            # 执行工具
            fn = tool_registry.get(tool_name)
            if fn:
                try:
                    result = await fn(**args)
                    success = True
                except Exception as e:
                    logger.exception(f"工具执行失败 [{tool_name}]: {e}")
                    result = f"工具 {tool_name} 执行失败: {e}"
                    success = False
            else:
                result = f"工具 {tool_name} 不存在"
                success = False

            # WebUI 事件：工具结果
            await _emit_event(
                "tool_result",
                name=tool_name,
                result_preview=str(result)[:300],
                success=success,
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

        # 回复带工具结果的 LLM 调用，传入下一轮计数
        return await self.chat(messages, tools=tools, _tool_round=_tool_round + 1)

    async def summarize(self, text: str, max_tokens: int = 512) -> str:
        """文本摘要（用于记忆压缩）"""
        messages = [
            {"role": "system", "content": "你是一个摘要助手。用简洁的语言总结以下内容。"},
            {"role": "user", "content": text},
        ]
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
            )
            content = resp.choices[0].message.content
            return content if content and content.strip() else _SUMMARY_FALLBACK
        except Exception as e:
            logger.warning(f"摘要失败: {e}")
            # 失败时返回固定短占位而非原文切片——原文可能极长，
            # 当作"摘要"用会反过来撑爆上下文。
            return _SUMMARY_FALLBACK

    async def generate_system_prompt(self, group_id: int,
                                     bot_name: str,
                                     memory_context: str,
                                     master_id: str = "") -> str:
        """生成系统提示词（单字符串版本，向后兼容）。

        优先走注入的人格预设管理器（支持多 role），未注入时回退硬编码。
        返回所有 system-role 内容拼接的字符串；user/assistant 角色的 prompt
        会被拼到字符串里（保持返回值是 str 的契约）。
        """
        if self._personality_manager is not None:
            try:
                messages = self._personality_manager.render_system_prompt(
                    bot_name=bot_name, master_id=master_id,
                    group_id=group_id, memory_context=memory_context,
                )
                # 拼 system 段；非 system 段（user/assistant）单独成块
                parts = [m["content"] for m in messages]
                return "\n".join(p for p in parts if p)
            except Exception as e:
                # PM 异常不应阻塞消息处理；回退硬编码并记日志
                logger.warning(f"人格预设渲染失败，回退硬编码: {e}")
        # 硬编码兜底（无 manager 或 PM 渲染异常时）
        return self._builtin_prompt(bot_name, group_id, master_id, memory_context)

    @staticmethod
    def _builtin_prompt(bot_name: str, group_id: int,
                        master_id: str, memory_context: str) -> str:
        """硬编码兜底人格（与 config/personalities/default.yaml 内容一致）。"""
        parts = [
            f"你是{bot_name}，一个可爱的 QQ 群聊 AI 助手。",
            f"你当前在群ID: {group_id}，此群的对话和记忆独立于其他群。",
            f"你的性格: 可爱、温柔、热情、有点小俏皮，说话亲切自然。",
            f"你喜欢用轻松活泼的语气和大家聊天，偶尔会卖萌，但不做作。",
            f"回复简洁友好，一般一两句话就好，不要长篇大论。",
            f"你会主动参与群聊讨论，但不会刷屏打扰别人。",
            f"你是群聊中的一员，有自己的性格和温度。",
            f"除非被@，否则你可以选择不回复，保持自然。",
            f"遇到不知道的问题，坦诚说不知道，不编造。",
            f"适当使用颜文字或 emoji 表达情绪，比如 (｡•̀ᴗ-)✧、(´▽`ʃ♡ƪ)。",
            f"你的性格会随着对话自然发展，逐渐形成更立体的形象。",
        ]
        if master_id:
            parts.append(f"你的主人QQ是{master_id}，主人说话时你可以更亲近一些。")
        parts.append("")
        parts.append(memory_context)
        return "\n".join(parts)
