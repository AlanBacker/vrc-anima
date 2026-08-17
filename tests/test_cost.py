"""成本计:计价、熔断、跨日清零。"""

from anima.util.cost import CostMeter


def make_meter(budget=5.0):
    day = ["2026-08-17"]
    meter = CostMeter(0.75, 3.75, budget, today=lambda: day[0])
    return meter, day


def test_pricing():
    meter, _ = make_meter()
    cost = meter.add(1_000_000, 1_000_000)
    assert abs(cost - 4.50) < 1e-9  # 0.75 + 3.75
    assert abs(meter.today_usd - 4.50) < 1e-9


def test_over_budget_trips():
    meter, _ = make_meter(budget=0.001)
    assert not meter.over_budget
    meter.add(10_000, 0)
    assert meter.over_budget
    meter.reset()
    assert not meter.over_budget


def test_day_rollover_resets():
    meter, day = make_meter(budget=0.001)
    meter.add(10_000, 0)
    assert meter.over_budget
    day[0] = "2026-08-18"
    assert meter.today_usd == 0.0
    assert not meter.over_budget


def test_zero_budget_means_unlimited():
    meter, _ = make_meter(budget=0)
    meter.add(10_000_000, 10_000_000)
    assert not meter.over_budget
    assert "无预算上限" in meter.summary()
