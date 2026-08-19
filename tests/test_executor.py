"""动作执行器:立即返回、后台执行、抢占、参数校验。"""

import asyncio

import pytest

from anima.action.executor import SNAPSHOT_SENTINEL, ActionExecutor
from anima.brain.base import ToolCall
from anima.config import CalibrationConfig, EmoteDef
from anima.osc.client import OscMotor


def make_executor(sent: list, cal: CalibrationConfig | None = None, puppet=None):
    motor = OscMotor(send_fn=lambda a, v: sent.append((a, v)))
    return ActionExecutor(
        motor,
        cal or CalibrationConfig(),
        {"wave": EmoteDef(address="/avatar/parameters/Wave", value=1.0,
                          reset_value=0.0, hold_ms=10)},
        puppet=puppet,
    )


@pytest.mark.asyncio
async def test_move_sets_then_clears_axis():
    sent: list = []
    ex = make_executor(sent)
    result = ex.dispatch(ToolCall("move", {"direction": "forward", "seconds": 0.05}))
    assert result["status"] == "ok" and "向前" in result["detail"]
    await asyncio.sleep(0.3)  # 实际持续 MIN_MOVE_S=0.2 秒
    assert ("/input/Vertical", 1.0) in sent
    assert sent[-1] == ("/input/Vertical", 0.0)  # finally 清零


@pytest.mark.asyncio
async def test_move_preemption_clears_old_axis_first():
    sent: list = []
    ex = make_executor(sent)
    ex.dispatch(ToolCall("move", {"direction": "forward", "seconds": 5}))
    await asyncio.sleep(0.02)
    ex.dispatch(ToolCall("move", {"direction": "back", "seconds": 0.05}))
    await asyncio.sleep(0.35)  # 新动作实际持续 MIN_MOVE_S=0.2 秒
    # 顺序:+1(旧)→ 0(旧被抢占清零)→ -1(新)→ 0(新结束)
    vertical = [v for a, v in sent if a == "/input/Vertical"]
    assert vertical == [1.0, 0.0, -1.0, 0.0]
    await ex.stop_everything()


@pytest.mark.asyncio
async def test_turn_duration_follows_calibration():
    sent: list = []
    ex = make_executor(sent, CalibrationConfig(turn_deg_per_sec=900.0))
    result = ex.dispatch(ToolCall("turn", {"degrees": -90}))
    assert result["status"] == "ok" and "左" in result["detail"]
    await asyncio.sleep(0.2)  # 90/900 = 0.1s
    look = [v for a, v in sent if a == "/input/LookHorizontal"]
    assert look == [-1.0, 0.0]


@pytest.mark.asyncio
async def test_look_pitch_disabled():
    sent: list = []
    ex = make_executor(sent, CalibrationConfig(enable_look_pitch=False))
    result = ex.dispatch(ToolCall("look_pitch", {"degrees": 30}))
    assert result["status"] == "error" and "禁用" in result["detail"]


@pytest.mark.asyncio
async def test_emote_hold_and_reset():
    sent: list = []
    ex = make_executor(sent)
    result = ex.dispatch(ToolCall("emote", {"name": "wave"}))
    assert result["status"] == "ok"
    await asyncio.sleep(0.1)
    assert ("/avatar/parameters/Wave", 1.0) in sent
    assert sent[-1] == ("/avatar/parameters/Wave", 0.0)


def test_emote_unknown_lists_available():
    sent: list = []
    ex = make_executor(sent)
    result = ex.dispatch(ToolCall("emote", {"name": "dance"}))
    assert result["status"] == "error" and "wave" in result["detail"]


def test_bad_args_return_error_dict():
    sent: list = []
    ex = make_executor(sent)
    result = ex.dispatch(ToolCall("move", {"direction": "up", "seconds": 1}))
    assert result["status"] == "error"
    result = ex.dispatch(ToolCall("move", {"direction": "forward"}))
    assert result["status"] == "error"  # 缺 seconds
    result = ex.dispatch(ToolCall("不存在", {}))
    assert result["status"] == "error"


class _FakePuppet:
    def __init__(self):
        self.calls = []

    def play(self, name, seconds=None):
        if name not in ("wave", "sway", "cheer", "stretch"):
            raise ValueError(f"未知木偶动作:{name}")
        self.calls.append(("play", name, seconds))
        return seconds or 4.0

    def pose(self, targets, seconds=None):
        self.calls.append(("pose", targets, seconds))
        return seconds or 8.0

    def rest(self):
        self.calls.append(("rest",))


def test_motion_preset_and_rest():
    pup = _FakePuppet()
    ex = make_executor([], puppet=pup)
    result = ex.dispatch(ToolCall("motion", {"move": "wave"}))
    assert result["status"] == "ok" and "wave" in result["detail"]
    result = ex.dispatch(ToolCall("motion", {"move": "rest"}))
    assert result["status"] == "ok" and "收势" in result["detail"]
    assert pup.calls == [("play", "wave", None), ("rest",)]


def test_motion_pose_maps_axis_args():
    pup = _FakePuppet()
    ex = make_executor([], puppet=pup)
    result = ex.dispatch(
        ToolCall("motion", {"arm_r_up": 0.8, "lean_x": -0.3, "seconds": 5})
    )
    assert result["status"] == "ok" and "5 秒" in result["detail"]
    assert pup.calls == [("pose", {"ArmR_Up": 0.8, "LeanX": -0.3}, 5.0)]


def test_motion_bad_usages():
    pup = _FakePuppet()
    ex = make_executor([], puppet=pup)
    # move 和姿势轴混用 / 什么都不给 / 未知动作名,都要回中文 error 回执
    both = ex.dispatch(ToolCall("motion", {"move": "wave", "lean_x": 0.5}))
    none = ex.dispatch(ToolCall("motion", {}))
    unknown = ex.dispatch(ToolCall("motion", {"move": "moonwalk"}))
    assert both["status"] == none["status"] == unknown["status"] == "error"
    assert pup.calls == []
    without = make_executor([]).dispatch(ToolCall("motion", {"move": "wave"}))
    assert without["status"] == "error" and "未启用" in without["detail"]


@pytest.mark.asyncio
async def test_stop_everything_rests_puppet():
    pup = _FakePuppet()
    ex = make_executor([], puppet=pup)
    await ex.stop_everything()
    assert ("rest",) in pup.calls


def test_snapshot_sentinel():
    sent: list = []
    ex = make_executor(sent)
    result = ex.dispatch(ToolCall("snapshot", {}))
    assert result.pop(SNAPSHOT_SENTINEL) is True
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_stop_all_cancels_and_zeroes():
    sent: list = []
    ex = make_executor(sent)
    ex.dispatch(ToolCall("move", {"direction": "right", "seconds": 5}))
    await asyncio.sleep(0.02)
    await ex.stop_everything()
    assert sent[-1] == ("/input/Horizontal", 0.0)


@pytest.mark.asyncio
async def test_seconds_clamped_to_max():
    sent: list = []
    ex = make_executor(sent, CalibrationConfig(move_max_seconds=0.3))
    result = ex.dispatch(ToolCall("move", {"direction": "forward", "seconds": 60}))
    assert "0.3" in result["detail"]  # 60 秒被封顶到 move_max_seconds
    await asyncio.sleep(0.45)
    assert sent[-1] == ("/input/Vertical", 0.0)
