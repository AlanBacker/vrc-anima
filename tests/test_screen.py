"""抓屏:连续失败 3 次后本次运行停用(Xlib 绕过日志直刷 stderr,不能一直试)。"""

import sys

from anima.perception.screen import ScreenGrabber


def test_disables_after_three_consecutive_failures(monkeypatch):
    monkeypatch.setitem(sys.modules, "mss", None)  # import mss → ImportError
    g = ScreenGrabber()
    for _ in range(3):
        assert g.grab_jpeg() is None
    assert g._fails == 3
    g.grab_jpeg()  # 已停用:不再尝试,计数不再涨
    assert g._fails == 3


def test_backend_none_never_attempts():
    g = ScreenGrabber(backend="none")
    assert g.grab_jpeg() is None
    assert g._fails == 0


class _FakeShot:
    size = (64, 48)
    bgra = bytes(64 * 48 * 4)  # 全零 = 全黑


class _FakeSct:
    monitors = [{}, {}]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def grab(self, _m):
        return _FakeShot()


def _fake_mss(monkeypatch):
    import types

    fake = types.ModuleType("mss")
    fake.mss = _FakeSct
    monkeypatch.setitem(sys.modules, "mss", fake)


def test_black_frames_warn_once_after_three(monkeypatch, caplog):
    _fake_mss(monkeypatch)
    g = ScreenGrabber()
    with caplog.at_level("WARNING"):
        for _ in range(2):
            assert g.grab_jpeg() is not None
        assert "全黑" not in caplog.text
        g.grab_jpeg()
        assert "Wayland" in caplog.text
        caplog.clear()
        g.grab_jpeg()  # 只提示一次
        assert "Wayland" not in caplog.text


def test_bright_frame_resets_black_run(monkeypatch):
    _fake_mss(monkeypatch)
    g = ScreenGrabber()
    g.grab_jpeg()
    g.grab_jpeg()
    assert g._black_run == 2
    monkeypatch.setattr(_FakeShot, "bgra", b"\xff" * (64 * 48 * 4))
    g.grab_jpeg()
    assert g._black_run == 0
    assert not g._black_warned
