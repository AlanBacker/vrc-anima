"""CLI 控制台(DESIGN.md Q14):管理动作只留给人,不暴露给模型。

命令走 stdin、结果打 stdout(log 走 stderr,互不搅和)。未来的 Web
控制台(M2)复用 app 上同一组方法,这里只是最薄的壳。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys

from anima.brain.base import ToolCall

log = logging.getLogger(__name__)

HELP = """\
可用命令:
  state           当前状态(模式/阶段/OSC/成本)
  mic             实时输入电平条(回车退出;显示电平/VAD 得分/说话段)
  turn <度数>      直接转身,不过大脑(正=右转,负=左转;标定用)
  cal             查看标定值;cal turn|look <值> 运行时调整(度/秒)
  puppet          身体姿态实验(OSC Trackers):puppet on|off|sway|bob|height|rate
  stop            立刻停止所有动作(清零所有轴)
  panic           急停:停动作 + 打断说话 + Avatar 安全模式
  mute on|off     开/关麦克风(OSC Voice)
  osc <参数> <值>  发一条裸 OSC(调 Avatar 参数用;可省 /avatar/parameters/ 前缀)
  say <文字>       让 Anima 直接说一句(测试 TTS 链路)
  cost            今日成本
  budget reset    预算清零(解除熔断)
  memory          记忆库位置与索引行数
  compress        压缩状态(轮数/摘要/触发线);compress now 立即压缩
  help            本帮助
  quit            退出 Anima
