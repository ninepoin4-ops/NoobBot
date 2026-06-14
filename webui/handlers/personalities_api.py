"""人格预设 API — 列出/激活/编辑预设 + 打开文件夹。

参照 skills_api.py 的风格：GET 列表 + POST 操作 + 持久化。
"""
from __future__ import annotations
import subprocess
from pathlib import Path

from aiohttp import web
from loguru import logger


# 项目根目录（webui/handlers/personalities_api.py → 向上三级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _pm(request: web.Request):
    """从 app 取人格预设管理器；缺失时返回 None。"""
    bot = request.app["bot"]
    return getattr(bot, "personalities", None)


async def personalities_list_handler(request: web.Request) -> web.Response:
    """GET /api/personalities — 列出所有预设 + 当前激活 key。"""
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    try:
        items = []
        for p in pm.list_all():
            items.append({
                "key": p.key,
                "name": p.name,
                "keywords": list(p.keywords),
                "prompt_count": len(p.prompts),
                "active": p.key == pm.active_key,
            })
        return web.json_response({
            "ok": True,
            "data": items,
            "active": pm.active_key,
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def personalities_detail_handler(request: web.Request) -> web.Response:
    """GET /api/personalities/{key} — 单个预设详情（含 prompts 内容，供编辑）。"""
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    key = request.match_info["key"]
    p = pm.get(key)
    if p is None:
        return web.json_response({"ok": False, "error": f"预设 '{key}' 不存在"}, status=404)
    try:
        return web.json_response({
            "ok": True,
            "data": {
                "key": p.key,
                "name": p.name,
                "keywords": list(p.keywords),
                "prompts": [
                    {"role": item["role"], "content": item["content"]}
                    for item in p.prompts
                ],
                "active": p.key == pm.active_key,
            },
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def personalities_activate_handler(request: web.Request) -> web.Response:
    """POST /api/personalities/activate — body: {name} 切换激活（走 hot_reload）。"""
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)
    name = str(body.get("name") or "").strip()
    if not name:
        return web.json_response({"ok": False, "error": "缺少 name"}, status=400)

    # 走统一的 hot_reload 入口（会 reload 目录 + set_active + 持久化 config.yaml）
    from webui.hot_reload import apply_config_change
    result = apply_config_change(request.app["bot"], "bot.active_personality", name)
    status_code = 200 if result.get("ok") else 400
    return web.json_response(result, status=status_code)


async def personalities_reload_handler(request: web.Request) -> web.Response:
    """POST /api/personalities/reload — 重新扫描目录（用户手动放文件后刷新）。"""
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    try:
        count = pm.reload()
        return web.json_response({
            "ok": True,
            "message": f"已重新加载，共 {count} 个预设",
            "count": count,
            "active": pm.active_key,
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def personalities_save_handler(request: web.Request) -> web.Response:
    """POST /api/personalities/{key} — 保存编辑（写回 yaml）。

    body: {name, keywords, prompts: [{role, content}]}
    """
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    key = request.match_info["key"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)

    name = str(body.get("name") or key)
    keywords = body.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    prompts = body.get("prompts") or []
    if not isinstance(prompts, list):
        return web.json_response({"ok": False, "error": "prompts 必须是列表"}, status=400)

    # 基本校验：至少一条非空 content
    valid = [p for p in prompts
             if isinstance(p, dict) and str(p.get("content", "")).strip()]
    if not valid:
        return web.json_response({"ok": False, "error": "至少需要一条 prompt"}, status=400)

    try:
        pm.save(key=key, name=name, keywords=keywords, prompts=valid)
        return web.json_response({"ok": True, "message": f"已保存预设 [{key}]"})
    except Exception as e:
        logger.exception(f"保存人格预设失败 [{key}]: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def personalities_delete_handler(request: web.Request) -> web.Response:
    """DELETE /api/personalities/{key} — 删除预设（不能删 default 和当前激活）。"""
    pm = _pm(request)
    if pm is None:
        return web.json_response({"ok": False, "error": "人格预设未初始化"}, status=500)
    key = request.match_info["key"]
    try:
        if not pm.delete(key):
            return web.json_response({
                "ok": False,
                "error": f"无法删除 '{key}'（不存在/是 default/当前激活）",
            }, status=400)
        return web.json_response({"ok": True, "message": f"已删除预设 [{key}]"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ── 打开文件夹（Windows）──

def _is_local_request(request: web.Request) -> bool:
    """判断请求是否来自本机（127.0.0.1 / ::1）。

    open-folder 会调 explorer.exe，等于在 Bot 所在机器上执行系统命令，
    远程触发等同于远程命令执行。必须限制只能本机调用。
    """
    peername = request.transport.get_extra_info("peername") if request.transport else None
    if not peername:
        return False
    ip = peername[0] if isinstance(peername, tuple) and peername else ""
    return ip in ("127.0.0.1", "::1", "localhost")


def _open_folder_in_explorer(dir_path: Path) -> tuple[bool, str]:
    """用资源管理器打开目录。返回 (成功, 消息)。"""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        # explorer.exe 打开目录；非阻塞，不需要等返回
        subprocess.Popen(["explorer.exe", str(dir_path)])
        return True, str(dir_path)
    except FileNotFoundError:
        return False, "explorer.exe 未找到（非 Windows 环境？）"
    except Exception as e:
        return False, str(e)


async def personalities_open_folder_handler(request: web.Request) -> web.Response:
    """POST /api/personalities/open-folder — 打开 config/personalities/ 目录。

    仅允许本机调用（防止远程触发系统命令执行）。
    """
    if not _is_local_request(request):
        return web.json_response(
            {"ok": False, "error": "出于安全考虑，打开文件夹仅限本机访问"},
            status=403,
        )
    target = _PROJECT_ROOT / "config" / "personalities"
    ok, msg = _open_folder_in_explorer(target)
    if ok:
        return web.json_response({"ok": True, "path": msg})
    return web.json_response({"ok": False, "error": msg}, status=500)


async def skills_open_folder_handler(request: web.Request) -> web.Response:
    """POST /api/skills/open-folder — 打开 skills/ 目录。

    仅允许本机调用（防止远程触发系统命令执行）。
    """
    if not _is_local_request(request):
        return web.json_response(
            {"ok": False, "error": "出于安全考虑，打开文件夹仅限本机访问"},
            status=403,
        )
    target = _PROJECT_ROOT / "skills"
    ok, msg = _open_folder_in_explorer(target)
    if ok:
        return web.json_response({"ok": True, "path": msg})
    return web.json_response({"ok": False, "error": msg}, status=500)
