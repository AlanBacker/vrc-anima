"""日志初始化:stderr 走 logging,面向用户的控制台输出走 print(stdout)。

两条通道分开:日志给排查问题的人,stdout 给正在用控制台的人,互不淹没。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup(level: str = "INFO", logfile: str | Path | None = None) -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(level=level.upper(), format=fmt, handlers=handlers)
    # 第三方库的调试噪音压到 WARNING
    for noisy in ("httpx", "httpcore", "google_genai", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
