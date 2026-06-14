"""WebUI aiohttp Application 工厂 + 启动入口。"""
from __future__ import annotations
from pathlib import Path

from aiohttp import web
from loguru import logger

from webui.events import event_bus


# 静态资源目录
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(bot, config: dict) -> web.Application:
    """创建 aiohttp Application，注入 bot 实例。"""
    app = web.Application()

    # 全局共享对象
    app["bot"] = bot
    app["config"] = config

    # ── API 路由 ──
    from webui.handlers.dashboard import dashboard_handler
    from webui.handlers.events import ws_events_handler
    from webui.handlers.config_api import (
        get_config_handler, set_config_handler,
        classify_handler, rebuild_llm_handler,
    )
    from webui.handlers.memory_api import (
        short_term_groups_handler, short_term_handler, short_term_delete_handler,
        long_term_stats_handler, long_term_search_handler, long_term_delete_handler,
    )
    from webui.handlers.skills_api import (
        skills_list_handler, skills_set_triggers_handler, skills_toggle_handler,
    )
    from webui.handlers.tools_api import (
        tools_list_handler, tools_toggle_handler,
    )
    from webui.handlers.groups_api import (
        groups_list_handler, groups_toggle_handler,
    )
    from webui.handlers.personalities_api import (
        personalities_list_handler, personalities_detail_handler,
        personalities_activate_handler, personalities_reload_handler,
        personalities_save_handler, personalities_delete_handler,
        personalities_open_folder_handler, skills_open_folder_handler,
    )

    app.router.add_get("/api/dashboard", dashboard_handler)
    app.router.add_get("/api/ws", ws_events_handler)
    app.router.add_get("/api/config", get_config_handler)
    app.router.add_post("/api/config", set_config_handler)
    app.router.add_get("/api/config/classify", classify_handler)
    app.router.add_post("/api/config/reload-llm", rebuild_llm_handler)

    # 群聊管理
    app.router.add_get("/api/groups", groups_list_handler)
    app.router.add_post("/api/groups/{group_id}/toggle", groups_toggle_handler)

    # 记忆
    app.router.add_get("/api/memory/groups", short_term_groups_handler)
    app.router.add_get("/api/memory/short-term", short_term_handler)
    app.router.add_delete("/api/memory/short-term/{group_id}", short_term_delete_handler)
    app.router.add_get("/api/memory/long-term/stats", long_term_stats_handler)
    app.router.add_get("/api/memory/long-term", long_term_search_handler)
    app.router.add_delete("/api/memory/long-term/{id}", long_term_delete_handler)

    # 技能
    app.router.add_get("/api/skills", skills_list_handler)
    app.router.add_post("/api/skills/{name}/triggers", skills_set_triggers_handler)
    app.router.add_post("/api/skills/{name}/toggle", skills_toggle_handler)
    app.router.add_post("/api/skills/open-folder", skills_open_folder_handler)

    # 人格预设
    app.router.add_get("/api/personalities", personalities_list_handler)
    app.router.add_get("/api/personalities/active", personalities_list_handler)  # 别名
    app.router.add_post("/api/personalities/activate", personalities_activate_handler)
    app.router.add_post("/api/personalities/reload", personalities_reload_handler)
    app.router.add_post("/api/personalities/open-folder", personalities_open_folder_handler)
    app.router.add_get("/api/personalities/{key}", personalities_detail_handler)
    app.router.add_post("/api/personalities/{key}", personalities_save_handler)
    app.router.add_delete("/api/personalities/{key}", personalities_delete_handler)

    # 工具
    app.router.add_get("/api/tools", tools_list_handler)
    app.router.add_post("/api/tools/{name}/toggle", tools_toggle_handler)

    # ── 静态资源（前端单页应用）──
    if _STATIC_DIR.exists():
        # 首页
        app.router.add_get("/", _serve_index)

        # 单独注册每个静态文件（add_static 会产生 /x/x 的双层路径，不适合单文件）
        for fname in ("style.css", "app.js"):
            fpath = _STATIC_DIR / fname
            if fpath.exists():
                app.router.add_get(f"/{fname}", _make_static_handler(fpath))

    return app


def _make_static_handler(fpath: Path):
    """为单个静态文件生成 handler，附带正确的 Content-Type。"""
    mime = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
    }
    content_type = mime.get(fpath.suffix.lower(), "application/octet-stream")

    async def handler(request: web.Request) -> web.Response:
        try:
            return web.FileResponse(fpath, headers={"Content-Type": content_type})
        except Exception as e:
            return web.Response(text=f"静态资源读取失败: {e}", status=500)

    return handler


async def _serve_index(request: web.Request) -> web.Response:
    """返回 index.html。"""
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(
            text="<h1>WebUI 静态资源缺失</h1><p>请确认 webui/static/index.html 存在</p>",
            status=500,
            content_type="text/html",
        )
    return web.FileResponse(index_path, headers={"Content-Type": "text/html; charset=utf-8"})


async def start_webui(bot, config: dict) -> web.AppRunner | None:
    """启动 WebUI 服务，返回 AppRunner（用于后续清理）。

    失败不抛异常，返回 None 让 Bot 主流程继续。
    """
    webui_cfg = config.get("webui", {})
    if not webui_cfg.get("enabled", True):
        logger.info("WebUI 已在 config 中禁用")
        return None

    host = webui_cfg.get("host", "127.0.0.1")
    port = int(webui_cfg.get("port", 8081))

    # 绑定 bot 到事件总线（供快照生成）
    event_bus.bind_bot(bot)

    try:
        app = create_app(bot, config)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()

        # 安全区警告
        if host in ("0.0.0.0", "::"):
            logger.warning(
                f"⚠️ WebUI 监听 {host}:{port}（局域网可访问且无认证，请确认安全）"
            )
        logger.info(f"🌐 WebUI 已启动: http://127.0.0.1:{port}"
                    + (f" (也监听 {host})" if host not in ("127.0.0.1", "localhost") else ""))
        return runner
    except Exception as e:
        logger.error(f"WebUI 启动失败（Bot 主流程不受影响）: {e}")
        return None
