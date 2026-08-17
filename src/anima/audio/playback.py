"""扬声器播放:mp3 流 → ffmpeg 管道解码 s16le → sounddevice。

edge-tts 吐的是 24kHz mp3 分块;ffmpeg 以管道边收边解,首包即可开播,
不用等整段合成完。播放全程由 speech 管线包裹(开麦→播→闭麦→回声尾),
本模块只管"把声音放出来"和"随时能停"。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

log = logging.getLogger(__name__)

PCM_RATE = 24000  # edge-tts 输出采样率
_CHUNK_BYTES = 4800  # 0.1s @ 24kHz s16le mono


class AudioPlayer:
    """把 mp3 字节流播放到输出设备。stop() 可随时打断。"""

    def __init__(self, device: str | int | None = None, ffmpeg: str = "ffmpeg"):
        self._device = device
        self._ffmpeg = ffmpeg
        self._proc: asyncio.subprocess.Process | None = None
        self._stop_flag = False
        self.available: bool | None = None  # None=未探测

    @property
    def is_playing(self) -> bool:
        return self._proc is not None

    def stop(self) -> None:
        """打断当前播放(panic / 关停用)。"""
        self._stop_flag = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def play_mp3(self, chunks: AsyncIterator[bytes]) -> bool:
        """播放一段 mp3 流;成功播完返回 True,设备/解码失败返回 False。

        失败不抛异常——上层据此退化为"只发聊天框字幕"。
        """
        try:
            import sounddevice as sd
        except Exception as e:
            self._mark_unavailable(f"sounddevice 不可用:{e}")
            return False

        self._stop_flag = False
        try:
            proc = await asyncio.create_subprocess_exec(
                self._ffmpeg,
                "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", str(PCM_RATE),
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._mark_unavailable("找不到 ffmpeg,请安装(apt install ffmpeg)")
            return False
        self._proc = proc

        async def feed() -> None:
            assert proc.stdin is not None
            try:
                async for chunk in chunks:
                    if self._stop_flag:
                        break
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            except Exception as e:  # TTS 网络流断了:播已到手的部分,别崩
                log.warning("TTS 音频流中断:%s", e)
            finally:
                try:
                    proc.stdin.close()
                except (ConnectionResetError, BrokenPipeError):
                    pass

        feeder = asyncio.create_task(feed())
        ok = True
        stream = None
        try:
            stream = sd.RawOutputStream(
                device=self._device,
                samplerate=PCM_RATE,
                channels=1,
                dtype="int16",
            )
            stream.start()
            self.available = True
            assert proc.stdout is not None
            while not self._stop_flag:
                data = await proc.stdout.read(_CHUNK_BYTES)
                if not data:
                    break
                # 阻塞写提供背压,丢线程池里做,别卡事件循环
                await asyncio.to_thread(stream.write, data)
        except Exception as e:
            self._mark_unavailable(f"音频播放失败:{e}")
            ok = False
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):
                pass
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
            self._proc = None
        return ok and not self._stop_flag

    def _mark_unavailable(self, reason: str) -> None:
        if self.available is not False:  # 只在状态翻转时刷日志
            log.warning("语音播放不可用,将退化为纯字幕:%s", reason)
        self.available = False
