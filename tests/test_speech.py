"""说话管线:半双工顺序(关门→开麦→播→闭麦→回声尾→开门)。"""

import asyncio

from anima.action.speech import SpeechPipeline
from anima.osc.client import OscMotor


class Cap:
    def __init__(self, events):
        self._events = events

    def gate(self, closed):
        self._events.append(("gate", closed))


class Player:
    def __init__(self, events, ok=True):
        self._events = events
        self._ok = ok

    async def play_mp3(self, chunks):
        async for _ in chunks:
            pass
        self._events.append(("play",))
        return self._ok

    def stop(self):
        self._events.append(("stop",))


class Tts:
    async def stream(self, text):
        yield b"pcm"


def make_pipeline(events, *, ok=True, echo_tail_ms=250):
    async def fake_sleep(s):
        events.append(("sleep", round(s, 3)))

    motor = OscMotor(send_fn=lambda a, v: events.append((a, v)))
    return SpeechPipeline(
        motor,
        None,
        Player(events, ok=ok),
        Tts(),
        capture=Cap(events),
        echo_tail_ms=echo_tail_ms,
        sleep=fake_sleep,
    )


async def test_half_duplex_sequence():
    events: list = []
    sp = make_pipeline(events)
    await sp.speak("你好")
    assert events == [
        ("gate", True),
        ("/input/Voice", 1),
        ("play",),
        ("/input/Voice", 0),
        ("sleep", 0.25),
        ("gate", False),
    ]


async def test_voice_closed_even_if_playback_fails():
    events: list = []
    sp = make_pipeline(events, ok=False)
    await sp.speak("你好")
    assert ("/input/Voice", 0) in events
    assert events[-1] == ("gate", False)  # 门一定重新打开


async def test_empty_text_is_noop():
    events: list = []
    sp = make_pipeline(events)
    await sp.speak("   ")
    assert events == []


async def test_subtitle_only_without_tts():
    pushed: list = []

    class Box:
        async def push(self, text):
            pushed.append(text)

    motor = OscMotor(send_fn=lambda a, v: pushed.append((a, v)))
    sp = SpeechPipeline(motor, Box(), None, None)
    await sp.speak(" 只有字幕 ")
    for _ in range(3):
        await asyncio.sleep(0)  # 让后台镜像任务跑完
    assert pushed == ["只有字幕"]  # 没有 /input/Voice 事件


async def test_interrupt_stops_player():
    events: list = []
    sp = make_pipeline(events)
    sp.interrupt()
    assert events == [("stop",)]
