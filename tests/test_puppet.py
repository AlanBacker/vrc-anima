"""木偶层:中立位、消息格式、平滑有界、动作过期、关键帧插值、生命周期。"""

import asyncio

import pytest

from anima.action.puppet import PoseFrame, PuppetDriver, neutral_pose


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _driver(height=1.6):
    clock = _Clock()
    sent = []
    drv = PuppetDriver(
        lambda addr, vals: sent.append((addr, vals)),
        height_m=height,
        rate_hz=50.0,
        monotonic=clock,
    )
    return drv, sent, clock


def test_neutral_pose_scales_with_height():
    pose = neutral_pose(1.6)
    assert pose["head"][1] == pytest.approx(0.94 * 1.6)
    assert pose["1"][1] == pytest.approx(0.54 * 1.6)
    assert pose["2"][0] == -pose["3"][0] != 0  # 双脚左右对称
    assert pose["2"][1] == pose["3"][1] == pytest.approx(0.05)
    assert neutral_pose(2.0)["1"][1] > pose["1"][1]


def test_tick_emits_position_and_rotation_for_all_trackers():
    drv, _, _ = _driver()
    msgs = drv.tick(0.02)
    assert {a for a, _ in msgs} == {
        f"/tracking/trackers/{tid}/{kind}"
        for tid in ("head", "1", "2", "3")
        for kind in ("position", "rotation")
    }
    assert all(len(vals) == 3 for _, vals in msgs)
    # 首拍即中立位,不从原点滑过来
    assert dict(msgs)["/tracking/trackers/1/position"][1] == pytest.approx(0.864)


async def test_sway_moves_hip_smoothly_within_bounds():
    drv, _, clock = _driver()
    drv.tick(0.02)  # 平滑器就位(中立)
    drv.play("sway", 8.0)
    xs = []
    prev = drv._pose["1"][0]
    for _ in range(400):  # 8 秒 @50Hz
        clock.t += 0.02
        drv.tick(0.02)
        x = drv._pose["1"][0]
        xs.append(x)
        assert abs(x) <= 0.081            # 不超摆幅
        assert abs(x - prev) < 0.02       # 相邻拍无跳变
        assert drv._pose["1"][1] == pytest.approx(0.864)  # 髋高不变
        assert drv._pose["2"] == pytest.approx(neutral_pose(1.6)["2"])  # 脚踩住
        prev = x
    assert max(xs) > 0.03 and min(xs) < -0.03  # 真的在两侧摆
    await drv.shutdown()


async def test_motion_expires_back_to_neutral_and_converges():
    drv, _, clock = _driver()
    drv.tick(0.02)
    drv.play("sway", 2.0)
    clock.t = 1.0
    drv.tick(0.02)
    assert drv._motion is not None
    clock.t = 2.5  # 过了 2 秒时限
    drv.tick(0.02)
    assert drv._motion is None
    for _ in range(300):
        drv.tick(0.02)
    assert drv._converged()
    await drv.shutdown()


async def test_play_clamps_seconds_and_rejects_unknown():
    drv, _, _ = _driver()
    assert drv.play("bob", 999) == 60.0
    assert drv.play("bob", 0.01) == 0.5
    with pytest.raises(ValueError):
        drv.play("moonwalk")
    with pytest.raises(ValueError):
        drv.play_frames([])
    await drv.shutdown()


async def test_play_frames_interpolates_and_falls_back_to_neutral():
    drv, _, clock = _driver()
    base = neutral_pose(1.6)
    hip = base["1"]
    frames = [
        PoseFrame(0.0, {"1": hip}),
        PoseFrame(2.0, {"1": (0.2, hip[1], 0.0, 0.0, 0.0, 0.0)}),
    ]
    assert drv.play_frames(frames) == 2.0
    clock.t = 1.0  # 两帧正中间 → 目标 x = 0.1
    for _ in range(300):
        drv.tick(0.02)
    assert drv._pose["1"][0] == pytest.approx(0.1, abs=0.005)
    assert drv._pose["2"] == pytest.approx(base["2"])  # 没给的点回中立
    await drv.shutdown()


async def test_stream_sends_and_stops_gracefully():
    drv, sent, clock = _driver()
    drv.rate_hz = 100.0
    drv.start()
    assert drv.streaming
    for _ in range(5):
        await asyncio.sleep(0.02)
        clock.t += 0.02
    assert len(sent) >= 8  # 至少发满一拍(4 点 × 位置+旋转)
    drv.request_stop()
    for _ in range(100):
        await asyncio.sleep(0.02)
        clock.t += 0.02
        if not drv.streaming:
            break
    assert not drv.streaming  # 中立位已收敛 → 自然退出


async def test_panic_off_cuts_stream_immediately():
    drv, _, _ = _driver()
    drv.play("sway")
    assert drv.streaming
    drv.panic_off()
    assert not drv.streaming and drv._motion is None
    await drv.shutdown()  # 幂等


async def test_calm_clears_motion_but_keeps_streaming():
    drv, _, _ = _driver()
    drv.play("bob")
    drv.calm()
    assert drv._motion is None
    assert drv.streaming
    await drv.shutdown()


async def test_status_text_reflects_state():
    drv, _, _ = _driver()
    assert "停" in drv.status_text()
    drv.play("sway", 8)
    assert "sway" in drv.status_text()
    await drv.shutdown()
    assert "停" in drv.status_text()
