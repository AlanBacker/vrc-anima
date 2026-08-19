"""说话管线:半双工的核心序列(DESIGN.md Q11/Q17)。

一次 speak 的完整顺序:
  1. 关采集门(自己的声音不进 VAD)
  2. 字幕镜像丢后台(聊天框有限速,不拖住语音)
  3. OSC 开麦(/input/Voice=1,前提:游戏内 Toggle Voice 关闭)
  4. TTS 流 → 播放器(失败自动退化为纯字幕)
  5. OSC 闭麦
  6. 等回声尾 echo_tail_ms(房间混响/虚拟声卡回灌的余音)
  7. 开采集门

整个 speak 持锁——一次只说一句;interrupt() 给 panic/关停/插话打断用。

barge_in=True(插话打断)时第 1/6/7 步跳过:说话期间采集门保持敞开,
让 VAD 能听见有人开口,app 层检测到插话就 interrupt()。前提是 bot 的
声音不会回灌进自己耳朵(VRChat 不回放本人麦克风;若世界会回放,请关
[audio].barge_in 退回半双工)。
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
        barge_in: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._motor = motor
        self._chatbox = chatbox
        self._player = player
        self._tts = tts
        self._capture = capture
        self._echo_tail_ms = echo_tail_ms
        self._barge_in = barge_in
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
            gate = self._capture is not None and not self._barge_in
            if gate:
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
                if gate:
                    await self._sleep(self._echo_tail_ms / 1000)
                    self._capture.gate(False)
