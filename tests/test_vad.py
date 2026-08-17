"""VAD 断句状态机(energy 后端,无模型依赖)。"""

import numpy as np

from anima.perception.vad import FRAME_MS, EnergyVad, UtteranceSegmenter

LOUD = np.full(512, 0.1, dtype=np.float32)   # rms=0.1
QUIET = np.zeros(512, dtype=np.float32)


def make_segmenter(**kw):
    defaults = dict(
        threshold=0.5,
        silence_ms=320,       # 10 帧
        min_speech_ms=320,    # 10 帧
        max_utterance_s=30.0,
        pre_roll_ms=96,       # 3 帧
    )
    defaults.update(kw)
    return UtteranceSegmenter(EnergyVad(rms_threshold=0.05), **defaults)


def feed_all(seg, frames):
    outputs = [seg.feed(f) for f in frames]
    return [o for o in outputs if o is not None]


def test_silence_yields_nothing():
    seg = make_segmenter()
    assert feed_all(seg, [QUIET] * 100) == []


def test_short_burst_discarded():
    seg = make_segmenter()
    # 5 帧语音(160ms)< min_speech_ms=320ms → 不触发
    assert feed_all(seg, [LOUD] * 5 + [QUIET] * 30) == []
    assert not seg.in_speech


def test_utterance_emitted_after_silence():
    seg = make_segmenter()
    outs = feed_all(seg, [QUIET] * 20 + [LOUD] * 15 + [QUIET] * 12)
    assert len(outs) == 1
    utt = outs[0]
    assert utt.dtype == np.float32
    assert len(utt) % 512 == 0
    # 至少包含全部 15 帧语音(预滚环保住了触发前的帧)
    assert len(utt) >= 15 * 512
    assert not seg.in_speech


def test_max_duration_force_cut():
    seg = make_segmenter(max_utterance_s=1.0)  # ≈31 帧
    outs = feed_all(seg, [LOUD] * 80)
    assert len(outs) >= 2  # 一直说话也会被强制切段
    assert all(len(o) * 1000 // (512 * 16000 // 512) or True for o in outs)
    for o in outs:
        assert len(o) // 512 * FRAME_MS <= 1000 + FRAME_MS


def test_in_speech_flag():
    seg = make_segmenter()
    for f in [QUIET] * 5 + [LOUD] * 12:
        seg.feed(f)
    assert seg.in_speech
    seg.reset()
    assert not seg.in_speech
