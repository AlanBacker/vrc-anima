"""OSC 发送端:轴截止时间与看门狗清零。"""

from anima.osc.client import OscMotor


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_set_axis_records_and_sends():
    sent = []
    motor = OscMotor(send_fn=lambda a, v: sent.append((a, v)))
    motor.set_axis("Vertical", 1.5, hold_s=2.0)  # 超界截到 1.0
    assert sent == [("/input/Vertical", 1.0)]


def test_watchdog_sweep_clears_expired():
    sent = []
    clock = FakeClock()
    motor = OscMotor(send_fn=lambda a, v: sent.append((a, v)), monotonic=clock)
    motor.set_axis("Vertical", 1.0, hold_s=2.0)
    clock.t = 1.0
    motor.sweep()
    assert ("/input/Vertical", 0.0) not in sent  # 没到点,不动
    clock.t = 2.1
    motor.sweep()
    assert sent[-1] == ("/input/Vertical", 0.0)  # 到点强制清零
    motor.sweep()
    assert sent[-1] == ("/input/Vertical", 0.0)  # 幂等:不重复发


def test_zero_all_only_touches_dirty_axes():
    sent = []
    motor = OscMotor(send_fn=lambda a, v: sent.append((a, v)))
    motor.set_axis("Horizontal", -1.0, hold_s=1.0)
    sent.clear()
    motor.zero_all()
    assert sent == [("/input/Horizontal", 0.0)]  # 干净的轴不发多余包


def test_voice_semantics():
    sent = []
    motor = OscMotor(send_fn=lambda a, v: sent.append((a, v)))
    motor.voice(True)
    motor.voice(False)
    assert sent == [("/input/Voice", 1), ("/input/Voice", 0)]
