"""控制台实时电平条:渲染纯函数 + 回车退出循环。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from anima.console.cli import Console


def _cap(level_now=0.0, level_rms=0.0, frames_total=100, gated=False):
    return SimpleNamespace(
        level_now=level_now,
        level_rms=level_rms,
        frames_total=frames_total,
        gated=gated,
    )


def _seg(last_prob=0.0, in_speech=False, threshold=0.5):
    return SimpleNamespace(
        last_prob=last_prob, in_speech=in_speech, threshold=threshold
    )


class TestMeterLine:
    def test_silence_shows_empty_bar(self):
        line = Console._meter_line(_cap(), _seg(), stalled_ticks=0)
        assert "静音" in line
        assert "█" not in line
        assert "VAD 0.00" in line

    def test_loud_signal_fills_bar_with_db(self):
        line = Console._meter_line(
            _cap(level_now=0.5, level_rms=0.5), _seg(last_prob=0.9), 0
        )
        assert "dB" in line
        assert line.count("█") > 20  # -6dB ≈ 九成满
        assert "VAD 0.90" in line

    def test_peak_cursor_survives_decay(self):
        # 瞬时已静,近 1 秒峰值仍在:条空但游标应立着
        line = Console._meter_line(_cap(level_now=0.0, level_rms=0.1), _seg(), 0)
        assert "▌" in line

    def test_stalled_warns_no_data(self):
        line = Console._meter_line(_cap(), _seg(), stalled_ticks=11)
        assert "无数据流" in line

    def test_gated_label(self):
        line = Console._meter_line(_cap(gated=True), _seg(), 0)
        assert "门控中" in line

    def test_in_speech_label(self):
        line = Console._meter_line(
            _cap(level_now=0.2, level_rms=0.2), _seg(0.8, in_speech=True), 0
        )
        assert "说话段" in line


class _FakeApp:
    def __init__(self):
        self.capture_ok = True
        self.capture = _cap(level_now=0.1, level_rms=0.1)
        self.segmenter = _seg()
        self.cfg = SimpleNamespace(audio=SimpleNamespace(input_device="test_src"))
        self.mic_text_called = False

    def mic_text(self):
        self.mic_text_called = True
        return "提示文本"


async def _run_meter(feed: bytes, delay: float = 0.0) -> tuple[str, _FakeApp]:
    app = _FakeApp()
    console = Console(app)
    reader = asyncio.StreamReader()
    console._reader = reader

    async def feeder():
        if delay:
            await asyncio.sleep(delay)
        reader.feed_data(feed)

    task = asyncio.create_task(feeder())
    follow = await console._mic_meter()
    await task
    return follow, app


async def test_enter_exits_with_no_follow_up():
    follow, _ = await _run_meter(b"\n")
    assert follow == ""


async def test_typed_command_is_returned_as_follow_up():
    follow, _ = await _run_meter(b"state\n", delay=0.15)
    assert follow == "state"


async def test_all_silent_session_prints_hints(capsys):
    app = _FakeApp()
    app.capture = _cap(level_now=0.0, level_rms=0.0)
    console = Console(app)
    reader = asyncio.StreamReader()
    console._reader = reader

    async def feeder():
        await asyncio.sleep(0.15)
        reader.feed_data(b"\n")

    task = asyncio.create_task(feeder())
    await console._mic_meter()
    await task
    assert app.mic_text_called
    assert "提示文本" in capsys.readouterr().out


async def test_capture_unavailable_prints_static_text(capsys):
    app = _FakeApp()
    app.capture_ok = False
    follow = await Console(app)._mic_meter()
    assert follow == ""
    assert app.mic_text_called
    assert "提示文本" in capsys.readouterr().out
