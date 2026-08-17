"""CLI 控制台(DESIGN.md Q14):管理动作只留给人,不暴露给模型。

命令走 stdin、结果打 stdout(log 走 stderr,互不搅和)。未来的 Web
控制台(M2)复用 app 上同一组方法,这里只是最薄的壳。
"""

from __future__ import annotations

import asyncio
import logging
import sys

log = logging.getLogger(__name__)

HELP = """\
可用命令:
  state           当前状态(模式/阶段/OSC/成本)
  stop            立刻停止所有动作(清零所有轴)
  panic           急停:停动作 + 打断说话 + Avatar 安全模式
  mute on|off     开/关麦克风(OSC Voice)
  say <文字>       让 Anima 直接说一句(测试 TTS 链路)
  cost            今日成本
  budget reset    预算清零(解除熔断)
  memory          记忆库位置与索引行数
  help            本帮助
  quit            退出 Anima
"""


class Console:
    """app 需要提供:status_text() / stop_actions() / panic() / set_mute(bool)
    / say(text) / cost / memory(可为 None)/ request_shutdown()。"""

    def __init__(self, app):
        self._app = app

    async def run(self) -> None:
        try:
            reader = await self._stdin_reader()
        except (OSError, ValueError) as e:
            log.info("stdin 不可用(%s),控制台停用", e)
            return
        print("Anima 控制台就绪,输入 help 看命令。", flush=True)
        while True:
            raw = await reader.readline()
            if not raw:  # EOF(管道关闭)
                log.info("stdin EOF,控制台退出")
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                if await self._dispatch(line):
                    return
            except Exception as e:
                print(f"命令执行失败:{e}", flush=True)

    async def _dispatch(self, line: str) -> bool:
        """返回 True 表示退出。"""
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()
        app = self._app

        if cmd in ("help", "?", "帮助"):
            print(HELP, flush=True)
        elif cmd in ("state", "状态"):
            print(app.status_text(), flush=True)
        elif cmd in ("stop", "停"):
            await app.stop_actions()
            print("已停止所有动作。", flush=True)
        elif cmd in ("panic", "急停"):
            await app.panic()
            print("已急停:动作清零、说话打断、Avatar 安全模式。", flush=True)
        elif cmd in ("mute", "麦"):
            if rest not in ("on", "off"):
                print("用法:mute on(静音)| mute off(开麦)", flush=True)
            else:
                app.set_mute(rest == "on")
                print("已静音。" if rest == "on" else "已开麦。", flush=True)
        elif cmd in ("say", "说"):
            if not rest:
                print("用法:say <要说的话>", flush=True)
            else:
                await app.say(rest)
        elif cmd in ("cost", "成本"):
            print(app.cost.summary(), flush=True)
        elif cmd == "budget" and rest == "reset":
            app.cost.reset()
            print("预算已清零,熔断解除。", flush=True)
        elif cmd in ("memory", "记忆"):
            if app.memory is None:
                print("记忆功能未启用。", flush=True)
            else:
                index = await app.memory.index_text()
                lines = len(index.splitlines()) if index else 0
                print(
                    f"记忆目录:{app.memory.scope_dir}(索引 {lines} 行)",
                    flush=True,
                )
        elif cmd in ("quit", "exit", "退出"):
            print("正在退出……", flush=True)
            app.request_shutdown()
            return True
        else:
            print(f"未知命令:{cmd}(help 看帮助)", flush=True)
        return False

    @staticmethod
    async def _stdin_reader() -> asyncio.StreamReader:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        return reader
