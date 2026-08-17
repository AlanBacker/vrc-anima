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
