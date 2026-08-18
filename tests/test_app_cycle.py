"""大脑回合的追问循环:调用了工具就要有下文,且 depth 封顶不打转。

不起真 App(不碰 OSC/音频),用 __new__ 只装 _brain_cycle 摸得到的部件。
"""

import asyncio

from anima.app import Anima
from anima.brain.base import BrainReply, TokenUsage, ToolCall
from anima.brain.history import History
from anima.config import AnimaConfig


class _FakeBrain:
    """按脚本逐轮吐 reply;耗尽后永远沉默。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def reply(self, contents):
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return BrainReply(text="", tool_calls=[], usage=TokenUsage())


class _FakeCost:
    def add(self, prompt, output):
        return 0.0


class _FakeSpeech:
    def __init__(self):
        self.spoken = []

    async def speak(self, text):
        self.spoken.append(text)


def _make_app(script):
    app = Anima.__new__(Anima)
    app.cfg = AnimaConfig()
    app.memory = None
    app.chatbox = None
    app.brain = _FakeBrain(script)
    app.cost = _FakeCost()
    app.history = History(20)
    app.speech = _FakeSpeech()
    app.executor = _FakeExecutor()
    return app


class _FakeExecutor:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, call):
        self.dispatched.append(call.name)
        return {"status": "ok", "detail": f"已开始 {call.name}"}


def _reply(text="", tools=()):
    return BrainReply(
        text=text,
        tool_calls=[ToolCall(name=n, args={}) for n in tools],
        usage=TokenUsage(),
    )


def test_tool_round_gets_follow_up():
    """动了工具 → 再问一轮,模型对结果说了话。"""
    app = _make_app([_reply(tools=["jump"]), _reply(text="跳完啦")])
    asyncio.run(app._brain_cycle(depth=0))
    assert app.brain.calls == 2
    assert app.executor.dispatched == ["jump"]
    assert app.speech.spoken == ["跳完啦"]


def test_no_tools_no_follow_up():
    """纯说话回合不追问。"""
    app = _make_app([_reply(text="你好呀")])
    asyncio.run(app._brain_cycle(depth=0))
    assert app.brain.calls == 1
    assert app.speech.spoken == ["你好呀"]


def test_depth_capped_no_infinite_loop():
    """模型每轮都动工具也只追问到 depth=2,共 3 轮封顶。"""
    always_act = [_reply(tools=["jump"]) for _ in range(10)]
    app = _make_app(always_act)
    asyncio.run(app._brain_cycle(depth=0))
    assert app.brain.calls == 3
    assert app.executor.dispatched == ["jump"] * 3
