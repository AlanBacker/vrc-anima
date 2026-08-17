"""命令行入口:anima [--config PATH] [--log-level LEVEL] [--log-file PATH]"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from .config import ConfigError, load
from .log import setup


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="anima",
        description="Anima——住在 VRChat 里的具身 AI(M1 骨架)",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="配置文件路径(默认 ./config.toml,允许缺省=全默认值)",
    )
    parser.add_argument(
        "--log-level", default="", help="覆盖配置里的日志级别(DEBUG/INFO/WARNING)"
    )
    parser.add_argument("--log-file", default="", help="同时把日志写到文件")
    parser.add_argument(
        "--version", action="version", version=f"anima {__version__}"
    )
    args = parser.parse_args()

    try:
        cfg = load(args.config)
        setup(args.log_level or cfg.core.log_level, args.log_file or None)
        from .app import Anima  # 配置合法后再拉起重依赖

        app = Anima(cfg)
    except ConfigError as e:
        print(f"配置错误:{e}", file=sys.stderr)
        return 2

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\n(Ctrl-C)Anima 已退出。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
