"""聊天框:144 分条与令牌桶限速(假时钟,不真等)。"""

import pytest

from anima.osc.chatbox import ChatboxMirror, split_message


def test_short_message_untouched():
    assert split_message("你好呀") == ["你好呀"]


def test_empty_message():
    assert split_message("   ") == []


def test_long_message_splits_at_sentences():
    text = "第一句话。" * 40  # 200 字
    chunks = split_message(text, max_chars=144)
    assert len(chunks) >= 2
    assert all(len(c) <= 144 for c in chunks)
    assert "".join(chunks) == text


def test_oversized_single_sentence_hard_cut():
    text = "啊" * 300  # 无标点
    chunks = split_message(text, max_chars=144)
    assert all(len(c) <= 144 for c in chunks)
    assert "".join(chunks) == text


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@pytest.mark.asyncio
async def test_token_bucket_rate_limit():
    sent: list = []
    clock = Clock()
    box = ChatboxMirror(
        send=lambda addr, val: sent.append((addr, val)),
        max_chars=10,
        capacity=5,
        refill_per_s=1.0,
        monotonic=clock.now,
        sleep=clock.sleep,
    )
    # 7 条各 10 字 → 前 5 条不等,第 6/7 条各等 1 秒
    await box.push("一二三四五六七八九十" * 7)
    msgs = [v for a, v in sent if a == "/chatbox/input"]
    assert len(msgs) == 7
    assert clock.slept == [1.0, 1.0]
    # 参数形状:[文本, 立即发送, 提示音]
    assert msgs[0][1] is True and msgs[0][2] is False


@pytest.mark.asyncio
async def test_typing_indicator():
    sent: list = []
    box = ChatboxMirror(send=lambda a, v: sent.append((a, v)))
    box.typing(True)
    box.typing(False)
    assert sent == [("/chatbox/typing", True), ("/chatbox/typing", False)]
