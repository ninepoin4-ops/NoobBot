"""HTML 报告渲染器 — Jinja2 + Playwright 截图"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from skills.group_report.models import GroupReport

# ── 模板路径 ──
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
_TEMPLATE = _ENV.get_template("report.html")


def generate_report_image(report: GroupReport) -> str:
    """渲染 HTML → Playwright 截图 → PNG，返回文件路径"""
    # 1. 渲染 HTML
    html = _TEMPLATE.render(report=report)

    # 2. Playwright 截图
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    try:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 800, "height": 100},
            device_scale_factor=2,  # Retina 高清
        )
        page.set_content(html, wait_until="networkidle")

        # 等字体和渲染完成
        page.wait_for_timeout(500)

        # 获取实际内容高度
        box = page.evaluate("() => document.body.getBoundingClientRect()")
        h = int(box["height"]) + 40

        # 重新截图——精确高度
        page.set_viewport_size({"width": 800, "height": h})
        page.wait_for_timeout(200)

        # 截图
        out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(
            out_dir,
            f"group_report_{report.group_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
        )
        page.screenshot(path=path, full_page=True, type="jpeg", quality=92)
        logger.info(f"报告图片已生成: {path}")
        return path
    finally:
        p.stop()
