"""参数木偶:自然位、接管协议、平滑有界、动作过期、姿势校验、生命周期。"""

import asyncio

import pytest

from anima.action.avatar_puppet import (
    AXES,
    GENERATORS,
    ON_ADDR,
    PREFIX,
    REST,
    AvatarPuppet,
)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _puppet(rate=20.0):
    clock = _Clock()
    sent = []
    pup = AvatarPuppet(
        lambda addr, v: sent.append((addr, v)), rate_hz=rate, monotonic=clock
    )
    return pup, sent, clock


def test_rest_pose_arms_down():
    """自然位:躯干归零,手臂轴 -1(muscle 0 是 T 姿平举,垂手在 -1)。"""
    assert REST["ArmL_Up"] == REST["ArmR_Up"] == -1.0
    assert REST["LeanX"] == REST["LeanZ"] == 0.0
    assert set(REST) == set(AXES)


def test_first_tick_emits_rest_values_with_prefix():
    pup, _, _ = _puppet()
    msgs = dict(pup.tick(0.05))
    assert set(msgs) == {PREFIX + a for a in AXES}
    for axis in AXES:
        assert msgs[PREFIX + axis] == pytest.approx(REST[axis])


async def test_pose_smooths_toward_target_without_jumps():
    pup, _, clock = _puppet()
    pup.tick(0.05)
    pup.pose({"arm_r_up": 1.0, "lean_x": 0.5}, 30)
    prev_up = pup._values["ArmR_Up"]
    for _ in range(60):  # 3 秒 @20Hz
        clock.t += 0.05
        pup.tick(0.05)
        up = pup._values["ArmR_Up"]
        assert prev_up - 1e-9 <= up <= 1.0  # 单调逼近、不过冲
        assert abs(up - prev_up) < 0.55     # 相邻拍步长有界
        prev_up = up
    assert pup._values["ArmR_Up"] == pytest.approx(1.0, abs=0.01)
    assert pup._values["LeanX"] == pytest.approx(0.5, abs=0.01)
    assert pup._values["ArmL_Up"] == pytest.approx(-1.0, abs=0.01)  # 没给的轴留在自然位
    await pup.shutdown()


async def test_pose_normalizes_axis_names_clamps_and_rejects():
    pup, _, _ = _puppet()
    assert pup.pose({"LeanX": 5.0}, 1) == 1.0  # 精确拼法 + 越界截到 1
    pup.tick(0.05)
    with pytest.raises(ValueError, match="未知姿势轴"):
        pup.pose({"leg_l": 1.0})
    with pytest.raises(ValueError, match="至少要给一个轴"):
        pup.pose({})
    await pup.shutdown()


async def test_play_defaults_clamps_and_rejects_unknown():
    pup, _, _ = _puppet()
    assert pup.play("wave") == 4.0     # 各动作默认时长
    assert pup.play("sway", 999) == 60.0
    assert pup.play("sway", 0.01) == 0.5
    with pytest.raises(ValueError, match="可用"):
        pup.play("moonwalk")
    await pup.shutdown()


def test_generators_stay_within_axis_range():
    for name, fn in GENERATORS.items():
        t = 0.0
        while t < 8.0:
            for axis, v in fn(t).items():
                assert axis in AXES, f"{name} 用了未知轴 {axis}"
                assert -1.0 <= v <= 1.0, f"{name}@{t:.2f}s {axis}={v}"
            t += 0.05


async def test_motion_expires_back_to_rest_and_converges():
    pup, _, clock = _puppet()
    pup.tick(0.05)
    pup.pose({"arm_l_up": 0.8}, 2.0)
    clock.t = 1.0
    pup.tick(0.05)
    assert pup._motion is not None
    clock.t = 2.5  # 过了 2 秒时限
    pup.tick(0.05)
    assert pup._motion is None
    for _ in range(200):
        pup.tick(0.05)
    assert pup._converged()
    await pup.shutdown()


async def test_stream_engages_then_disengages_cleanly():
    """接管顺序:轴归位 → On=1;收势后:轴归位 → On=0,任务自然退出。"""
    pup, sent, clock = _puppet(rate=100.0)
    pup.pose({"lean_x": 0.3}, 0.5)
    assert pup.active
    await asyncio.sleep(0.03)  # 让 _loop 起跑,发出接管序列
    on_idx = sent.index((ON_ADDR, 1.0))
    before = {a for a, _ in sent[:on_idx]}
    assert before == {PREFIX + a for a in AXES}  # On 之前所有轴已归自然位
    for _ in range(200):
        await asyncio.sleep(0.01)
        clock.t += 0.05
        if not pup.active:
            break
    assert not pup.active
    assert sent[-1] == (ON_ADDR, 0.0)  # 收势最后一条是关闸
    tail = dict(sent[-7:-1])
    for axis in AXES:  # 关闸前轴精确归位
        assert tail[PREFIX + axis] == pytest.approx(REST[axis])


async def test_rest_clears_motion_but_lets_stream_finish():
    pup, _, _ = _puppet()
    pup.play("sway")
    pup.rest()
    assert pup._motion is None
    assert pup.active  # 流送还在,等收敛后自己关
    await pup.shutdown()


async def test_panic_off_sends_off_immediately():
    pup, sent, _ = _puppet()
    pup.play("cheer")
    assert pup.active
    pup.panic_off()
    assert not pup.active
    assert (ON_ADDR, 0.0) in sent
    await pup.shutdown()  # 幂等


async def test_status_text_reflects_state():
    pup, _, _ = _puppet()
    assert "闲置" in pup.status_text()
    pup.play("wave", 8)
    assert "挥手" in pup.status_text()
    await pup.shutdown()
    assert "闲置" in pup.status_text()
