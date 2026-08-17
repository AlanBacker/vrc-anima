"""控制台标定命令:turn 直发动作、cal 运行时改标定值。"""

from types import SimpleNamespace

from anima.console.cli import Console


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def dispatch(self, call):
        self.calls.append(call)
        return {"status": "ok", "detail": "已开始向右转约 90 度(1.0 秒)"}


def _app():
    return SimpleNamespace(
        executor=_FakeExecutor(),
        cfg=SimpleNamespace(
            calibration=SimpleNamespace(turn_deg_per_sec=90.0, look_deg_per_sec=90.0)
        ),
    )


async def test_turn_dispatches_directly(capsys):
    app = _app()
    await Console(app)._dispatch("turn -45")
    (call,) = app.executor.calls
    assert call.name == "turn"
    assert call.args == {"degrees": -45.0}
    assert "已开始" in capsys.readouterr().out


async def test_turn_bad_input_prints_usage(capsys):
    app = _app()
    await Console(app)._dispatch("turn abc")
    await Console(app)._dispatch("turn")
    assert app.executor.calls == []
    assert "用法" in capsys.readouterr().out


async def test_cal_sets_value_at_runtime(capsys):
    app = _app()
    console = Console(app)
    await console._dispatch("cal turn 135")
    assert app.cfg.calibration.turn_deg_per_sec == 135.0
    await console._dispatch("cal look 80")
    assert app.cfg.calibration.look_deg_per_sec == 80.0
    assert "config.toml" in capsys.readouterr().out  # 提醒持久化


async def test_cal_show_and_reject_bad(capsys):
    app = _app()
    console = Console(app)
    await console._dispatch("cal")
    assert "turn_deg_per_sec = 90" in capsys.readouterr().out
    await console._dispatch("cal turn -5")
    await console._dispatch("cal wat 10")
    assert app.cfg.calibration.turn_deg_per_sec == 90.0
    assert "用法" in capsys.readouterr().out
