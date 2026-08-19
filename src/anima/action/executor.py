"""动作执行器:工具调用 → OSC 输入,立即返回、后台执行、可抢占。

语义(DESIGN.md Q13):
- dispatch() 同步返回中文回执(functionResponse 的内容),动作丢进
  asyncio 任务后台跑——模型不用等腿走完就能继续说话;
- 同组动作互相抢占(新 move 打断旧 move),抢占先等旧任务的 finally
  清完轴再起新任务,不留脏轴;
- 每个动作任务自己 try/finally 清轴,OscMotor 看门狗是第二道防线;
- snapshot 只返回哨兵,抓帧和追问由 app 层做(执行器不碰屏幕)。

模型给的参数是不可信输入:全部走 float()/查表校验,坏参数返回中文
error 回执而不是抛异常。
"""

from __future__ import annotations

import asyncio
import logging

from ..brain.base import ToolCall
from ..config import CalibrationConfig, EmoteDef
from ..osc.client import OscMotor
from .avatar_puppet import AvatarPuppet

log = logging.getLogger(__name__)

SNAPSHOT_SENTINEL = "__snapshot__"

_MOVE_AXES = {
    "forward": ("Vertical", 1.0, "向前"),
    "back": ("Vertical", -1.0, "向后"),
    "left": ("Horizontal", -1.0, "向左"),
    "right": ("Horizontal", 1.0, "向右"),
}

MIN_MOVE_S = 0.2


