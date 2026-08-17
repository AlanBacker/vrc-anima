"""STT 转写:语音段 → 文本(默认路径,见 DESIGN.md Q20)。

两个 provider:
- sensevoice:本地 SenseVoice-small(funasr),CPU 实时,中英日韩粤,
  默认;需要 extras:pip install "vrc-anima[stt-local]"
- openai:OpenAI 兼容 /v1/audio/transcriptions(经 New API 网关等)

转写选择权在用户(设计决策 Q20):Anima 不越权替用户选模型。
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from dataclasses import dataclass

import numpy as np

from ..config import ConfigError, SttConfig

log = logging.getLogger(__name__)


@dataclass
class SttResult:
    text: str
    language: str | None = None


def pcm_to_wav_bytes(pcm: np.ndarray, rate: int = 16000) -> bytes:
    """float32 [-1,1] PCM → 16-bit WAV 字节(给 HTTP STT / 音频附件用)。"""
    clipped = np.clip(pcm, -1.0, 1.0)
    int16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(int16.tobytes())
    return buf.getvalue()


class SenseVoiceStt:
    """本地 SenseVoice-small。首次调用才加载模型(几百 MB,下载+载入慢)。"""

    def __init__(self, model_id: str = "iic/SenseVoiceSmall", language: str = "auto"):
        self._model_id = model_id
        self._language = language
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                try:
                    from funasr import AutoModel
                except ImportError as e:
                    raise ConfigError(
                        "本地 STT 需要 funasr:pip install \"vrc-anima[stt-local]\""
                    ) from e
                log.info("加载 SenseVoice 模型 %s(首次会下载)…", self._model_id)
                self._model = await asyncio.to_thread(
                    AutoModel,
                    model=self._model_id,
                    disable_update=True,
                    disable_pbar=True,
                )
        return self._model

    async def transcribe(self, pcm: np.ndarray) -> SttResult:
        model = await self._ensure()

        def _run() -> str:
            res = model.generate(
                input=pcm.astype(np.float32),
                fs=16000,
                language=self._language,
                use_itn=True,
            )
            if not res:
                return ""
            raw = res[0].get("text", "")
            try:
                from funasr.utils.postprocess_utils import (
                    rich_transcription_postprocess,
                )
                return rich_transcription_postprocess(raw)
            except Exception:
                return raw

        text = await asyncio.to_thread(_run)
        return SttResult(text=text.strip())


class OpenAiStt:
    """OpenAI 兼容 /audio/transcriptions(New API 网关同款)。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "auto",
        timeout: float = 30.0,
    ):
        if not base_url:
            raise ConfigError("[stt] provider=openai 需要 base_url(如 https://网关/v1)")
        if not model:
            raise ConfigError("[stt] provider=openai 需要 model(如 whisper-1)")
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout
        self._client = None

    async def transcribe(self, pcm: np.ndarray) -> SttResult:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        wav = pcm_to_wav_bytes(pcm)
        data: dict[str, str] = {"model": self._model}
        if self._language and self._language != "auto":
            data["language"] = self._language
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        resp = await self._client.post(
            self._url,
            data=data,
            files={"file": ("utterance.wav", wav, "audio/wav")},
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json()
        return SttResult(
            text=str(payload.get("text", "")).strip(),
            language=payload.get("language"),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def build_stt(cfg: SttConfig):
    """按配置构建 STT 引擎。"""
    if cfg.provider == "sensevoice":
        return SenseVoiceStt(language=cfg.language)
    if cfg.provider == "openai":
        return OpenAiStt(
            base_url=cfg.base_url,
            api_key=cfg.resolved_api_key(),
            model=cfg.model,
            language=cfg.language,
        )
    raise ConfigError(f"[stt] 未知 provider:{cfg.provider}")
