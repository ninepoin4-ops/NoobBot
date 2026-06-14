"""配置管理 API — 读写 + 智能热更新。"""
from __future__ import annotations
from aiohttp import web

from webui.hot_reload import (
    apply_config_change, rebuild_llm_client,
    mask_sensitive, get_config_meta, classify,
    APPLIED, REBUILD_REQUIRED, RESTART_REQUIRED,
)


async def get_config_handler(request: web.Request) -> web.Response:
    """GET /api/config — 返回完整 config（脱敏）+ 字段元信息。"""
    bot = request.app["bot"]
    try:
        masked = mask_sensitive(bot.config)
        return web.json_response({
            "ok": True,
            "data": {
                "config": masked,
                "meta": get_config_meta(),
            },
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def set_config_handler(request: web.Request) -> web.Response:
    """POST /api/config — body: {key: "llm.model", value: "..."}"""
    bot = request.app["bot"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效的 JSON"}, status=400)

    key = body.get("key")
    value = body.get("value")
    if not key:
        return web.json_response({"ok": False, "error": "缺少 key"}, status=400)

    result = apply_config_change(bot, key, value)
    status_code = 200 if result.get("ok") else 400
    return web.json_response(result, status=status_code)


async def classify_handler(request: web.Request) -> web.Response:
    """GET /api/config/classify?key=llm.model — 查询单个字段的生效方式。"""
    key = request.query.get("key")
    if not key:
        return web.json_response({"ok": False, "error": "缺少 key"}, status=400)
    return web.json_response({"ok": True, "status": classify(key)})


async def rebuild_llm_handler(request: web.Request) -> web.Response:
    """POST /api/config/reload-llm — 重建 LLM 客户端。"""
    bot = request.app["bot"]
    result = rebuild_llm_client(bot)
    status_code = 200 if result.get("ok") else 500
    return web.json_response(result, status=status_code)
