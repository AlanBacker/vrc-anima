"""参与状态机:三模式 + 粘性超时(假时钟)。"""

from anima.state.machine import Phase, StateMachine


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_always_on_responds_to_everything():
    sm = StateMachine(mode="always_on", name="Anima")
    assert sm.should_respond("随便说点什么")
    assert sm.phase is Phase.ENGAGED


def test_wakeword_requires_name():
    clock = FakeClock()
    sm = StateMachine(
        mode="wakeword", name="Anima", wakeword="阿尼玛, 小安",
        engaged_idle_timeout_s=60, monotonic=clock,
    )
    assert not sm.should_respond("今天天气不错")
    assert sm.phase is Phase.IDLE
    assert sm.should_respond("anima 你在吗")   # 大小写不敏感
    assert sm.should_respond("阿尼玛快看这个")  # 自定义变体
    assert sm.should_respond("小安小安")


def test_engaged_is_sticky_until_timeout():
    clock = FakeClock()
    sm = StateMachine(
        mode="wakeword", name="Anima", engaged_idle_timeout_s=60, monotonic=clock
    )
    assert sm.should_respond("Anima 你好")
    clock.t = 30
    assert sm.should_respond("接着聊,不用喊名字")  # 粘性
    clock.t = 120
    assert not sm.should_respond("过了很久再说话")  # 超时回落
    assert sm.phase is Phase.IDLE


def test_gated_falls_back_to_mention_match_in_m1():
    sm = StateMachine(mode="gated", name="Anima")
    assert not sm.should_respond("路人甲和路人乙聊天")
    assert sm.should_respond("Anima 过来一下")


def test_describe_in_chinese():
    sm = StateMachine(mode="always_on")
    text = sm.describe()
    assert "常开" in text and "待机" in text
