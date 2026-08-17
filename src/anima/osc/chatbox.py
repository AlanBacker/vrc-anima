"""聊天框镜像:说的话同步显示成"字幕"。

VRChat /chatbox/input 的硬约束(官方文档):
- 单条上限 144 字符(超出的会被截断,所以必须自己分条);
- 限速为漏桶:5 条 / 5 秒,超发触发游戏内 rate limit;
- 换行在白名单内,可以保留;中文(UTF-8)显示待实机验证。

这里实现:按句子边界分条(≤144)+ 令牌桶限速(容量 5、每秒回填 1)+
/chatbox/typing 打字指示。时间函数可注入,方便测试不用真等。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[。!?!?;;.…\n])")


def split_message(text: str, max_chars: int = 144) -> list[str]:
    """把长文本按句子边界切成 ≤max_chars 的段;单句超长时硬切。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > max_chars:  # 单句超长:硬切
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if sentence:
            pieces.append(sentence)
    # 贪心合并相邻小段
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current.strip())
            current = piece
        else:
            current += piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


class ChatboxMirror:
    """限速的聊天框发送队列。send 注入 OscMotor.send。"""

    def __init__(
        self,
        send: Callable[[str, object], None],
        max_chars: int = 144,
        notify_sound: bool = False,
        capacity: int = 5,
        refill_per_s: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._send = send
        self.max_chars = max_chars
        self.notify = notify_sound
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill = refill_per_s
        self._last = monotonic()
        self._now = monotonic
        self._sleep = sleep
        self._lock = asyncio.Lock()  # 保序:并发 push 不交错

    def typing(self, on: bool) -> None:
        self._send("/chatbox/typing", on)

    async def push(self, text: str) -> None:
        """分条并按限速依次发送;调用方通常 create_task 不等它。"""
        chunks = split_message(text, self.max_chars)
        if not chunks:
            return
        async with self._lock:
            for chunk in chunks:
                await self._acquire()
                # 参数含义:文本, 立即发送(不打开输入框), 是否播放提示音
                self._send("/chatbox/input", [chunk, True, self.notify])

    async def _acquire(self) -> None:
        while True:
            now = self._now()
            self._tokens = min(
                float(self._capacity), self._tokens + (now - self._last) * self._refill
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await self._sleep((1.0 - self._tokens) / self._refill)
