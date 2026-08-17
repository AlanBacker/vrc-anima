"""采集层:软件增益的数学(放大 + 削顶),不起真 parec 进程。"""

from types import SimpleNamespace

import numpy as np
import pytest

from anima.audio.capture import FRAME_SAMPLES, MicCapture


class _Stdout:
    """喂完给定帧后置 _stopping,让读取线程体安静退出(不触发意外退出告警)。"""

    def __init__(self, cap: MicCapture, chunks: list[bytes]):
        self._cap = cap
        self._chunks = list(chunks)

    def read(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        self._cap._stopping = True
        return b""


def _run_one_frame(gain: float, amplitude: float) -> MicCapture:
    cap = MicCapture(gain=gain)
    data = np.full(FRAME_SAMPLES, amplitude, dtype=np.float32).tobytes()
    cap._proc = SimpleNamespace(stdout=_Stdout(cap, [data]), stderr=None)
    cap._read_loop()
    return cap


def test_gain_scales_level_stats():
    cap = _run_one_frame(gain=3.0, amplitude=0.1)
    assert cap.frames_total == 1
    assert cap.level_now == pytest.approx(0.3, rel=1e-3)


def test_gain_clips_at_full_scale():
    cap = _run_one_frame(gain=4.0, amplitude=0.5)
    assert cap.level_now == pytest.approx(1.0, rel=1e-3)


def test_unity_gain_passthrough():
    cap = _run_one_frame(gain=1.0, amplitude=0.1)
    assert cap.level_now == pytest.approx(0.1, rel=1e-3)
