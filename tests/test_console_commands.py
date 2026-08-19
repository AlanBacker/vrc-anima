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


def _compress_app(enabled=True):
    app = SimpleNamespace(
        cfg=SimpleNamespace(
            compress=SimpleNamespace(
                enabled=enabled,
                keep_recent_turns=3,
                max_context_tokens=0,
                threshold=0.7,
            ),
            brain=SimpleNamespace(max_history_turns=20),
        ),
        history=SimpleNamespace(user_turn_count=lambda: 7, summary="摘要文本"),
        _last_prompt_tokens=1234,
        compress_calls=0,
    )

    async def compress_now():
        app.compress_calls += 1
        return True

    app.compress_now = compress_now
    return app


async def test_compress_status_line(capsys):
    await Console(_compress_app())._dispatch("compress")
    out = capsys.readouterr().out
    assert "压缩:开" in out and "7/20 轮" in out and "留 3 轮" in out
    assert "摘要 4 字" in out and "未启用" in out  # token 线默认关


async def test_compress_now_and_disabled(capsys):
    app = _compress_app()
    await Console(app)._dispatch("compress now")
    assert app.compress_calls == 1
    assert "已压缩" in capsys.readouterr().out
    off = _compress_app(enabled=False)
    await Console(off)._dispatch("compress now")
    assert off.compress_calls == 0
    assert "停用" in capsys.readouterr().out


class _FakePuppet:
    def __init__(self):
        self.calls = []
        self.height_m = 1.6
        self.rate_hz = 50.0

    def status_text(self):
        return "木偶状态一行"

    def start(self):
        self.calls.append("start")

    def request_stop(self):
        self.calls.append("stop")

    def play(self, name, seconds=8.0):
        if name == "nope":
            raise ValueError("未知木偶动作:nope")
        self.calls.append(f"play:{name}:{seconds:g}")
        return seconds


def _puppet_app():
    return SimpleNamespace(puppet=_FakePuppet())


async def test_puppet_status_on_off(capsys):
    app = _puppet_app()
    console = Console(app)
    await console._dispatch("puppet")
    assert "木偶状态一行" in capsys.readouterr().out
    await console._dispatch("puppet on")
    await console._dispatch("puppet off")
    assert app.puppet.calls == ["start", "stop"]
    assert "Calibrate FBT" in capsys.readouterr().out


async def test_puppet_play_with_seconds_and_unknown(capsys):
    app = _puppet_app()
    console = Console(app)
    await console._dispatch("puppet sway 12")
    await console._dispatch("puppet bob")
    assert app.puppet.calls == ["play:sway:12", "play:bob:8"]
    assert "12 秒后自动回中立" in capsys.readouterr().out
    await console._dispatch("puppet nope")
    assert "用法" in capsys.readouterr().out
    assert app.puppet.calls == ["play:sway:12", "play:bob:8"]


async def test_puppet_height_rate_set_and_reject(capsys):
    app = _puppet_app()
    console = Console(app)
    await console._dispatch("puppet height 1.72")
    assert app.puppet.height_m == 1.72
    await console._dispatch("puppet rate 30")
    assert app.puppet.rate_hz == 30.0
    assert "config.toml" in capsys.readouterr().out
    await console._dispatch("puppet height 9")
    await console._dispatch("puppet rate 5")
    assert app.puppet.height_m == 1.72 and app.puppet.rate_hz == 30.0
    assert "用法" in capsys.readouterr().out
