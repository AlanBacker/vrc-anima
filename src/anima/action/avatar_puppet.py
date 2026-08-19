"""参数木偶(路线二,docs/avatar-puppet.md):经 Avatar 参数驱动躯干/双臂。

Avatar 里的 Puppet 动画层把 6 个语义轴(float,-1~1)混合成姿势,
本模块就是拉线的手:往 /avatar/parameters/Puppet/* 发参数值。

- 接管协议:开始动作时先把所有轴放回自然位、再发 Puppet/On=1(免得
  上次断开时残留的参数值闪现);动作播完淡回自然位,收敛后发 On=0
  把身体还给游戏 IK,流送自然结束。
- 铁律与 trackers 层同款:一切目标切换经指数平滑,永不发跳变(本地
  画面是参数直驱姿势,跳变=瞬移);panic 立即断开(On=0 后游戏侧
  有 0.25 秒淡出,视觉无害)。
- On 用 float 1/0 不用 bool:实测(2026-08-20)OSC 拨 Bool 参数在
  部分环境不生效,Float 稳;Unity 侧过渡条件用 Greater/Less 0.5。
- 手臂刻度:muscle 0 = T 姿平举,所以轴 0=平举、-1=垂下、+1=举过头,
  自然位是 -1(与 Expression Parameters 默认值一致)。

预置动作是"参数化生成器"(连续函数)而非动画片段,遵守"不要固定
预设动作"的裁定;pose() 则让大脑自由组合任意姿势。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Callable

log = logging.getLogger(__name__)

PREFIX = "/avatar/parameters/Puppet/"
ON_ADDR = PREFIX + "On"

AXES = ("LeanX", "LeanZ", "ArmL_Up", "ArmR_Up", "ArmL_Fwd", "ArmR_Fwd")

# 自然位:躯干直立、双臂垂下(ArmUp 轴 0=T 姿平举,垂手在 -1)
REST = {
    "LeanX": 0.0,
    "LeanZ": 0.0,
    "ArmL_Up": -1.0,
    "ArmR_Up": -1.0,
    "ArmL_Fwd": 0.0,
    "ArmR_Fwd": 0.0,
}

# 轴名宽容拼法(pose 入参用):snake / 全小写都认
_AXIS_LOOKUP = {a.lower(): a for a in AXES} | {
    "lean_x": "LeanX",
    "lean_z": "LeanZ",
    "arm_l_up": "ArmL_Up",
    "arm_r_up": "ArmR_Up",
    "arm_l_fwd": "ArmL_Fwd",
    "arm_r_fwd": "ArmR_Fwd",
}

SMOOTH_TAU_S = 0.18     # 指数平滑时间常数(所有姿态过渡的缓冲)
RATE_HZ = 20.0          # 参数流送频率(本地观感的插值密度)
MAX_MOTION_S = 60.0     # 单段动作时长封顶(安全绳:任何姿势都有截止时间)
DEFAULT_MOTION_S = 6.0
DEFAULT_POSE_S = 8.0
CONVERGE_EPS = 0.005    # 与自然位差距小于此视为已收势
SEND_EPS = 0.001        # 值没变就不重发(参数是状态不是事件)


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, float(v)))


# ---------------------------------------------------------------- 生成器
# 签名:fn(片段内秒数) -> {轴: 绝对目标值}。没给的轴回自然位。


def _gen_sway(t: float) -> dict[str, float]:
    """躯干左右摇摆(听歌打拍子)。"""
    return {"LeanX": 0.35 * math.sin(2 * math.pi * 0.4 * t)}


def _gen_wave(t: float) -> dict[str, float]:
    """右臂举起前后摆——挥手打招呼。
    摆动幅度按平滑低通的衰减补偿过(τ=0.18 时 1.1Hz 增益约 0.66)。"""
    return {
        "ArmR_Up": 0.55,
        "ArmR_Fwd": 0.45 * math.sin(2 * math.pi * 1.1 * t),
    }


def _gen_cheer(t: float) -> dict[str, float]:
    """双臂高举小幅挥动——欢呼/万岁。"""
    up = 0.85 + 0.10 * math.sin(2 * math.pi * 1.3 * t)
    return {"ArmL_Up": up, "ArmR_Up": up, "LeanZ": -0.10}


def _gen_stretch(t: float) -> dict[str, float]:
    """伸懒腰:双臂缓缓举过头 + 轻微后仰。"""
    ramp = min(t / 1.5, 1.0)
    up = -1.0 + 2.0 * ramp
    return {"ArmL_Up": up, "ArmR_Up": up, "LeanZ": -0.25 * ramp}


GENERATORS: dict[str, Callable[[float], dict[str, float]]] = {
    "wave": _gen_wave,
    "sway": _gen_sway,
    "cheer": _gen_cheer,
    "stretch": _gen_stretch,
}

GEN_LABELS = {"wave": "挥手", "sway": "摇摆", "cheer": "欢呼", "stretch": "伸懒腰"}

DEFAULT_SECONDS = {"wave": 4.0, "sway": 8.0, "cheer": 5.0, "stretch": 6.0}


# ---------------------------------------------------------------- 驱动器


class AvatarPuppet:
    """参数流送任务 + 平滑器 + 当前动作。send 可注入用于测试。"""

    def __init__(
        self,
        send: Callable[[str, object], None],
        rate_hz: float = RATE_HZ,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._send = send
        self.rate_hz = float(rate_hz)
        self._now = monotonic
        self._task: asyncio.Task | None = None
        self._values: dict[str, float] | None = None  # 平滑器状态(None=未接管)
        self._last_sent: dict[str, float] = {}
        self._motion: Callable[[float], dict[str, float]] | None = None
        self._motion_name = ""
        self._motion_t0 = 0.0
        self._motion_deadline = 0.0

    # ------------------------------------------------------------ 动作入口

    def play(self, name: str, seconds: float | None = None) -> float:
        """播放一个预置动作;返回实际时长(到点自动收势)。"""
        fn = GENERATORS.get(name)
        if fn is None:
            raise ValueError(
                f"未知木偶动作:{name}(可用:{'、'.join(GENERATORS)})"
            )
        if seconds is None:
            seconds = DEFAULT_SECONDS.get(name, DEFAULT_MOTION_S)
        return self._begin(fn, name, seconds)

    def pose(self, targets: dict[str, float], seconds: float | None = None) -> float:
        """摆一个自由姿势并保持;轴名认 LeanX / lean_x 两种拼法。"""
        fixed: dict[str, float] = {}
        bad: list[str] = []
        for key, value in targets.items():
            axis = _AXIS_LOOKUP.get(str(key).strip().lower().replace("-", "_"))
            if axis is None:
                bad.append(str(key))
            else:
                fixed[axis] = _clamp(value)
        if bad:
            raise ValueError(
                f"未知姿势轴:{'、'.join(bad)}(可用:{'、'.join(AXES)})"
            )
        if not fixed:
            raise ValueError("姿势至少要给一个轴")
        if seconds is None:
            seconds = DEFAULT_POSE_S
        return self._begin(lambda t: fixed, "pose", seconds)

    def _begin(self, fn, name: str, seconds: float) -> float:
        seconds = max(0.5, min(float(seconds), MAX_MOTION_S))
        self._motion, self._motion_name = fn, name
        self._motion_t0 = self._now()
        self._motion_deadline = self._motion_t0 + seconds
        if not self.active:
            self._task = asyncio.create_task(self._loop(), name="avatar-puppet")
        return seconds

    def rest(self) -> None:
        """收势:中止当前动作,淡回自然位,收敛后自动关闸(On=0)。"""
        if self._motion is not None:
            log.info("木偶动作 %s 中止,收势", self._motion_name)
        self._motion = None

    def panic_off(self) -> None:
        """急停:立即断开(On=0;游戏侧过渡自带 0.25 秒淡出,视觉无害)。"""
        self._motion = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._values = None
        self._last_sent = {}
        self._send(ON_ADDR, 0.0)

    async def shutdown(self) -> None:
        task = self._task
        self.panic_off()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------ 每拍

    def tick(self, dt: float) -> list[tuple[str, float]]:
        """推进一拍:目标 → 平滑 → 待发消息(独立成方法便于测试)。"""
        now = self._now()
        if self._motion is not None and now >= self._motion_deadline:
            log.info("木偶动作 %s 播完,收势回自然位", self._motion_name)
            self._motion = None
        target = dict(REST)
        if self._motion is not None:
            for axis, v in self._motion(now - self._motion_t0).items():
                target[axis] = _clamp(v)
        if self._values is None:
            self._values = dict(REST)  # 从自然位起步:重新接管永不跳变
        alpha = 1.0 - math.exp(-dt / SMOOTH_TAU_S) if dt > 0 else 0.0
        msgs: list[tuple[str, float]] = []
        for axis in AXES:
            v = self._values[axis] + (target[axis] - self._values[axis]) * alpha
            self._values[axis] = v
            if abs(v - self._last_sent.get(axis, math.inf)) > SEND_EPS:
                self._last_sent[axis] = v
                msgs.append((PREFIX + axis, v))
        return msgs

    def _converged(self) -> bool:
        if self._motion is not None or self._values is None:
            return False
        return all(abs(self._values[a] - REST[a]) <= CONVERGE_EPS for a in AXES)

    async def _loop(self) -> None:
        log.info("木偶接管:Puppet/On=1(参数流送 %.0fHz)", self.rate_hz)
        # 开闸前先把所有轴放回自然位:上次断开时残留的参数值不会闪现
        for axis in AXES:
            self._send(PREFIX + axis, REST[axis])
        self._values = dict(REST)
        self._last_sent = dict(REST)
        self._send(ON_ADDR, 1.0)
        last = self._now()
        try:
            while True:
                await asyncio.sleep(1.0 / max(1.0, self.rate_hz))
                now = self._now()
                dt, last = now - last, now
                for addr, v in self.tick(dt):
                    self._send(addr, v)
                if self._converged():
                    break
        finally:
            # 收势:轴精确归位(差距 ≤ 收敛阈,视觉无感)再关闸
            for axis in AXES:
                self._send(PREFIX + axis, REST[axis])
            self._send(ON_ADDR, 0.0)
            self._values = None
            self._last_sent = {}
            log.info("木偶收势:Puppet/On=0,身体还给游戏")

    # ------------------------------------------------------------ 状态

    def status_text(self) -> str:
        moves = "、".join(f"{n} {GEN_LABELS[n]}" for n in GENERATORS)
        if not self.active:
            state = "闲置(身体在游戏手里)"
        elif self._motion is not None:
            left = max(0.0, self._motion_deadline - self._now())
            name = GEN_LABELS.get(self._motion_name, self._motion_name)
            state = f"接管中,动作 {name}(剩 {left:.0f} 秒)"
        else:
            state = "接管中(收势回自然位)"
        lines = [f"木偶(参数,{self.rate_hz:g}Hz):{state}"]
        if self._values is not None:
            lines.append(
                "轴:" + " ".join(f"{a}={self._values[a]:+.2f}" for a in AXES)
            )
        lines.append(f"预置动作:{moves};自由姿势:puppet pose 轴=值 …")
        return "\n".join(lines)
