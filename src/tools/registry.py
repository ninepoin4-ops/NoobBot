"""工具调用注册中心"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Any, Callable, Coroutine

from loguru import logger


# ── 工具注册与发现 ──

class ToolRegistry:
    """工具注册中心。工作方式类似 OpenAI Function Calling 的 schema 注册。"""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def register(self, name: str, description: str,
                 fn: Callable[..., Coroutine[Any, Any, str]],
                 parameters: dict):
        """注册一个工具"""
        self._tools[name] = {
            "name": name,
            "description": description,
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }
        logger.info(f"工具已注册: {name}")

    def get(self, name: str) -> Callable | None:
        tool = self._tools.get(name)
        return tool["fn"] if tool else None

    def get_openai_schemas(self, enabled: list[str] | None = None) -> list[dict]:
        """返回 OpenAI 格式的 tools 列表"""
        if enabled is None:
            return [t["schema"] for t in self._tools.values()]

        schemas = []
        for name in enabled:
            tool = self._tools.get(name)
            if tool:
                schemas.append(tool["schema"])
            else:
                logger.warning(f"配置启用了未注册工具: {name}")
        return schemas

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


# 全局工具注册中心
tool_registry = ToolRegistry()
_tool_context: ContextVar[dict[str, Any]] = ContextVar(
    "tool_context", default={}
)


@contextmanager
def tool_context(**values: Any):
    current = dict(_tool_context.get())
    current.update(values)
    token = _tool_context.set(current)
    try:
        yield
    finally:
        _tool_context.reset(token)


def get_tool_context() -> dict[str, Any]:
    return dict(_tool_context.get())


# ── 内置工具 ──

async def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索（使用 DuckDuckGo，异步线程池避免阻塞）"""
    try:
        from duckduckgo_search import DDGS
        import asyncio
        def _search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        results = await asyncio.to_thread(_search)
        if not results:
            return "没有找到相关结果"
        return "\n\n".join(
            f"{i+1}. {r['title']}\n{r['href']}\n{r['body']}"
            for i, r in enumerate(results)
        )
    except ImportError:
        return "搜索功能未安装（需要 duckduckgo-search）"
    except Exception as e:
        return f"搜索失败: {e}"


async def remind(seconds: int, message: str) -> str:
    """设置定时提醒"""
    from src.state import scheduler_instance
    if scheduler_instance:
        ctx = get_tool_context()
        gid = int(ctx.get("group_id") or 0)
        if gid <= 0:
            return "无法确定要提醒的群"
        seconds = max(1, int(seconds))
        scheduler_instance.add_reminder(gid, message, seconds)
        return f"已创建提醒，{seconds}秒后提醒：{message}"
    return "提醒功能暂时不可用"


async def get_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间"""
    from datetime import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def weather(location: str) -> str:
    """简单天气查询，复用搜索工具避免额外 API key。"""
    return await web_search(f"{location} 天气", max_results=3)


# ── 注册内置工具 ──

tool_registry.register(
    name="web_search",
    description="搜索互联网。当你需要实时信息、新闻或你不知道的知识时使用。",
    fn=web_search,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果数（默认5）",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)

tool_registry.register(
    name="remind",
    description="设置一个定时提醒。Bot 会在指定秒数后提醒你。",
    fn=remind,
    parameters={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "integer",
                "description": "多少秒后提醒",
            },
            "message": {
                "type": "string",
                "description": "提醒内容",
            },
        },
        "required": ["seconds", "message"],
    },
)

tool_registry.register(
    name="weather",
    description="查询城市天气。需要用户提供城市或地区名称。",
    fn=weather,
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "城市或地区名称，如 北京、上海、广州",
            },
        },
        "required": ["location"],
    },
)

tool_registry.register(
    name="get_time",
    description="获取当前时间，默认北京时间",
    fn=get_time,
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "时区，如 Asia/Shanghai",
                "default": "Asia/Shanghai",
            },
        },
    },
)
