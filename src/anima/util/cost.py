"""成本计:token 用量 → 美元,按自然日累计,超预算熔断(DESIGN.md Q18)。

单价在 config [costs] 里(默认 gemini-3.7-flash:输入 $0.75/Mtok、
输出 $3.75/Mtok),模型换了自己改配置,代码不猜价。
"""

from __future__ import annotations

import time
from typing import Callable


def _local_date() -> str:
    return time.strftime("%Y-%m-%d")


class CostMeter:
    def __init__(
        self,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        daily_budget_usd: float,
        today: Callable[[], str] = _local_date,
    ):
        self._in_rate = input_usd_per_mtok / 1_000_000
        self._out_rate = output_usd_per_mtok / 1_000_000
        self.daily_budget_usd = daily_budget_usd
        self._today = today
        self._day = today()
        self._usd = 0.0
        self._prompt_tokens = 0
        self._output_tokens = 0
        self._turns = 0

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day = day
            self._usd = 0.0
            self._prompt_tokens = 0
            self._output_tokens = 0
            self._turns = 0

    def add(self, prompt_tokens: int, output_tokens: int) -> float:
        """记一笔,返回本笔美元。"""
        self._roll()
        cost = prompt_tokens * self._in_rate + output_tokens * self._out_rate
        self._usd += cost
        self._prompt_tokens += prompt_tokens
        self._output_tokens += output_tokens
        self._turns += 1
        return cost

    @property
    def today_usd(self) -> float:
        self._roll()
        return self._usd

    @property
    def over_budget(self) -> bool:
        return self.daily_budget_usd > 0 and self.today_usd >= self.daily_budget_usd

    def reset(self) -> None:
        """手动清零(console 的 budget reset)。"""
        self._day = self._today()
        self._usd = 0.0
        self._prompt_tokens = 0
        self._output_tokens = 0
        self._turns = 0

    def summary(self) -> str:
        self._roll()
        budget = (
            f"/预算 ${self.daily_budget_usd:.2f}"
            if self.daily_budget_usd > 0
            else "(无预算上限)"
        )
        return (
            f"今日 {self._turns} 回合,输入 {self._prompt_tokens} tok / "
            f"输出 {self._output_tokens} tok,约 ${self._usd:.4f}{budget}"
        )
