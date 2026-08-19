"""木偶层(实验):经 VRChat OSC Trackers 协议流送程序化身体姿态。

VRChat 官方支持从外部程序喂全身追踪数据(/tracking/trackers/1..8 与
head)——本来是给 mocopi/SlimeVR 这类动捕硬件的,但数据是谁算出来的
它不管。本模块用它做"非预设"的躯干/下肢动作:

- 追踪点:head(坐标空间对齐参考)+ 1=髋 + 2=左脚 + 3=右脚。官方
  文档明说"点越少 IK 补偿越好",髋+双脚是甜点位,膝/肘/胸按需再加。
- 坐标系:Unity 左手系,+Y 向上,1.0=1 米;旋转为欧拉角(度),
  VRChat 按 Z→X→Y 顺序应用。
- 铁律:姿态流必须连续——一切目标切换都经指数平滑,动作播完淡回
  中立位,任何情况下不发跳变(IK 吃到瞬移会抽风);panic 立即断流。
- 桌面模式接收 OSC Trackers 是社区实证、官方未文档化 ⚠️——控制台
  puppet 命令就是实机验证入口(DESIGN.md §12)。

接口位:play_frames() 收 PoseFrame 序列,是未来 text-to-motion
provider(文本→骨骼动作模型)的入场口——模型输出经"关节→追踪点"
转换后从这里进,与内置程序化生成器(sway/bob)走同一条平滑通道。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

TRACKER_IDS = ("head", "1", "2", "3")  # head=对齐参考,1=髋,2=左脚,3=右脚

SMOOTH_TAU_S = 0.12      # 指数平滑时间常数:所有姿态过渡的缓冲
MAX_MOTION_S = 60.0      # 单段动作时长封顶(安全绳,与 move 同哲学)
DEFAULT_MOTION_S = 8.0
CONVERGE_EPS = 2e-3      # 与中立位差距小于此视为已回位(米/度同阈)

# 6 元组:x, y, z(米)+ rx, ry, rz(欧拉角,度)
Pose = dict[str, tuple[float, float, float, float, float, float]]


def neutral_pose(height_m: float) -> Pose:
    """站立中立位。比例取人体测量常用值:头≈0.94H,髋≈0.54H。"""
    h = height_m
    return {
        "head": (0.0, 0.94 * h, 0.0, 0.0, 0.0, 0.0),
        "1": (0.0, 0.54 * h, 0.0, 0.0, 0.0, 0.0),
        "2": (-0.10, 0.05, 0.0, 0.0, 0.0, 0.0),
        "3": (0.10, 0.05, 0.0, 0.0, 0.0, 0.0),
    }


# ---------------------------------------------------------------- 生成器
# 签名:fn(片段内秒数, 中立位) -> 目标姿态。全是连续参数函数,不是动画片段。


def _gen_sway(t: float, base: Pose) -> Pose:
    """髋部左右摇摆 + 轻微侧倾,双脚踩住不动。"""
    p = math.sin(2 * math.pi * 0.45 * t)
    x, y, z, rx, ry, rz = base["1"]
    out = dict(base)
    out["1"] = (x + 0.08 * p, y, z, rx, ry, rz - 7.0 * p)
    return out


def _gen_bob(t: float, base: Pose) -> Pose:
    """上下律动(蹲弹打拍子)。"""
    dip = 0.03 * (1.0 - math.cos(2 * math.pi * 1.4 * t))  # 0..0.06 米
    x, y, z, rx, ry, rz = base["1"]
    out = dict(base)
    out["1"] = (x, y - dip, z, rx, ry, rz)
    return out


GENERATORS: dict[str, Callable[[float, Pose], Pose]] = {
    "sway": _gen_sway,
    "bob": _gen_bob,
}


@dataclass
class PoseFrame:
    """一帧目标姿态(text-to-motion provider 的入场格式)。

    t 为相对片段起点的秒数,须递增;trackers 只给想控制的点,缺的
    回落中立位。"""

    t: float
    trackers: Pose


def _frames_motion(frames: list[PoseFrame]) -> Callable[[float, Pose], Pose]:
    """关键帧序列 → 采样函数(相邻帧线性插值,首尾帧外保持)。"""
    frames = sorted(frames, key=lambda f: f.t)

    def sample(t: float, base: Pose) -> Pose:
        lo = hi = frames[0] if t <= frames[0].t else frames[-1]
        alpha = 0.0
        if frames[0].t < t < frames[-1].t:
            for i in range(len(frames) - 1):
                if frames[i].t <= t <= frames[i + 1].t:
                    lo, hi = frames[i], frames[i + 1]
                    span = hi.t - lo.t
                    alpha = (t - lo.t) / span if span > 0 else 0.0
                    break
        out = dict(base)
        for tid in TRACKER_IDS:
            a = lo.trackers.get(tid, base[tid])
            b = hi.trackers.get(tid, base[tid])
            out[tid] = tuple(x + (y - x) * alpha for x, y in zip(a, b))
        return out

    return sample


# ---------------------------------------------------------------- 驱动器


class PuppetDriver:
    """流送任务 + 平滑器 + 当前动作。

    所有公开方法可在任意时刻调用:目标怎么切,发出去的姿态都经
    指数平滑过渡,永不跳变。send 可注入用于测试。"""

    def __init__(
        self,
        send: Callable[[str, object], None],
        height_m: float = 1.60,
        rate_hz: float = 50.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._send = send
        self.height_m = float(height_m)
        self.rate_hz = float(rate_hz)
        self._now = monotonic
        self._task: asyncio.Task | None = None
        self._pose: Pose | None = None  # 平滑器状态 = 实际发出的姿态
        self._motion: Callable[[float, Pose], Pose] | None = None
        self._motion_name = ""
        self._motion_t0 = 0.0
        self._motion_deadline = 0.0
        self._stop_requested = False

    # ------------------------------------------------------------ 流送控制

    @property
    def streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """开始流送(初始即中立位)。已在流送则无操作。"""
        if self.streaming:
            return
        self._stop_requested = False
        self._task = asyncio.create_task(self._loop(), name="puppet")

    def request_stop(self) -> None:
        """请求停止:淡回中立位,收敛后流送自然结束(不瞬移)。"""
        self.calm()
        self._stop_requested = True

    def panic_off(self) -> None:
        """急停:立即断流(panic 语义优先于平滑)。"""
        self._motion = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._pose = None

    async def shutdown(self) -> None:
        task = self._task
        self.panic_off()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def calm(self) -> None:
        """中止当前动作、目标回中立位,流送保持(stop 命令语义)。"""
        if self._motion is not None:
            log.info("木偶动作 %s 中止,回中立位", self._motion_name)
        self._motion = None

    # ------------------------------------------------------------ 动作

    def play(self, name: str, seconds: float = DEFAULT_MOTION_S) -> float:
        """播放一个程序化生成器动作;未在流送则自动开始。返回实际时长。"""
        fn = GENERATORS.get(name)
        if fn is None:
            raise ValueError(
                f"未知木偶动作:{name}(可用:{'、'.join(GENERATORS)})"
            )
        return self._begin(fn, name, seconds)

    def play_frames(self, frames: list[PoseFrame], name: str = "frames") -> float:
        """播放关键帧序列——text-to-motion provider 的入场口。"""
        if not frames:
            raise ValueError("空的姿态帧序列")
        return self._begin(_frames_motion(frames), name, frames[-1].t)

    def _begin(self, fn, name: str, seconds: float) -> float:
        seconds = max(0.5, min(float(seconds), MAX_MOTION_S))
        self._motion, self._motion_name = fn, name
        self._motion_t0 = self._now()
        self._motion_deadline = self._motion_t0 + seconds
        self.start()
        return seconds

    # ------------------------------------------------------------ 每拍

    def tick(self, dt: float) -> list[tuple[str, list[float]]]:
        """推进一拍:目标姿态 → 平滑 → 待发消息(独立成方法便于测试)。"""
        base = neutral_pose(self.height_m)
        now = self._now()
        if self._motion is not None and now >= self._motion_deadline:
            log.info("木偶动作 %s 播完,回中立位", self._motion_name)
            self._motion = None
        if self._motion is None:
            target = base
        else:
            target = self._motion(now - self._motion_t0, base)
        if self._pose is None:
            self._pose = dict(target)
        else:
            alpha = 1.0 - math.exp(-dt / SMOOTH_TAU_S) if dt > 0 else 0.0
            self._pose = {
                tid: tuple(
                    c + (t - c) * alpha
                    for c, t in zip(
                        self._pose.get(tid, base[tid]), target.get(tid, base[tid])
                    )
                )
                for tid in TRACKER_IDS
            }
        msgs: list[tuple[str, list[float]]] = []
        for tid, v in self._pose.items():
            msgs.append((f"/tracking/trackers/{tid}/position", [v[0], v[1], v[2]]))
            msgs.append((f"/tracking/trackers/{tid}/rotation", [v[3], v[4], v[5]]))
        return msgs

    def _converged(self) -> bool:
        if self._motion is not None or self._pose is None:
            return False
        base = neutral_pose(self.height_m)
        return all(
            abs(c - t) <= CONVERGE_EPS
            for tid in TRACKER_IDS
            for c, t in zip(self._pose[tid], base[tid])
        )

    async def _loop(self) -> None:
        log.info(
            "木偶流送开始(%.0fHz,身高 %.2fm)。数据没被游戏吃就检查:"
            "OSC 已开、游戏内已 Calibrate FBT、身高与游戏内设置一致",
            self.rate_hz, self.height_m,
        )
        last = self._now()
        try:
            while True:
                await asyncio.sleep(1.0 / max(1.0, self.rate_hz))
                now = self._now()
                dt, last = now - last, now
                for addr, vals in self.tick(dt):
                    self._send(addr, vals)
                if self._stop_requested and self._converged():
                    break
        finally:
            self._pose = None
            log.info("木偶流送停止")

    # ------------------------------------------------------------ 状态

    def status_text(self) -> str:
        if not self.streaming:
            state = "停"
        elif self._motion is not None:
            left = max(0.0, self._motion_deadline - self._now())
            state = f"流送中,动作 {self._motion_name}(剩 {left:.0f} 秒)"
        else:
            state = "流送中(中立位)"
        return (
            f"木偶(OSC Trackers 实验):{state}\n"
            f"追踪点:head+髋+双脚 | 频率 {self.rate_hz:g}Hz | 身高 {self.height_m:g}m\n"
            "实机验证:OSC 开着 → puppet on → 游戏内快捷菜单 Calibrate FBT(T 姿)\n"
            "→ puppet sway 看角色扭不扭;身高须与游戏内 User Real Height 一致"
        )
