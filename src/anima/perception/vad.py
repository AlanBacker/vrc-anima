"""VAD 与断句:把连续麦克风帧切成"一段话"。

两个后端:
- silero:silero-vad v5 的 ONNX 模型(首次运行自动下载,CPU 推理,
  每帧 512 样本 @16kHz 正好一个窗口);
- energy:纯 RMS 能量门限,无依赖,作为 silero 不可用时的兜底,也
  让测试不需要模型文件。

UtteranceSegmenter 是纯逻辑状态机:预滚环(pre-roll)+ 最短语音时长
去抖 + 静音收尾 + 最长时限强切,输入输出都是 numpy,可离线测试。
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

log = logging.getLogger(__name__)

FRAME_SAMPLES = 512
SAMPLE_RATE = 16000
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE  # 32ms

SILERO_URLS = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "master/src/silero_vad/data/silero_vad.onnx",
    # GitHub raw 被限流(429)时的 CDN 镜像
    "https://cdn.jsdelivr.net/gh/snakers4/silero-vad@master/"
    "src/silero_vad/data/silero_vad.onnx",
)


class Vad(Protocol):
    def __call__(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class EnergyVad:
    """RMS 能量门限:超过阈值记 1.0,否则 0.0。糙,但零依赖。"""

    def __init__(self, rms_threshold: float = 0.015):
        self.rms_threshold = rms_threshold

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        return 1.0 if rms >= self.rms_threshold else 0.0

    def reset(self) -> None:
        pass


CONTEXT_SAMPLES = 64  # v5 要求帧前拼上一帧尾部 64 样本,实际输入 576


class SileroVad:
    """silero-vad v5 ONNX:输入 [1, 64+512] f32 + 状态 [2,1,128],输出语音概率。

    64 样本上下文不可省:模型输入轴是动态的,裸喂 512 不报错,但概率
    会塌到 ~0(真人语音实测 max 0.008 vs 正确姿势 max 1.0)。
    """

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        if frame.shape[0] != FRAME_SAMPLES:
            raise ValueError(f"silero 需要 {FRAME_SAMPLES} 样本帧,收到 {frame.shape[0]}")
        frame = frame.astype(np.float32, copy=False)
        x = np.concatenate([self._context, frame])
        out, self._state = self._session.run(
            None,
            {"input": x.reshape(1, -1), "state": self._state, "sr": self._sr},
        )
        self._context = frame[-CONTEXT_SAMPLES:]
        return float(out[0][0])


async def ensure_silero_model(path: Path) -> Path:
    """模型不存在时下载(约 2MB);官方 raw 被限流时依次换镜像源。"""
    if path.exists() and path.stat().st_size > 100_000:
        return path
    import httpx

    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        for url in SILERO_URLS:
            log.info("下载 silero-vad 模型 → %s(源:%s)", path, url.split("/")[2])
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as e:
                last_err = e
                continue
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(resp.content)
            tmp.replace(path)
            return path
    raise RuntimeError(
        f"所有下载源都失败(最后错误:{last_err});"
        f"可手动下载:wget -O {path} {SILERO_URLS[0]}"
    )


async def build_vad(backend: str, data_dir: Path) -> Vad:
    """按配置构建 VAD;silero 失败时降级 energy 并警告(不致命)。"""
    if backend == "silero":
        try:
            model = await ensure_silero_model(data_dir / "models" / "silero_vad.onnx")
            vad = SileroVad(model)
            log.info("VAD 后端:silero(ONNX)")
            return vad
        except Exception as e:
            log.warning("silero VAD 不可用(%s),降级为能量门限", e)
    log.info("VAD 后端:energy(RMS 门限)")
    return EnergyVad()


class UtteranceSegmenter:
    """帧进、整段话出的断句状态机。

    feed(frame) 返回 None(话没说完)或一段完整语音的 float32 PCM。
    去抖:连续语音 ≥ min_speech_ms 才算开口;静音 ≥ silence_ms 收尾;
    时长 ≥ max_utterance_s 强制切段,防一直不停嘴撑爆内存。
    """

    def __init__(
        self,
        vad: Callable[[np.ndarray], float],
        threshold: float = 0.5,
        silence_ms: int = 700,
        min_speech_ms: int = 300,
        max_utterance_s: float = 30.0,
        pre_roll_ms: int = 300,
    ):
        self._vad = vad
        self._threshold = threshold
        self._silence_ms = silence_ms
        self._min_speech_ms = max(min_speech_ms, FRAME_MS)
        self._max_ms = int(max_utterance_s * 1000)
        # 预滚环得装下 pre-roll + 开口去抖期间的全部帧,否则起头会被吃掉
        ring = (pre_roll_ms + self._min_speech_ms) // FRAME_MS + 2
        self._pre: deque[np.ndarray] = deque(maxlen=ring)
        self.last_prob = 0.0  # 最近一帧的 VAD 得分(诊断/电平表用)
        self._reset_runs()

    @property
    def threshold(self) -> float:
        return self._threshold

    def _reset_runs(self) -> None:
        self._active = False
        self._frames: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_run = 0

    def reset(self) -> None:
        self._reset_runs()
        self._pre.clear()
        if hasattr(self._vad, "reset"):
            self._vad.reset()

    @property
    def in_speech(self) -> bool:
        return self._active

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        self.last_prob = float(self._vad(frame))
        speaking = self.last_prob >= self._threshold

        if not self._active:
            self._pre.append(frame)
            if speaking:
                self._speech_run += FRAME_MS
                if self._speech_run >= self._min_speech_ms:
                    self._active = True
                    self._frames = list(self._pre)
                    self._silence_run = 0
            else:
                self._speech_run = 0
            return None

        self._frames.append(frame)
        if speaking:
            self._silence_run = 0
        else:
            self._silence_run += FRAME_MS

        duration = len(self._frames) * FRAME_MS
        if self._silence_run >= self._silence_ms or duration >= self._max_ms:
            utterance = np.concatenate(self._frames)
            self._reset_runs()
            self._pre.clear()
            return utterance
        return None
