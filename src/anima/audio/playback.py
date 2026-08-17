"""扬声器播放:mp3 流 → ffmpeg 管道解码 s16le → pacat 写入指定 Pulse 设备。

edge-tts 吐的是 24kHz mp3 分块;ffmpeg 以管道边收边解,首包即可开播,
不用等整段合成完。用 pacat 而不是 PortAudio:可以按名字精确指定输出
设备(如 anima_mouth 虚拟声卡),声音才进得了游戏麦克风而不是默认扬声器。

播放全程由 speech 管线包裹(开麦→播→闭麦→回声尾),本模块只管
"把声音放到指定设备"和"随时能停";play_mp3 会等 pacat 排空缓冲后才
返回,保证上层闭麦时机在声音真正播完之后。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

log = logging.getLogger(__name__)

PCM_RATE = 24000  # edge-tts 输出采样率
_CHUNK_BYTES = 4800  # 0.1s @ 24kHz s16le mono


class AudioPlayer:
    """把 mp3 字节流播放到指定 Pulse 设备。stop() 可随时打断。"""

    def __init__(
        self,
        device: str | None = None,
        ffmpeg: str = "ffmpeg",
        pacat: str = "pacat",
    ):
        self._device = device
        self._ffmpeg = ffmpeg
        self._pacat = pacat
        self._procs: list[asyncio.subprocess.Process] = []
        self._stop_flag = False
        self.available: bool | None = None  # None=未探测

    @property
    def is_playing(self) -> bool:
        return bool(self._procs)

    def stop(self) -> None:
        """打断当前播放(panic / 关停用)。"""
        self._stop_flag = True
        for proc in self._procs:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def play_mp3(self, chunks: AsyncIterator[bytes]) -> bool:
        """播放一段 mp3 流;成功播完返回 True,设备/解码失败返回 False。

        失败不抛异常——上层据此退化为"只发聊天框字幕"。
        """
        self._stop_flag = False
        try:
            decoder = await asyncio.create_subprocess_exec(
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

        pacat_cmd = [
            self._pacat,
            "--playback",
            "--raw",
            f"--rate={PCM_RATE}",
            "--channels=1",
            "--format=s16le",
            "--client-name=Anima",
            "--stream-name=tts",
        ]
        if self._device:
            pacat_cmd.append(f"--device={self._device}")
        try:
            sink = await asyncio.create_subprocess_exec(
                *pacat_cmd,
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            decoder.kill()
            await decoder.wait()
            self._mark_unavailable(
                "找不到 pacat(PulseAudio 播放工具):sudo apt install pulseaudio-utils"
            )
            return False
        self._procs = [decoder, sink]

        async def feed() -> None:
            assert decoder.stdin is not None
            try:
                async for chunk in chunks:
                    if self._stop_flag:
                        break
                    decoder.stdin.write(chunk)
                    await decoder.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            except Exception as e:  # TTS 网络流断了:播已到手的部分,别崩
                log.warning("TTS 音频流中断:%s", e)
            finally:
                try:
                    decoder.stdin.close()
                except (ConnectionResetError, BrokenPipeError):
                    pass

        feeder = asyncio.create_task(feed())
        ok = True
        try:
            assert decoder.stdout is not None and sink.stdin is not None
            while not self._stop_flag:
                data = await decoder.stdout.read(_CHUNK_BYTES)
                if not data:
                    break
                sink.stdin.write(data)
                await sink.stdin.drain()  # pacat 缓冲提供背压
            try:
                sink.stdin.close()
            except (ConnectionResetError, BrokenPipeError):
                pass
            if not self._stop_flag:
                # pacat 排空缓冲后自己退出;非零 = 设备打不开等错误
                rc = await sink.wait()
                if rc != 0:
                    err = b""
                    if sink.stderr is not None:
                        err = await sink.stderr.read()
                    self._mark_unavailable(
                        f"pacat 播放失败(device={self._device or '默认'}):"
                        f"{err.decode(errors='replace').strip() or f'退出码 {rc}'}"
                    )
                    ok = False
                else:
                    self.available = True
        except (ConnectionResetError, BrokenPipeError):
            err = b""
            if sink.stderr is not None:
                try:
                    err = await sink.stderr.read()
                except Exception:
                    pass
            self._mark_unavailable(
                f"音频播放中断(device={self._device or '默认'}):"
                f"{err.decode(errors='replace').strip() or '管道关闭'}"
            )
            ok = False
        except Exception as e:
            self._mark_unavailable(f"音频播放失败:{e}")
            ok = False
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):
                pass
            for proc in self._procs:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                await proc.wait()
            self._procs = []
        return ok and not self._stop_flag

    def _mark_unavailable(self, reason: str) -> None:
        if self.available is not False:  # 只在状态翻转时刷日志
            log.warning("语音播放不可用,将退化为纯字幕:%s", reason)
        self.available = False
