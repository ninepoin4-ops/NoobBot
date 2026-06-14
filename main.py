"""Noob Bot 入口"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger


def load_config() -> dict:
    """加载配置"""
    config_path = Path(__file__).parent / "config" / "config.yaml"
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 环境变量覆盖
    import os
    load_dotenv(Path(__file__).parent / "config" / ".env")

    if os.getenv("LLM_API_KEY"):
        config["llm"]["api_key"] = os.getenv("LLM_API_KEY")
    elif os.getenv("LLM_API_KEY_HASH"):
        # 从 hash 重构 (sk- + hash)
        h = os.getenv("LLM_API_KEY_HASH") or ""
        config["llm"]["api_key"] = "sk-" + h
    if os.getenv("MASTER_QQ"):
        config["bot"]["master_id"] = os.getenv("MASTER_QQ")
    if os.getenv("NAPCAT_TOKEN"):
        config["napcat"]["reverse"]["access_token"] = os.getenv("NAPCAT_TOKEN")

    return config


# install.bat 生成的 .env 模板占位符；用户没改时 api_key 会拼成 sk- 占位符
_PLACEHOLDER_API_KEYS = {
    "", "sk-", "sk-YOUR_DEEPSEEK_KEY_HERE", "YOUR_DEEPSEEK_KEY_HERE",
}


def _warn_if_api_key_missing(config: dict) -> None:
    """启动时检查 LLM api_key 是否为空或仍是占位符。

    空 key 下 LLMClient 仍会构造成功（OpenAI 客户端不预校验），
    但第一次调用必然 401，Bot 对所有消息表现为静默/兜底，
    用户会以为 Bot 坏了。这里给醒目告警，指明修复方向。
    """
    api_key = (config.get("llm", {}) or {}).get("api_key", "") or ""
    if api_key.strip() in _PLACEHOLDER_API_KEYS:
        logger.error("=" * 60)
        logger.error("⚠️  LLM API key 未配置！Bot 将无法生成回复。")
        logger.error("   请编辑 config/.env 填入真实的 DeepSeek API key：")
        logger.error("     LLM_API_KEY_HASH=<你的 key，去掉 sk- 前缀>")
        logger.error("   或在 config/config.yaml 的 llm.api_key 填完整 key。")
        logger.error("   获取地址: https://platform.deepseek.com/api_keys")
        logger.error("=" * 60)
    elif "YOUR_" in api_key or "HERE" in api_key.upper():
        # 兜底：检测其它形式的占位符
        logger.warning(
            f"⚠️  LLM api_key 看起来是占位符 ({api_key[:20]}...)，"
            "若非真实 key 请修改 config/.env"
        )


def setup_logging(config: dict):
    """配置日志"""
    lc = config.get("logging", {})
    level = lc.get("level", "INFO")
    log_file = lc.get("file", "data/bot.log")
    rotation = lc.get("rotation", "10 MB")

    logger.remove()  # 清除默认 handler
    logger.add(sys.stderr, level=level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
    logger.add(log_file, level=level, rotation=rotation,
               format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

    # WebUI 日志流：把日志转发到事件总线，前端可实时查看
    try:
        from webui.events import event_bus
        def _loguru_to_eventbus(message):
            record = message.record
            event_bus.emit_nowait("log", {
                "level": record["level"].name,
                "message": str(record["message"]),
                "module": record["module"],
                "line": record["line"],
            })
        logger.add(_loguru_to_eventbus, level="INFO")
    except Exception as e:
        logger.debug(f"WebUI 日志 sink 注册失败（已忽略）: {e}")

    logger.info("日志配置完成")


async def main():
    config = load_config()
    setup_logging(config)

    # 启动前校验关键配置：空/占位符 api_key 会让 LLM 静默失败，
    # 用户以为是 Bot 坏了。这里只告警不退出（用户可能想先跑起来看 WebUI）。
    _warn_if_api_key_missing(config)

    # 延迟导入（确保日志先配好）
    from src.bot import QQBot

    bot = QQBot(config)

    # 启动 WebUI（与 Bot 同 event loop，失败不影响主流程）
    webui_runner = None
    try:
        from webui.server import start_webui
        webui_runner = await start_webui(bot, config)
    except Exception as e:
        logger.error(f"WebUI 初始化失败（已忽略）: {e}")

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    except Exception as e:
        logger.exception(f"启动失败: {e}")
    finally:
        if webui_runner:
            try:
                await webui_runner.cleanup()
            except Exception:
                pass
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
