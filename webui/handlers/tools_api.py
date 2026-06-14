"""工具管理 API — 列表、开关、schema 查看。"""
from __future__ import annotations
from aiohttp import web

from src.tools.registry import tool_registry


async def tools_list_handler(request: web.Request) -> web.Response:
    """GET /api/tools — 工具列表（含完整 schema + 启用状态）。"""
    bot = request.app["bot"]
    try:
        enabled_set = set(bot.config.get("tools", {}).get("enabled") or [])
        all_schemas = tool_registry.get_openai_schemas(enabled=None)
        data = []
        for schema in all_schemas:
            fn = schema.get("function", {})
            name = fn.get("name", "")
            data.append({
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
                "enabled": name in enabled_set,
            })
        return web.json_response({"ok": True, "data": data})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def tools_toggle_handler(request: web.Request) -> web.Response:
    """POST /api/tools/{name}/toggle — 启用/禁用工具。body: {"enabled": bool}"""
    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)
    enabled = bool(body.get("enabled"))

    bot = request.app["bot"]
    enabled_list = list(bot.config.get("tools", {}).get("enabled") or [])
    if enabled and name not in enabled_list:
        enabled_list.append(name)
    elif not enabled and name in enabled_list:
        enabled_list = [t for t in enabled_list if t != name]
    else:
        return web.json_response({"ok": True, "message": "状态未变化"})

    bot.config.setdefault("tools", {})["enabled"] = enabled_list  # bot.py 实时读取，天然热生效
    try:
        from webui.hot_reload import persist_config
        persist_config(bot.config)
    except Exception as e:
        return web.json_response({"ok": True, "warning": f"内存已生效但持久化失败: {e}"})

    action = "启用" if enabled else "禁用"
    return web.json_response({"ok": True, "message": f"已{action}工具 [{name}]"})
