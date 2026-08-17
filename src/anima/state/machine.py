"""参与状态机(DESIGN.md Q6 / §5)。

三种模式:
- always_on(常开):听到话就回应,M1 默认;
- wakeword(唤醒):转写里出现名字/唤醒词才进入 ENGAGED;
- gated(门控):M1 先复用唤醒词的本地文本匹配兜底——设计里的
  "ALERT 廉价判定"(≈$0.0002 的纯文本小调用判断是否在跟我说话)
  在 M2 接入,接入点就是 should_respond() 里标 TODO 的分支。

ENGAGED 有粘性:进入后 engaged_idle_timeout_s 秒内的后续发言直接
回应,不用每句都喊名字;超时静默回落 IDLE。

STT 红利:名字检测就是转写文本里的子串匹配,零成本(Q20)。
"""

from __future__ import annotations

import re
import time
from enum import Enum
from typing import Callable


class Phase(str, Enum):
    IDLE = "idle"
    ENGAGED = "engaged"


_SPLIT_RE = re.compile(r"[,、,;;\s]+")

MODE_LABELS = {"always_on": "常开", "gated": "门控", "wakeword": "唤醒"}
PHASE_LABELS = {Phase.IDLE: "待机", Phase.ENGAGED: "交谈中"}


class StateMachine:
    def __init__(
        self,
        mode: str = "always_on",
        name: str = "Anima",
        wakeword: str = "",
        engaged_idle_timeout_s: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.mode = mode
        self._timeout = engaged_idle_timeout_s
        self._now = monotonic
        self._phase = Phase.IDLE
        self._last_activity = 0.0
        variants = [name] + _SPLIT_RE.split(wakeword or "")
        self._variants = [v.lower() for v in variants if v.strip()]

    # ------------------------------------------------------------ 查询

    @property
    def phase(self) -> Phase:
        self._maybe_timeout()
        return self._phase

    def describe(self) -> str:
        return (
            f"模式={MODE_LABELS.get(self.mode, self.mode)} "
            f"阶段={PHASE_LABELS[self.phase]}"
        )

    # ------------------------------------------------------------ 事件

    def should_respond(self, transcript: str) -> bool:
        """一段转写进来,要不要动脑回应。副作用:推进状态。"""
        self._maybe_timeout()

        if self.mode == "always_on":
            self._engage()
            return True

        if self._phase is Phase.ENGAGED:  # 粘性:交谈中不用反复喊名字
            self._engage()
            return True

        if self._mentioned(transcript):
            self._engage()
            return True

        # TODO(M2): gated 模式在这里接"ALERT 廉价判定"——把转写连同
        # 近期上下文发给纯文本小调用,判断"是否在对我说话/是否该搭话"。
        return False

    def on_turn_done(self) -> None:
        """回合结束(说完话)刷新粘性计时。"""
        if self._phase is Phase.ENGAGED:
            self._last_activity = self._now()

    def disengage(self) -> None:
        self._phase = Phase.IDLE

    # ------------------------------------------------------------ 内部

    def _mentioned(self, transcript: str) -> bool:
        lowered = transcript.lower()
        return any(v in lowered for v in self._variants)

    def _engage(self) -> None:
        self._phase = Phase.ENGAGED
        self._last_activity = self._now()

    def _maybe_timeout(self) -> None:
        if (
            self._phase is Phase.ENGAGED
            and self._now() - self._last_activity >= self._timeout
        ):
            self._phase = Phase.IDLE
