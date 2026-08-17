"""OSC 发送端(执行器的地基)。

VRChat OSC 输入的铁律:轴和按键是"状态"不是"事件"——置 1 不清零就
永远生效,bot 会一直走到天涯海角。因此:

1. 所有写入都经过本模块,轴值必须带截止时间(deadline);
2. 动作任务自己在 finally 里清零是第一道防线;
3. 看门狗协程每 100ms 扫一遍,过期轴强制清零,是第二道防线。

按键(int 1/0)同理:press 之后必须 release,提供 pulse 一步到位。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from pythonosc.udp_client import SimpleUDPClient

log = logging.getLogger(__name__)

# 桌面模式可用的输入轴(float −1..1)。LookVertical 文档缺失、更新日志
# 承认存在(2023.3.2)——是否生效以实机验证为准,不通就在配置里关掉。
AXES = ("Vertical", "Horizontal", "LookHorizontal", "LookVertical")

# 常用按键(int 1/0)。Use/Grab/Drop 为 VR-Only,桌面 bot 用不了,不收录。
BUTTONS = ("Jump", "Run", "Voice", "PanicButton")

# 任何轴最长持续时间的兜底(即使调用方给了更长的 deadline 也截断)
MAX_AXIS_HOLD_S = 8.0
WATCHDOG_INTERVAL_S = 0.1


class OscMotor:
    """带看门狗的 OSC 发送端。send_fn 可注入用于测试。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        send_fn: Callable[[str, object], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if send_fn is None:
            client = SimpleUDPClient(host, port)
            send_fn = client.send_message
        self._send = send_fn
        self._now = monotonic
        self._axis_value: dict[str, float] = {}
        self._axis_deadline: dict[str, float] = {}
        self._run_on = False
        self._watchdog: asyncio.Task | None = None

    # ------------------------------------------------------------ 原始发送

    def send(self, address: str, value: object) -> None:
        try:
            self._send(address, value)
        except OSError as e:  # UDP 发送几乎不失败,失败也不能拖垮主循环
            log.warning("OSC 发送失败 %s=%r:%s", address, value, e)

    # ------------------------------------------------------------ 轴

    def set_axis(self, name: str, value: float, hold_s: float) -> None:
        """设置轴值并登记截止时间;看门狗到点强制清零。"""
        if name not in AXES:
            raise ValueError(f"未知输入轴:{name}")
        value = max(-1.0, min(1.0, float(value)))
        hold_s = min(hold_s, MAX_AXIS_HOLD_S)
        self._axis_value[name] = value
        self._axis_deadline[name] = self._now() + hold_s
        self.send(f"/input/{name}", value)

    def clear_axis(self, name: str) -> None:
        if self._axis_value.get(name, 0.0) != 0.0:
            self.send(f"/input/{name}", 0.0)
        self._axis_value[name] = 0.0
        self._axis_deadline.pop(name, None)

    # ------------------------------------------------------------ 按键

    def press(self, button: str) -> None:
        self.send(f"/input/{button}", 1)

    def release(self, button: str) -> None:
        self.send(f"/input/{button}", 0)

    async def pulse(self, button: str, ms: int = 80) -> None:
        """按下→短暂保持→抬起。VRChat 需要看到 1→0 变化才会再次触发。"""
        self.press(button)
        try:
            await asyncio.sleep(ms / 1000)
        finally:
            self.release(button)

    # ------------------------------------------------------------ 常用语义

    def voice(self, on: bool) -> None:
        """麦克风开关。前提:游戏内关闭 "Toggle Voice",此时 1=开麦 0=静音。"""
        self.send("/input/Voice", 1 if on else 0)

    def set_run(self, on: bool) -> None:
        self._run_on = on
        self.send("/input/Run", 1 if on else 0)

    async def panic(self) -> None:
        """安全模式:关闭所有 Avatar 特效。仅控制台/桥可触发,不暴露给模型。"""
        self.zero_all()
        await self.pulse("PanicButton", ms=150)

    def zero_all(self) -> None:
        """所有运动输入归零(不动 Voice——静音是独立决策)。"""
        for axis in AXES:
            self.clear_axis(axis)
        if self._run_on:
            self.set_run(False)

    # ------------------------------------------------------------ 看门狗

    def start_watchdog(self) -> None:
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(
                self._watchdog_loop(), name="osc-watchdog"
            )

    async def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            try:
                await self._watchdog
            except asyncio.CancelledError:
                pass
            self._watchdog = None
        self.zero_all()

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            self.sweep()

    def sweep(self) -> None:
        """清零所有过期轴(独立成方法便于测试)。"""
        now = self._now()
        for name, deadline in list(self._axis_deadline.items()):
            if now >= deadline:
                log.debug("看门狗清零过期轴 %s", name)
                self.clear_axis(name)