class ActionExecutor:
    def __init__(
        self,
        motor: OscMotor,
        calibration: CalibrationConfig,
        emotes: dict[str, EmoteDef],
        puppet: AvatarPuppet | None = None,
    ):
        self._motor = motor
        self._cal = calibration
        self._emotes = emotes
        self._puppet = puppet
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ 调度

    def dispatch(self, call: ToolCall) -> dict:
        handler = getattr(self, f"_t_{call.name}", None)
        if handler is None:
            return {"status": "error", "detail": f"未知工具:{call.name}"}
        try:
            return handler(**call.args)
        except (TypeError, ValueError, KeyError) as e:
            log.info("工具参数不合法 %s(%r):%s", call.name, call.args, e)
            return {"status": "error", "detail": f"参数不合法:{e}"}

    def _schedule(self, group: str, coro) -> None:
        """同组抢占:先等旧任务(及其 finally 清轴)结束,再跑新动作。"""
        old = self._tasks.get(group)

        async def runner():
            if old is not None and not old.done():
                old.cancel()
                try:
                    await old
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("动作任务 %s 异常:%s", group, e)

        self._tasks[group] = asyncio.create_task(runner(), name=f"action-{group}")

    async def stop_everything(self) -> None:
        """取消所有动作任务并清零(console/panic 用)。"""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._motor.zero_all()
        if self._puppet is not None:
            self._puppet.rest()

    # ------------------------------------------------------------ 工具实现

    def _t_move(self, direction: str, seconds: float) -> dict:
        if direction not in _MOVE_AXES:
            raise ValueError(f"direction 必须是 {sorted(_MOVE_AXES)} 之一")
        seconds = max(MIN_MOVE_S, min(float(seconds), self._cal.move_max_seconds))
        axis, value, label = _MOVE_AXES[direction]

        async def run():
            self._motor.set_axis(axis, value, hold_s=seconds)
            try:
                await asyncio.sleep(seconds)
            finally:
                self._motor.clear_axis(axis)

        self._schedule("move", run())
        return {"status": "ok", "detail": f"已开始{label}移动 {seconds:.1f} 秒"}

    def _t_turn(self, degrees: float) -> dict:
        degrees = float(degrees)
        if degrees == 0:
            return {"status": "ok", "detail": "转 0 度,原地没动"}
        degrees = max(-720.0, min(720.0, degrees))
        seconds = abs(degrees) / self._cal.turn_deg_per_sec
        value = 1.0 if degrees > 0 else -1.0
        label = "右" if degrees > 0 else "左"

        async def run():
            self._motor.set_axis("LookHorizontal", value, hold_s=seconds)
            try:
                await asyncio.sleep(seconds)
            finally:
                self._motor.clear_axis("LookHorizontal")

        self._schedule("turn", run())
        return {
            "status": "ok",
            "detail": f"已开始向{label}转约 {abs(degrees):.0f} 度({seconds:.1f} 秒)",
        }

    def _t_look_pitch(self, degrees: float) -> dict:
        if not self._cal.enable_look_pitch:
            return {"status": "error", "detail": "look_pitch 已在配置中禁用"}
        degrees = max(-90.0, min(90.0, float(degrees)))
        if degrees == 0:
            return {"status": "ok", "detail": "视线没动"}
        seconds = abs(degrees) / self._cal.look_deg_per_sec
        # 校准假设:LookVertical +1 = 抬头。实机验证不对就改这里的符号。
        value = 1.0 if degrees > 0 else -1.0
        label = "抬头" if degrees > 0 else "低头"

        async def run():
            self._motor.set_axis("LookVertical", value, hold_s=seconds)
            try:
                await asyncio.sleep(seconds)
            finally:
                self._motor.clear_axis("LookVertical")

        self._schedule("pitch", run())
        return {"status": "ok", "detail": f"已开始{label}约 {abs(degrees):.0f} 度"}

    def _t_jump(self) -> dict:
        self._schedule("jump", self._motor.pulse("Jump", ms=80))
        return {"status": "ok", "detail": "跳了一下"}

    def _t_set_run(self, on: bool) -> dict:
        self._motor.set_run(bool(on))
        return {"status": "ok", "detail": "奔跑已开启" if on else "已恢复步行"}

    def _t_emote(self, name: str) -> dict:
        emote = self._emotes.get(name)
        if emote is None:
            available = "、".join(sorted(self._emotes)) or "(一个都没配)"
            return {"status": "error", "detail": f"没有这个表情,可用:{available}"}

        async def run():
            self._motor.send(emote.address, emote.value)
            if emote.hold_ms > 0:
                try:
                    await asyncio.sleep(emote.hold_ms / 1000)
                finally:
                    if emote.reset_value is not None:
                        self._motor.send(emote.address, emote.reset_value)
            elif emote.reset_value is not None:
                self._motor.send(emote.address, emote.reset_value)

        self._schedule("emote", run())
        return {"status": "ok", "detail": f"播放表情:{name}"}

    def _t_motion(
        self,
        move: str | None = None,
        seconds: float | None = None,
        lean_x: float | None = None,
        lean_z: float | None = None,
        arm_l_up: float | None = None,
        arm_r_up: float | None = None,
        arm_l_fwd: float | None = None,
        arm_r_fwd: float | None = None,
    ) -> dict:
        if self._puppet is None:
            return {"status": "error", "detail": "木偶层未启用"}
        given = {
            "LeanX": lean_x,
            "LeanZ": lean_z,
            "ArmL_Up": arm_l_up,
            "ArmR_Up": arm_r_up,
            "ArmL_Fwd": arm_l_fwd,
            "ArmR_Fwd": arm_r_fwd,
        }
        axes = {k: float(v) for k, v in given.items() if v is not None}
        hold = None if seconds is None else float(seconds)
        if move is not None and axes:
            return {
                "status": "error",
                "detail": "move 和姿势轴一次只能用一种,分两次调用",
            }
        if move == "rest":
            self._puppet.rest()
            return {"status": "ok", "detail": "收势,回自然站姿"}
        if move is not None:
            played = self._puppet.play(str(move), hold)  # 未知名会 ValueError→回执
            return {
                "status": "ok",
                "detail": f"动作 {move} 已开始,约 {played:g} 秒后自动收势",
            }
        if axes:
            played = self._puppet.pose(axes, hold)
            return {
                "status": "ok",
                "detail": f"姿势已摆好,保持约 {played:g} 秒后自动收势",
            }
        return {"status": "error", "detail": "要么给 move 预置动作,要么给至少一个姿势轴"}

    def _t_snapshot(self) -> dict:
        # 真正抓帧在 app 层(执行器不依赖屏幕);哨兵键会在入史前剥掉
        return {
            "status": "ok",
            "detail": "已拍摄,高清画面马上给你",
            SNAPSHOT_SENTINEL: True,
        }

    def _t_stop_all(self) -> dict:
        self._schedule("stop", self.stop_everything())
        return {"status": "ok", "detail": "已停止所有动作"}
