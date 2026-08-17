"""说话管线:半双工的核心序列(DESIGN.md Q11/Q17)。

一次 speak 的完整顺序:
  1. 关采集门(自己的声音不进 VAD)
  2. 字幕镜像丢后台(聊天框有限速,不拖住语音)
  3. OSC 开麦(/input/Voice=1,前提:游戏内 Toggle Voice 关闭)
  4. TTS 流 → 播放器(失败自动退化为纯字幕)
  5. OSC 闭麦
  6. 等回声尾 echo_tail_ms(房间混响/虚拟声卡回灌的余音)
  7. 开采集门

整个 speak 持锁——一次只说一句;interrupt() 给 panic/关停用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class SpeechPipeline:
    def __init__(
        self,
        motor,
        chatbox,
        player,
        tts,
        capture=None,
        echo_tail_ms: int = 300,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._motor = motor
        self._chatbox = chatbox
        self._player = player
        self._tts = tts
        self._capture = capture
        self._echo_tail_ms = echo_tail_ms
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()

    @property
    def speaking(self) -> bool:
        return self._lock.locked()

    def interrupt(self) -> None:
        if self._player is not None:
            self._player.stop()

    async def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        async with self._lock:
            if self._capture is not None:
                self._capture.gate(True)
            try:
                if self._chatbox is not None:
                    task = asyncio.create_task(self._chatbox.push(text))
                    self._bg.add(task)
                    task.add_done_callback(self._bg.discard)
                if self._tts is not None and self._player is not None:
                    self._motor.voice(True)
                    try:
                        ok = await self._player.play_mp3(self._tts.stream(text))
                        if not ok:
                            log.debug("语音播放未完成,字幕已镜像")
                    finally:
                        self._motor.voice(False)
            finally:
                if self._capture is not None:
                    await self._sleep(self._echo_tail_ms / 1000)
                    self._capture.gate(False)
