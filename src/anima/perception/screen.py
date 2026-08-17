"""屏幕帧捕获:VRChat 画面 → JPEG 字节。

同机部署,X11 下用 mss 抓指定显示器整屏(VRChat 全屏运行即整屏=游戏
画面)。回合帧默认缩到长边 768px(Gemini 单 tile,约 258 tokens);
snapshot 工具要高清帧时传大一号的 max_px。

抓屏失败(无 X、headless 开发机)不致命:返回 None,回合退化为纯文本,
警告只刷一次。
"""

from __future__ import annotations

import asyncio
import io
import logging

log = logging.getLogger(__name__)


class ScreenGrabber:
    def __init__(
        self,
        backend: str = "x11",
        monitor: int = 1,
        jpeg_quality: int = 80,
    ):
        self._backend = backend
        self._monitor = monitor
        self._quality = jpeg_quality
        self._warned = False
        self._fails = 0  # 连续失败计数;满 3 次本次运行停用(Xlib 会绕过日志直刷 stderr)
        self._black_run = 0  # 连续全黑帧计数;满 3 帧提示一次(Wayland 会话的典型症状)
        self._black_warned = False

    def grab_jpeg(self, max_px: int = 768) -> bytes | None:
        """同步抓一帧;异步代码请用 agrab_jpeg。"""
        if self._backend == "none" or self._fails >= 3:
            return None
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                monitors = sct.monitors
                idx = self._monitor if self._monitor < len(monitors) else 1
                shot = sct.grab(monitors[idx])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            w, h = img.size
            longest = max(w, h)
            if longest > max_px:
                scale = max_px / longest
                img = img.resize(
                    (max(1, round(w * scale)), max(1, round(h * scale))),
                    Image.LANCZOS,
                )
            self._check_black(img)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=self._quality)
            self._fails = 0
            return buf.getvalue()
        except Exception as e:
            self._fails += 1
            if not self._warned:
                self._warned = True
                log.warning("抓屏失败(%s),回合将不带画面帧(仅提示一次)", e)
            elif self._fails == 3:
                log.warning(
                    "抓屏连续失败 3 次,本次运行停用抓屏;"
                    "修好 DISPLAY/XAUTHORITY 后重启生效"
                )
            else:
                log.debug("抓屏失败:%s", e)
            return None

    def _check_black(self, img) -> None:
        """连续 3 帧全黑给一次提示:抓成功但拍到黑,几乎总是 Wayland 会话。"""
        if self._black_warned:
            return
        _lo, hi = img.convert("L").getextrema()
        if hi > 5:
            self._black_run = 0
            return
        self._black_run += 1
        if self._black_run >= 3:
            self._black_warned = True
            log.warning(
                "抓屏成功但连续全黑:大概率是 Wayland 会话(X11 抓屏只能拍到黑),"
                "请在登录界面齿轮处切「Ubuntu on Xorg」;若已是 Xorg,"
                "检查 [screen].monitor 序号(0=所有屏拼接)"
            )

    async def agrab_jpeg(self, max_px: int = 768) -> bytes | None:
        return await asyncio.to_thread(self.grab_jpeg, max_px)
