"""麦克风采集:parec 子进程 → 16kHz 单声道 float32,512 样本一帧(32ms)。

帧大小 512 特意与 silero-vad v5 的输入窗口一致——采集帧不需要重组
就能直接喂 VAD。

用 PulseAudio 原生工具 parec 而不是 PortAudio:可以按名字精确指定
采集源(如 anima_ears.monitor),PipeWire/Pulse 负责重采样;PortAudio
在 Linux 上通常只暴露 pulse/default 设备,定向不可靠。

半双工回声抑制的第一环在这里:bot 开口说话前 gate(True),采集帧在
读取线程里直接丢掉;说完并等够 echo_tail_ms 后 gate(False) 恢复。这样
自己的声音(经房间/混音回来的)不会被当成来人说话。
"""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import threading
from typing import AsyncIterator

import numpy as np

log = logging.getLogger(__name__)

FRAME_SAMPLES = 512  # 32ms @ 16kHz,= silero v5 窗口


class MicCapture:
    """parec 子进程 → asyncio 队列。start() 需在事件循环内调用。"""

    def __init__(
        self,
        device: str | None = None,
        sample_rate: int = 16000,
        queue_max: int = 512,
        parec: str = "parec",
        gain: float = 1.0,
    ):
        self._device = device
        self.sample_rate = sample_rate
        self._parec = parec
        self._gain = gain
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=queue_max)
        self._gated = False
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._dropped = 0
        # 诊断用(读取线程写、任意线程读;标量赋值在 GIL 下安全):
        self.frames_total = 0     # 收到的原始帧数(含门控期丢弃的)
        self.level_now = 0.0      # 最近一帧 RMS(线性 0..1,瞬时)
        self.level_rms = 0.0      # 近 1 秒 RMS 峰值(线性 0..1,衰减保持)
        self._heard_signal = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        cmd = [
            self._parec,
            "--raw",
            f"--rate={self.sample_rate}",
            "--channels=1",
            "--format=float32le",
            "--latency-msec=50",
            "--client-name=Anima",
            "--stream-name=ears",
        ]
        if self._device:
            cmd.append(f"--device={self._device}")
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "找不到 parec(PulseAudio 采集工具):sudo apt install pulseaudio-utils"
            ) from e
        self._stopping = False
        self._thread = threading.Thread(
            target=self._read_loop, name="mic-parec", daemon=True
        )
        self._thread.start()
        log.info(
            "麦克风采集已启动(parec):device=%s rate=%d 帧=%d样本",
            self._device or "默认音源",
            self.sample_rate,
            FRAME_SAMPLES,
        )
        if self._gain != 1.0:
            log.info("输入软件增益:×%.1f([audio].input_gain)", self._gain)

    def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

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

    # ---------------------------------------------------------- 读取线程

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        frame_bytes = FRAME_SAMPLES * 4  # float32
        while not self._stopping:
            data = proc.stdout.read(frame_bytes)
            if not data or len(data) < frame_bytes:
                break
            frame = np.frombuffer(data, dtype=np.float32)
            if self._gain != 1.0:
                # 增益在统计之前施加:电平条/VAD/转写看到的都是同一份信号
                frame = np.clip(frame * self._gain, -1.0, 1.0)
            self.frames_total += 1
            rms = float(np.sqrt(np.mean(frame * frame)))
            self.level_now = rms
            self.level_rms = max(rms, self.level_rms * 0.9)
            if not self._heard_signal and rms > 1e-4:
                self._heard_signal = True
                log.info("听觉通路有信号了(%.0f dB)", 20 * math.log10(rms))
            if self._gated:
                continue
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._put, frame.copy())
        if not self._stopping:
            err = b""
            try:
                if proc.stderr is not None:
                    err = proc.stderr.read() or b""
            except Exception:
                pass
            log.warning(
                "麦克风采集进程意外退出(device=%s):%s",
                self._device or "默认音源",
                err.decode(errors="replace").strip() or "未知原因",
            )

    def _put(self, frame: np.ndarray) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("音频帧队列满,累计丢弃 %d 帧(消费太慢?)", self._dropped)
