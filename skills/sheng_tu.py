"""生图技能 — GPT Image 2（文生图 + 图生图 + 队列）"""
from __future__ import annotations
import asyncio
import json
import os
import re
import urllib.request
from typing import Any

from skills.base import Skill


class ShengTuSkill(Skill):
    name = "sheng_tu"
    description = "GPT Image 2 生图 / 改图"
    triggers = ["生图", "画图", "生成图片", "画一张", "改图", "换风格"]

    # 实例级异步锁，保证同一时刻只有一个生图任务在跑。
    # 用 bool 做"锁"没有同步语义，两个协程会同时通过检查进入临界区。
    def __init__(self):
        self._lock = asyncio.Lock()

    async def run(self, bot: Any, group_id: int, user_id: int,
                  message: str, params: dict | None = None) -> str | None:
        # ── 排队检查 ──
        # 非阻塞 acquire：拿不到锁说明正在生图，立即排队反馈
        if self._lock.locked():
            await bot.send_group_msg_by_id(
                group_id, "⏳ 正在生成上一张图，请稍后再试"
            )
            return None

        # 提取参考图和 prompt
        ref_urls = self._extract_image_urls(message)
        prompt = self._extract_prompt(message)
        if not prompt and not ref_urls:
            return "请告诉我画什么，或发图片+描述"

        api_key = self._get_api_key()
        if not api_key:
            return ("生图功能未配置 API key。请在 config/.env 填入：\n"
                    "GPT_IMAGE2_API_KEY_HASH=<你的 key，去掉 sk- 前缀>")

        mode = "图生图" if ref_urls else "文生图"
        async with self._lock:
            await bot.send_group_msg_by_id(group_id, f"🎨 {mode}中，请稍候...")
            success, result = await self._generate(api_key, prompt, ref_urls)
            if success:
                # 发送图片（CQ 码图片），不返回文本
                cq_img = f"[CQ:image,file={result}]"
                await bot.send_group_msg_by_id(group_id, cq_img)
            else:
                await bot.send_group_msg_by_id(group_id, f"❌ {mode}失败：{result}")

        return None

    # ── 提取与配置 ──

    def _extract_image_urls(self, message: str) -> list[str]:
        urls = []
        for match in re.finditer(r'\[CQ:image[^\]]*url=([^\]]+)\]', message):
            urls.append(match.group(1))
        return urls

    def _extract_prompt(self, message: str) -> str:
        clean = re.sub(r'\[CQ:[^\]]+\]', '', message).strip()
        for trigger in self.triggers:
            if trigger in clean:
                clean = clean.replace(trigger, "", 1).strip()
                break
        return clean

    # install.bat 生成的 .env 模板占位符；用户没改时拼接出的 key 必然 401，
    # 与其拿假 key 去请求生图 API 报「文生图失败」，不如直接告知未配置。
    _PLACEHOLDER_VALUES = {
        "", "YOUR_GPTIMAGE2_KEY_HERE", "YOUR_DEEPSEEK_KEY_HERE",
    }

    def _get_api_key(self) -> str:
        """从 config/.env 读取 GPT_IMAGE2_API_KEY_HASH，重构为 sk-<hash>。

        未配置或仍是占位符时返回空串，让调用方走「未配置」提示，
        而不是拿假 key 真去请求 API 导致 401。
        """
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config", ".env"
        )
        if not os.path.exists(env_path):
            return ""
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GPT_IMAGE2_API_KEY_HASH="):
                    val = line.split("=", 1)[1].strip()
                    if val and val not in self._PLACEHOLDER_VALUES:
                        return "sk-" + val
        return ""

    # ── API 调用 ──

    async def _generate(self, api_key: str, prompt: str,
                        ref_urls: list[str] | None = None) -> tuple[bool, str]:
        body = {
            "model": "gpt-image-2",
            "prompt": prompt or "保持原图风格，优化细节",
            "aspectRatio": "1024x1024",
            "shutProgress": False,
        }
        if ref_urls:
            body["urls"] = ref_urls

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            "https://grsai.dakka.com.cn/v1/draw/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        def _do_request():
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode("utf-8")

        try:
            raw = await asyncio.to_thread(_do_request)
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("data: "):
                    try:
                        obj = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if obj.get("status") == "succeeded":
                        results = obj.get("results") or []
                        if not results or not results[0].get("url"):
                            return False, "API 返回成功但缺少图片地址"
                        return True, results[0]["url"]
                    elif obj.get("status") == "failed":
                        return False, obj.get("failure_reason", "未知错误")
            return False, "API 返回格式异常"
        except Exception as e:
            return False, str(e)
