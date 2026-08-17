"""OSC 接收端:VRChat 在 9001 端口输出的自身状态。

事实约束:OSC 输出只有"自己"——Avatar 参数(Grounded、VelocityX/Y/Z、
MuteSelf、Viseme、Upright……),没有任何其他玩家或世界的数据。这里把
最新值缓存成一份快照,供执行器闭环与"状态文字"注入模型上下文。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

log = logging.getLogger(__name__)

_PARAM_PREFIX = "/avatar/parameters/"


class SelfState:
    """自身状态快照:最新参数值 + 最后更新时间。"""

    def __init__(self, monotonic=time.monotonic):
        self.params: dict[str, Any] = {}
        self.last_update: float = 0.0
        self._now = monotonic

    def update(self, address: str, value: Any) -> None:
        if address.startswith(_PARAM_PREFIX):
            self.params[address[len(_PARAM_PREFIX):]] = value
            self.last_update = self._now()

    # ------------------------------------------------------------ 便捷读

    @property
    def grounded(self) -> bool | None:
        v = self.params.get("Grounded")
        return None if v is None else bool(v)

    @property
    def muted(self) -> bool | None:
        v = self.params.get("MuteSelf")
        return None if v is None else bool(v)

    @property
    def speed(self) -> float | None:
        try:
            vx = float(self.params.get("VelocityX", 0.0))
            vy = float(self.params.get("VelocityY", 0.0))
            vz = float(self.params.get("VelocityZ", 0.0))
        except (TypeError, ValueError):
            return None
        if not self.params:
            return None
        return math.sqrt(vx * vx + vy * vy + vz * vz)

    @property
    def alive(self) -> bool:
        """最近 10 秒内有没有收到过任何输出(判断 OSC 链路是否活着)。"""
        return self._now() - self.last_update < 10.0

    def state_line(self) -> str:
        """一行中文状态,注入模型上下文用;链路没数据时如实说。"""
        if not self.params:
            return "自身状态:暂无(OSC 输出尚未收到数据)"
        parts: list[str] = []
        if self.grounded is not None:
            parts.append(f"着地={'是' if self.grounded else '否'}")
        spd = self.speed
        if spd is not None:
            parts.append(f"速度={spd:.1f}m/s")
        if self.muted is not None:
            parts.append(f"游戏内静音={'是' if self.muted else '否'}")
        return "自身状态:" + " ".join(parts) if parts else "自身状态:参数已连接"


async def start_listener(host: str, port: int, state: SelfState):
    """监听 VRChat 的 OSC 输出,返回 transport(close() 即停);
    端口被占用时降级为无状态运行(不致命),返回 None。"""
    import asyncio

    dispatcher = Dispatcher()
    dispatcher.set_default_handler(lambda addr, *args: state.update(
        addr, args[0] if len(args) == 1 else list(args)
    ))
    server = AsyncIOOSCUDPServer(
        (host, port), dispatcher, asyncio.get_running_loop()
    )
    try:
        transport, _ = await server.create_serve_endpoint()
    except OSError as e:
        log.warning("OSC 接收端口 %s:%d 打不开(%s),状态感知降级为空", host, port, e)
        return None
    log.info("OSC 状态监听中:%s:%d", host, port)
    return transport
