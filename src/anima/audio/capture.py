"""麦克风采集:16kHz 单声道 float32,512 样本一帧(32ms)。

帧大小 512 特意与 silero-vad v5 的输入窗口一致——采集帧不需要重组
就能直接喂 VAD。

半双工回声抑制的第一环在这里:bot 开口说话前 gate(True),采集帧在
回调里直接丢掉;说完并等够 echo_tail_ms 后 gate(False) 恢复。这样
自己的声音(经房间/混音回来的)不会被当成来人说话。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import numpy as np

log = logging.getLogger(__name__)

FRAME_SAMPLES = 512  # 32ms @ 16kHz,= silero v5 窗口


class MicCapture:
    """sounddevice 输入流 → asyncio 队列。start() 需在事件循环内调用。"""

    def __init__(
        self,
        device: str | int | None = None,
        sample_rate: int = 16000,
        queue_max: int = 512,
    ):
        self._device = device
        self.sample_rate = sample_rate
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=queue_max)
        self._gated = False
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped = 0
        self._status_logged = False

    def start(self) -> None:
        import sounddevice as sd  # 懒加载:无声卡的环境 import 就会抱怨

        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            device=self._device,
            channels=1,
            samplerate=self.sample_rate,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()
        log.info(
            "麦克风采集已启动:device=%s rate=%d 帧=%d样本",
            self._device if self._device is not None else "默认",
            self.sample_rate,
            FRAME_SAMPLES,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ---------------------------------------------------------- 半双工门控

    def gate(self, closed: bool) -> None:
        """closed=True 时丢弃一切输入(bot 正在说话)。"""
        self._gated = closed
        if closed:
            self.drain()

    @property
    def gated(self) -> bool:
        return self._gated

    def drain(self) -> None:
        """清空积压帧(门控开启瞬间,旧帧也不要了)。"""
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    # ---------------------------------------------------------- 消费

    async def frames(self) -> AsyncIterator[np.ndarray]:
        while True:
            yield await self._queue.get()

    # ---------------------------------------------------------- 回调(音频线程)

    def _callback(self, indata, frames, time_info, status) -> None:
        if status and not self._status_logged:
            self._status_logged = True
            log.warning("音频输入流状态异常(仅提示一次):%s", status)
        if self._gated:
            return
        frame = np.copy(indata[:, 0])
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._put, frame)

    def _put(self, frame: np.ndarray) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("音频帧队列满,累计丢弃 %d 帧(消费太慢?)", self._dropped)
