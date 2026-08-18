"""大脑回合的追问循环:调用了工具就要有下文,且 depth 封顶不打转。

不起真 App(不碰 OSC/音频),用 __new__ 只装 _brain_cycle 摸得到的部件。
"""

import asyncio

from anima.app import Anima
from anima.brain.base import (
    AssistantTurn,
    BrainReply,
    TokenUsage,
    ToolCall,
    UserTurn,
)
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
    app._summarizer = None
    app._last_prompt_tokens = 0
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


# ------------------------------------------------------------------ 上下文压缩


class _FakeMemory:
    def __init__(self):
        self.writes = []

    async def run_tool(self, call):
        self.writes.append(call)
        return {"status": "ok", "detail": "已写入"}

    async def index_text(self):
        return ""


_SUMMARY_TEXT = (
    "<summary>## 聊了什么\n猫和喷泉</summary>\n"
    '<memories>[{"type": "user", "name": "user-小明", '
    '"description": "养两只猫", "content": "小明养了两只猫。"}]</memories>'
)


def _fill_rounds(app, n):
    for i in range(1, n + 1):
        app.history.add(UserTurn(text=f"第{i}句"))
        app.history.add(AssistantTurn(text=f"回{i}"))


def test_compress_now_swaps_history_and_writes_memories():
    app = _make_app([])
    app.memory = _FakeMemory()
    app._summarizer = _FakeBrain([_reply(text=_SUMMARY_TEXT)])
    _fill_rounds(app, 5)  # keep_recent_turns 默认 3 → 压前 2 轮
    assert asyncio.run(app.compress_now())
    assert app.history.user_turn_count() == 3
    assert "猫和喷泉" in app.history.summary
    assert len(app.memory.writes) == 1
    call = app.memory.writes[0]
    assert call.name == "memory_write"
    assert call.args["path"] == "user-小明.md"
    assert "小明养了两只猫" in call.args["content"]


def test_compress_failure_keeps_originals():
    """摘要调用炸了 / 没产出摘要 → 原文原样保留,不丢任何回合。"""

    class _Boom:
        async def reply(self, contents):
            raise RuntimeError("网关超时")

    app = _make_app([])
    app._summarizer = _Boom()
    _fill_rounds(app, 5)
    assert not asyncio.run(app.compress_now())
    assert app.history.user_turn_count() == 5 and app.history.summary == ""

    app2 = _make_app([])
    app2._summarizer = _FakeBrain([_reply(text="   ")])  # 空产出
    _fill_rounds(app2, 5)
    assert not asyncio.run(app2.compress_now())
    assert app2.history.user_turn_count() == 5 and app2.history.summary == ""


def test_compress_nothing_to_do():
    app = _make_app([])
    app._summarizer = _FakeBrain([_reply(text=_SUMMARY_TEXT)])
    _fill_rounds(app, 3)  # 不超过 keep_recent_turns,无候选
    assert not asyncio.run(app.compress_now())
    assert app._summarizer.calls == 0  # 连模型都不该叫


def test_maybe_compress_window_trigger():
    """窗口线:轮数到 max_history_turns-1 就压,抢在滑动窗口丢原文之前。"""
    app = _make_app([])
    app.cfg.brain.max_history_turns = 5
    app.history = History(5)
    app._summarizer = _FakeBrain([_reply(text=_SUMMARY_TEXT)])
    _fill_rounds(app, 3)
    asyncio.run(app._maybe_compress())  # 3 < 5-1,还不到线
    assert app._summarizer.calls == 0
    app.history.add(UserTurn(text="第4句"))
    asyncio.run(app._maybe_compress())  # 4 ≥ 5-1,触发
    assert app._summarizer.calls == 1
    assert app.history.user_turn_count() == 3  # 留 keep_recent_turns 轮原文


def test_maybe_compress_token_trigger_and_disable():
    app = _make_app([])
    app.cfg.compress.max_context_tokens = 1000
    app._summarizer = _FakeBrain([_reply(text=_SUMMARY_TEXT)])
    _fill_rounds(app, 4)  # 窗口远未满(默认 20)
    app._last_prompt_tokens = 600  # 低于 0.70×1000
    asyncio.run(app._maybe_compress())
    assert app._summarizer.calls == 0
    app._last_prompt_tokens = 750  # 触线
    asyncio.run(app._maybe_compress())
    assert app._summarizer.calls == 1

    app2 = _make_app([])
    app2.cfg.compress.enabled = False
    app2.cfg.brain.max_history_turns = 5
    app2._summarizer = _FakeBrain([_reply(text=_SUMMARY_TEXT)])
    _fill_rounds(app2, 4)
    asyncio.run(app2._maybe_compress())  # 总开关关着,窗口满也不动
    assert app2._summarizer.calls == 0
