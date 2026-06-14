"""NapCat process watchdog.

Restarts NapCat when the account is kicked offline so quick login can run again.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger


KICKED_MARKERS = (
    "[KickedOffLine]",
    "你的帐号当前登录已失效",
    "账号当前登录已失效",
)


class NapCatWatchdog:
    def __init__(
        self,
        qq: str,
        root: Path,
        restart_delay: int = 10,
        max_restarts: int = 0,
    ):
        self.qq = qq
        self.root = root
        self.napcat_dir = root / "napcat" / "napcat"
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts
        self.restart_count = 0
        self._stopping = False
        self._proc: subprocess.Popen | None = None

    def run(self):
        if not self.qq:
            raise ValueError("watchdog 需要 QQ 号")
        if not self.napcat_dir.exists():
            raise FileNotFoundError(f"NapCat 目录不存在: {self.napcat_dir}")

        signal.signal(signal.SIGINT, self._handle_stop)
        # Windows 没有 SIGTERM，注册可能抛 ValueError；安全降级
        try:
            signal.signal(signal.SIGTERM, self._handle_stop)
        except (AttributeError, ValueError, OSError):
            pass

        while not self._stopping:
            proc = self._start_napcat()
            should_restart = self._watch_output(proc)
            self._terminate_tree(proc)

            if self._stopping or not should_restart:
                break

            self._kill_qq()
            self.restart_count += 1
            if self.max_restarts and self.restart_count > self.max_restarts:
                logger.error(f"达到最大重启次数 {self.max_restarts}，停止守护")
                break

            logger.warning(
                f"检测到账号下线，{self.restart_delay}s 后重启 NapCat "
                f"(第 {self.restart_count} 次)"
            )
            time.sleep(self.restart_delay)

    def _start_napcat(self) -> subprocess.Popen:
        cmd = ["cmd", "/c", "launcher-user.bat", self.qq]
        logger.info(f"启动 NapCat quick login: QQ={self.qq}")
        self._proc = subprocess.Popen(
            cmd,
            cwd=self.napcat_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return self._proc

    def _watch_output(self, proc: subprocess.Popen) -> bool:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(line, flush=True)
            if any(marker in line for marker in KICKED_MARKERS):
                return True
            if self._stopping:
                return False
        code = proc.wait()
        if code != 0:
            logger.warning(f"NapCat 进程退出，code={code}")
        return False

    def _terminate_tree(self, proc: subprocess.Popen):
        if proc.poll() is not None:
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as e:
            logger.warning(f"结束 NapCat 进程失败: {e}")

    def _kill_qq(self):
        kill_script = self.napcat_dir / "KillQQ.bat"
        if not kill_script.exists():
            return
        try:
            subprocess.run(
                ["cmd", "/c", str(kill_script)],
                cwd=self.napcat_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as e:
            logger.warning(f"清理 QQ 进程失败: {e}")

    def _handle_stop(self, *_args):
        self._stopping = True
        if self._proc:
            self._terminate_tree(self._proc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NapCat kicked-offline watchdog")
    parser.add_argument("--qq", required=True, help="QQ number for quick login")
    parser.add_argument(
        "--restart-delay",
        type=int,
        default=10,
        help="seconds to wait before restart",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=0,
        help="0 means unlimited",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    )
    NapCatWatchdog(
        qq=args.qq,
        root=root,
        restart_delay=max(1, args.restart_delay),
        max_restarts=max(0, args.max_restarts),
    ).run()


if __name__ == "__main__":
    main()