"""


class Console:
    """app 需要提供:status_text() / stop_actions() / panic() / set_mute(bool)
    / say(text) / cost / memory(可为 None)/ executor / puppet / motor / cfg
    / history / compress_now() / request_shutdown()。"""

    def __init__(self, app):
        self._app = app
        self._reader: asyncio.StreamReader | None = None

    async def run(self) -> None:
        try:
            reader = await self._stdin_reader()
        except (OSError, ValueError) as e:
            log.info("stdin 不可用(%s),控制台停用", e)
            return
        self._reader = reader
        print("Anima 控制台就绪,输入 help 看命令。", flush=True)
        while True:
            raw = await reader.readline()
            if not raw:  # EOF(管道关闭)
                log.info("stdin EOF,控制台退出")
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                if await self._dispatch(line):
                    return
            except Exception as e:
                print(f"命令执行失败:{e}", flush=True)

    async def _dispatch(self, line: str) -> bool:
        """返回 True 表示退出。"""
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()
        app = self._app

        if cmd in ("help", "?", "帮助"):
            print(HELP, flush=True)
        elif cmd in ("state", "状态"):
            print(app.status_text(), flush=True)
        elif cmd in ("mic", "听觉"):
            follow_up = await self._mic_meter()
            if follow_up:
                return await self._dispatch(follow_up)
        elif cmd in ("turn", "转"):
            try:
                degrees = float(rest)
            except ValueError:
                print("用法:turn <度数>(正=右转,负=左转,如 turn 90 / turn -45)", flush=True)
            else:
                result = app.executor.dispatch(ToolCall("turn", {"degrees": degrees}))
                print(result.get("detail", str(result)), flush=True)
        elif cmd in ("cal", "标定"):
            self._calibrate(rest)
        elif cmd in ("puppet", "木偶"):
            self._puppet(rest)
        elif cmd in ("stop", "停"):
            await app.stop_actions()
            print("已停止所有动作。", flush=True)
        elif cmd in ("panic", "急停"):
            await app.panic()
            print("已急停:动作清零、说话打断、Avatar 安全模式。", flush=True)
        elif cmd in ("mute", "麦"):
            if rest not in ("on", "off"):
                print("用法:mute on(静音)| mute off(开麦)", flush=True)
            else:
                app.set_mute(rest == "on")
                print("已静音。" if rest == "on" else "已开麦。", flush=True)
        elif cmd in ("osc", "参数"):
            self._osc_raw(rest)
        elif cmd in ("say", "说"):
            if not rest:
                print("用法:say <要说的话>", flush=True)
            else:
                await app.say(rest)
        elif cmd in ("cost", "成本"):
            print(app.cost.summary(), flush=True)
        elif cmd == "budget" and rest == "reset":
            app.cost.reset()
            print("预算已清零,熔断解除。", flush=True)
        elif cmd in ("memory", "记忆"):
            if app.memory is None:
                print("记忆功能未启用。", flush=True)
            else:
                index = await app.memory.index_text()
                lines = len(index.splitlines()) if index else 0
                print(
                    f"记忆目录:{app.memory.scope_dir}(索引 {lines} 行)",
                    flush=True,
                )
        elif cmd in ("compress", "压缩"):
            cc = app.cfg.compress
            if rest == "now":
                if not cc.enabled:
                    print("压缩已在配置中停用([compress].enabled)。", flush=True)
                elif await app.compress_now():
                    print("已压缩,详见日志。", flush=True)
                else:
                    print("本次没有压缩(无旧回合或失败,详见日志)。", flush=True)
            else:
                token_line = (
                    f"token 线:实报 {app._last_prompt_tokens} / "
                    f"{cc.threshold:.0%}×{cc.max_context_tokens}"
                    if cc.max_context_tokens > 0
                    else "token 线:未启用([compress].max_context_tokens=0)"
                )
                print(
                    f"压缩:{'开' if cc.enabled else '关'} | "
                    f"对话 {app.history.user_turn_count()}/"
                    f"{app.cfg.brain.max_history_turns} 轮"
                    f"(留 {cc.keep_recent_turns} 轮原文) | "
                    f"摘要 {len(app.history.summary)} 字 | {token_line}",
                    flush=True,
                )
        elif cmd in ("quit", "exit", "退出"):
            print("正在退出……", flush=True)
            app.request_shutdown()
            return True
        else:
            print(f"未知命令:{cmd}(help 看帮助)", flush=True)
        return False

    # ---------------------------------------------------------- 裸 OSC

    def _osc_raw(self, rest: str) -> None:
        """手动拨 Avatar 参数(如路线二木偶轴),不必等模型或后端出手。
        true/false 发布尔,其余按 float 发;整型参数(如 VRCEmote)走 emote 映射。"""
        addr, _, raw = rest.partition(" ")
        raw = raw.strip()
        if not addr or not raw:
            print(
                "用法:osc <参数名或完整地址> <数值|true|false>\n"
                "例:osc Puppet/ArmL_Up 0.7(自动补 /avatar/parameters/ 前缀)",
                flush=True,
            )
            return
        if not addr.startswith("/"):
            addr = "/avatar/parameters/" + addr
        value: bool | float
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = float(raw)
            except ValueError:
                print("值要是数字或 true/false。", flush=True)
                return
        self._app.motor.send(addr, value)
        print(f"已发送 {addr} = {value}", flush=True)

    # ---------------------------------------------------------- 转身标定

    def _calibrate(self, rest: str) -> None:
        cal = self._app.cfg.calibration
        if not rest:
            print(
                f"turn_deg_per_sec = {cal.turn_deg_per_sec:g}\n"
                f"look_deg_per_sec = {cal.look_deg_per_sec:g}\n"
                "调法:cal turn <值>(换算:新值 = 当前值 × 实转度数 ÷ 目标度数)",
                flush=True,
            )
            return
        key, _, raw = rest.partition(" ")
        attr = {"turn": "turn_deg_per_sec", "look": "look_deg_per_sec"}.get(key)
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if attr is None or value <= 0:
            print("用法:cal turn <正数> | cal look <正数>", flush=True)
            return
        setattr(cal, attr, value)
        print(
            f"{attr} = {value:g}(仅本次运行;满意后写进 config.toml 的 [calibration])",
            flush=True,
        )

    # ---------------------------------------------------------- 木偶实验

    def _puppet(self, rest: str) -> None:
        """OSC Trackers 身体姿态实验(桌面模式接收与否要实机验证)。"""
        pup = self._app.puppet
        sub, _, arg = rest.partition(" ")
        sub, arg = sub.strip().lower(), arg.strip()
        if not sub:
            print(pup.status_text(), flush=True)
        elif sub == "on":
            pup.start()
            print(
                "木偶流送已开始(中立位)。进游戏快捷菜单 Calibrate FBT(T 姿)"
                "后再试 puppet sway。",
                flush=True,
            )
        elif sub == "off":
            pup.request_stop()
            print("正在淡回中立位,收敛后停止流送。", flush=True)
        elif sub in ("height", "身高", "rate", "频率"):
            self._puppet_set(sub, arg)
        else:
            try:
                seconds = float(arg) if arg else None
            except ValueError:
                seconds = None
            try:
                played = pup.play(sub) if seconds is None else pup.play(sub, seconds)
            except ValueError as e:
                print(
                    f"{e}\n用法:puppet [on|off|sway [秒]|bob [秒]"
                    "|height <米>|rate <Hz>]",
                    flush=True,
                )
            else:
                print(f"木偶动作 {sub} 开始,{played:g} 秒后自动回中立位。", flush=True)

    def _puppet_set(self, key: str, raw: str) -> None:
        pup = self._app.puppet
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if key in ("height", "身高"):
            if not 0.5 <= value <= 2.5:
                print("用法:puppet height <0.5~2.5>(米,与游戏内身高一致)", flush=True)
                return
            pup.height_m = value
            print(
                f"木偶身高 = {value:g}m(仅本次运行;满意后写进 config.toml 的 [puppet])",
                flush=True,
            )
        else:
            if not 10 <= value <= 100:
                print("用法:puppet rate <10~100>(Hz)", flush=True)
                return
            pup.rate_hz = value
            print(f"木偶流送频率 = {value:g}Hz(仅本次运行)", flush=True)

    # ---------------------------------------------------------- 实时电平条

    async def _mic_meter(self) -> str:
        """实时刷新输入电平,回车退出。

        返回退出时用户顺手敲的命令(空串=纯回车),交还 _dispatch 执行。
        """
        app = self._app
        if not app.capture_ok:
            print(app.mic_text(), flush=True)
            return ""
        cap = app.capture
        seg = app.segmenter
        dev = app.cfg.audio.input_device or "默认音源"
        print(
            f"实时输入电平(设备:{dev},VAD 阈值 {seg.threshold:.2f})——按回车退出",
            flush=True,
        )

        stop = (
            asyncio.ensure_future(self._reader.readline())
            if self._reader is not None
            else None
        )
        session_peak = 0.0
        last_frames = cap.frames_total
        stalled_ticks = 0
        ticks = 0
        line = b""
        try:
            while True:
                if stop is not None:
                    done, _ = await asyncio.wait({stop}, timeout=0.1)
                    if done:
                        line = stop.result()
                        break
                else:  # stdin 异常时的兜底:跑 10 秒自动停
                    await asyncio.sleep(0.1)
                    if ticks >= 100:
                        break
                ticks += 1
                session_peak = max(session_peak, cap.level_rms)
                if cap.frames_total == last_frames:
                    stalled_ticks += 1
                else:
                    stalled_ticks = 0
                    last_frames = cap.frames_total
                sys.stdout.write("\r\x1b[K" + self._meter_line(cap, seg, stalled_ticks))
                sys.stdout.flush()
        finally:
            if stop is not None and not stop.done():
                stop.cancel()
            sys.stdout.write("\n")
            sys.stdout.flush()

        if session_peak < 1e-6:
            # 整个观察期一点声音都没有:直接给路由排查提示
            print(app.mic_text(), flush=True)
        return line.decode("utf-8", "replace").strip()

    @staticmethod
    def _meter_line(cap, seg, stalled_ticks: int) -> str:
        width = 30
        floor = -60.0  # 显示下限 dBFS
        rms = cap.level_now
        db = 20 * math.log10(rms) if rms > 1e-9 else floor
        peak = cap.level_rms
        db_peak = 20 * math.log10(peak) if peak > 1e-9 else floor
        filled = max(0, min(width, round((db - floor) / -floor * width)))
        peak_pos = max(0, min(width - 1, round((db_peak - floor) / -floor * width) - 1))
        bar = ["█"] * filled + ["░"] * (width - filled)
        if peak > 1e-6 and peak_pos >= filled:
            bar[peak_pos] = "▌"  # 近 1 秒峰值游标
        level = " 静音 " if rms < 1e-6 else f"{db:4.0f}dB"
        if stalled_ticks > 10:
            status = "⚠ 无数据流(parec 没在吐帧)"
        elif cap.gated:
            status = "门控中(bot 在说话)"
        elif seg.in_speech:
            status = f"VAD {seg.last_prob:.2f} ●说话段"
        else:
            status = f"VAD {seg.last_prob:.2f}"
        return f"[{''.join(bar)}] {level}  {status}"

    @staticmethod
    async def _stdin_reader() -> asyncio.StreamReader:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        return reader
