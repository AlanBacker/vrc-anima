"""Anima 总装:把 OSC、音频、感知、大脑、动作、记忆、状态、控制台
拧成一个"听→想→说/动"的回合环(DESIGN.md §3)。

回合时序:
  VAD 断句 → STT 转写 → 参与判定(状态机)→ 组装上下文
  (转写 + 现场状态行 + 768px 画面帧 + 记忆索引)→ gemini 级联
  → 文本=语音(TTS + 字幕镜像)+ 工具调用(异步执行、回执入史)

工具回执不额外调 API——functionResponse 附在历史里随下一回合送出;
唯一例外是 snapshot:抓高清帧后立即追问一轮(最多一层),因为"看"
的结果就是这回合要用的。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path

from .action.executor import SNAPSHOT_SENTINEL, ActionExecutor
from .action.speech import SpeechPipeline
from .action.tools import MEMORY_TOOLS, build_tool_decls
from .audio.capture import MicCapture
from .audio.playback import AudioPlayer
from .brain.base import AssistantTurn, ToolCall, ToolResultTurn, UserTurn
from .brain.gemini import GeminiBrain
from .brain.history import History
from .brain.prompt import build_system_prompt
from .config import AnimaConfig
from .console.cli import Console
from .memory.service import MemoryService
from .osc.chatbox import ChatboxMirror
from .osc.client import OscMotor
from .osc.listener import SelfState, start_listener
from .perception.screen import ScreenGrabber
from .perception.stt import build_stt, pcm_to_wav_bytes
from .perception.vad import UtteranceSegmenter, build_vad
from .state.machine import StateMachine
from .tts.edge import EdgeTts
from .util.cost import CostMeter

log = logging.getLogger(__name__)

SNAPSHOT_PX = 1280  # snapshot 高清帧长边(回合常规帧用 [screen].turn_frame_px)


def _device(value: str) -> str | int | None:
    """配置里的音频设备:空=系统默认,纯数字=设备序号,其余按名称匹配。"""
    value = (value or "").strip()
    if not value:
        return None
    return int(value) if value.isdigit() else value


class Anima:
    def __init__(self, cfg: AnimaConfig):
        self.cfg = cfg
        self._shutdown = asyncio.Event()
        self._last_budget_warn = 0.0
        self._capture_ok = False
        self._osc_transport = None
        self.segmenter: UtteranceSegmenter | None = None

        self.motor = OscMotor(cfg.osc.host, cfg.osc.send_port)
        self.self_state = SelfState()
        self.chatbox = (
            ChatboxMirror(
                self.motor.send,
                max_chars=cfg.chatbox.max_chars,
                notify_sound=cfg.chatbox.notify_sound,
            )
            if cfg.chatbox.enabled
            else None
        )
        self.screen = ScreenGrabber(
            cfg.screen.backend, cfg.screen.monitor, cfg.screen.jpeg_quality
        )
        self.stt = build_stt(cfg.stt)
        self.tts = (
            EdgeTts(cfg.tts.voice, cfg.tts.rate)
            if cfg.tts.provider == "edge"
            else None
        )
        self.player = AudioPlayer(_device(cfg.audio.output_device))
        self.capture = MicCapture(
            _device(cfg.audio.input_device), cfg.audio.sample_rate
        )
        self.memory = (
            MemoryService(cfg.data_dir, cfg.memory.session_key)
            if cfg.memory.enabled
            else None
        )
        self.state = StateMachine(
            mode=cfg.state.mode,
            name=cfg.core.name,
            wakeword=cfg.state.wakeword,
            engaged_idle_timeout_s=cfg.state.engaged_idle_timeout_s,
        )
        self.cost = CostMeter(
            cfg.costs.input_per_mtok, cfg.costs.output_per_mtok, cfg.limits.daily_usd
        )
        self.history = History(cfg.brain.max_history_turns)
        self.executor = ActionExecutor(self.motor, cfg.calibration, cfg.emotes)
        self.speech = SpeechPipeline(
            self.motor,
            self.chatbox,
            self.player,
            self.tts,
            self.capture,
            echo_tail_ms=cfg.audio.echo_tail_ms,
        )

        decls = build_tool_decls(
            sorted(cfg.emotes), cfg.calibration.enable_look_pitch
        )
        if self.memory is None:
            decls = [d for d in decls if d["name"] not in MEMORY_TOOLS]
        self.brain = GeminiBrain(
            cfg.brain,
            build_system_prompt(cfg.core.name, self._load_persona()),
            decls,
        )

    def _load_persona(self) -> str:
        p = Path(self.cfg.core.persona_file)
        if p.is_file():
            return p.read_text(encoding="utf-8")
        log.info("人设文件 %s 不存在,先用空人设(可从 persona.example.md 复制)", p)
        return ""

    # ================================================================ 主循环

    async def run(self) -> None:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        log.info(
            "Anima 启动:名字=%s 模式=%s 大脑=%s STT=%s",
            cfg.core.name, cfg.state.mode, cfg.brain.gemini.model, cfg.stt.provider,
        )

        self.motor.start_watchdog()
        self._osc_transport = await start_listener(
            cfg.osc.host, cfg.osc.recv_port, self.self_state
        )

        vad = await build_vad(cfg.vad.backend, cfg.data_dir)
        self.segmenter = UtteranceSegmenter(
            vad,
            threshold=cfg.vad.threshold,
            silence_ms=cfg.vad.silence_ms,
            min_speech_ms=cfg.vad.min_speech_ms,
            max_utterance_s=cfg.vad.max_utterance_s,
            pre_roll_ms=cfg.vad.pre_roll_ms,
        )

        try:
            self.capture.start()
            self._capture_ok = True
        except Exception as e:
            log.warning("麦克风不可用(%s):听觉关闭,仅控制台可用(say/state)", e)

        if self.tts is None:
            log.info("TTS 关闭([tts].provider=%r):说话只走聊天框字幕", cfg.tts.provider)

        console_task = asyncio.create_task(Console(self).run(), name="console")
        watch: list[asyncio.Task] = [
            asyncio.create_task(self._shutdown.wait(), name="shutdown")
        ]
        if self._capture_ok:
            watch.append(asyncio.create_task(self._listen_loop(), name="listen"))

        try:
            done, _ = await asyncio.wait(watch, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.get_name() != "shutdown" and t.exception() is not None:
                    log.error(
                        "任务 %s 崩溃", t.get_name(), exc_info=t.exception()
                    )
        finally:
            for t in [*watch, console_task]:
                t.cancel()
            await asyncio.gather(*watch, console_task, return_exceptions=True)
            await self._cleanup()
        log.info("Anima 已退出")

    async def _cleanup(self) -> None:
        self.speech.interrupt()
        if self._capture_ok:
            self.capture.stop()
        try:
            await self.executor.stop_everything()
        except Exception:
            pass
        await self.motor.stop()
        if self._osc_transport is not None:
            self._osc_transport.close()
        aclose = getattr(self.stt, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass

    async def _listen_loop(self) -> None:
        assert self.segmenter is not None
        async for frame in self.capture.frames():
            utterance = self.segmenter.feed(frame)
            if utterance is None:
                continue
            try:
                await self._on_utterance(utterance)
            except Exception:
                log.exception("回合处理异常,继续听")

    async def _on_utterance(self, pcm) -> None:
        seconds = len(pcm) / self.cfg.audio.sample_rate
        log.info("捕到语音段(%.1f 秒),转写中…", seconds)
        t0 = time.monotonic()
        try:
            result = await self.stt.transcribe(pcm)
        except Exception as e:
            log.warning("STT 转写失败:%s", e)
            return
        stt_ms = (time.monotonic() - t0) * 1000
        text = result.text.strip()
        if not text:
            log.info("转写为空(%.1f 秒语音,耗时 %.0fms)——噪声或听不清", seconds, stt_ms)
            return
        log.info("听到(%.1fs|转写 %.0fms):%s", seconds, stt_ms, text)

        if not self.state.should_respond(text):
            log.info("旁听不回应(%s)", self.state.describe())
            return
        if self.cost.over_budget:
            now = time.monotonic()
            if now - self._last_budget_warn > 60:
                self._last_budget_warn = now
                log.warning(
                    "当日预算已用完(%s)——回合熔断,控制台 budget reset 可解除",
                    self.cost.summary(),
                )
            return

        await self.run_turn(text, pcm)
        self.state.on_turn_done()

    # ================================================================ 回合

    async def run_turn(self, text: str, pcm=None) -> None:
        """一个完整回合:组装上下文 → 大脑 → 说话/动作。"""
        frame = await self.screen.agrab_jpeg(self.cfg.screen.turn_frame_px)
        wav = None
        if pcm is not None and self.cfg.brain.attach_audio:
            wav = pcm_to_wav_bytes(pcm, self.cfg.audio.sample_rate)
        self.history.add(
            UserTurn(
                text=text,
                state_line=self._state_line(),
                frame_jpeg=frame,
                audio_wav=wav,
            )
        )
        await self._brain_cycle(depth=0)

    async def _brain_cycle(self, depth: int) -> None:
        memory_index = await self.memory.index_text() if self.memory else ""
        if self.chatbox is not None:
            self.chatbox.typing(True)
        try:
            reply = await self.brain.reply(self.history.render(memory_index))
        except Exception as e:
            log.error("大脑调用失败:%s", e)
            return
        finally:
            if self.chatbox is not None:
                self.chatbox.typing(False)

        spent = self.cost.add(reply.usage.prompt_tokens, reply.usage.output_tokens)
        text = reply.text
        limit = self.cfg.limits.max_reply_chars
        if len(text) > limit:
            log.info("回复超长(%d 字),截断到 %d 字", len(text), limit)
            text = text[:limit] + "……"
        log.info(
            "回应:%s%s(本回合 $%.4f)",
            text or "(沉默)",
            f" [{len(reply.tool_calls)} 个动作]" if reply.tool_calls else "",
            spent,
        )
        self.history.add(AssistantTurn(text=text, tool_calls=reply.tool_calls))

        snapshot_taken = False
        for call in reply.tool_calls:
            result = await self._run_tool(call)
            frame_hi = None
            if isinstance(result, dict) and result.pop(SNAPSHOT_SENTINEL, False):
                frame_hi = await self.screen.agrab_jpeg(SNAPSHOT_PX)
                if frame_hi is None:
                    result = {"status": "error", "detail": "抓屏失败,拍不了照片"}
                else:
                    snapshot_taken = True
            log.info(
                "动作 %s%s → %s",
                call.name,
                call.args or "",
                result.get("detail") or result.get("status"),
            )
            self.history.add(
                ToolResultTurn(
                    name=call.name,
                    result=result,
                    call_id=call.call_id,
                    frame_jpeg=frame_hi,
                )
            )

        # 说话与 snapshot 追问并行:追问回合的说话会在锁上自然排队
        speak_task = (
            asyncio.create_task(self.speech.speak(text)) if text else None
        )
        if snapshot_taken and depth < 1:
            await self._brain_cycle(depth + 1)
        if speak_task is not None:
            await speak_task

    async def _run_tool(self, call: ToolCall) -> dict:
        if call.name in MEMORY_TOOLS:
            if self.memory is None:
                return {"status": "error", "detail": "记忆功能未启用"}
            return await self.memory.run_tool(call)
        return self.executor.dispatch(call)

    def _state_line(self) -> str:
        return (
            f"{self.self_state.state_line()} | {self.state.describe()} | "
            f"时间 {time.strftime('%H:%M')}"
        )

    # ================================================================ 控制台接口

    @property
    def capture_ok(self) -> bool:
        return self._capture_ok

    def _mic_level_text(self) -> str:
        """输入通路诊断:parec 有没有吐帧、帧里有没有声音。"""
        n = self.capture.frames_total
        if n == 0:
            return "无数据(parec 没吐出任何帧)"
        rms = self.capture.level_rms
        if rms < 1e-6:
            return f"纯静音(已收 {n} 帧——设备在采,但没有声音流入)"
        return f"{20 * math.log10(rms):.0f} dB(已收 {n} 帧)"

    def mic_text(self) -> str:
        if not self._capture_ok:
            return "听觉不可用:启动时采集就失败了,翻启动日志看原因。"
        dev = self.cfg.audio.input_device or "默认音源"
        lines = [
            f"采集设备:{dev}",
            f"门控:{'关(bot 说话/回声尾,暂不收音)' if self.capture.gated else '开(正常收音)'}",
            f"输入电平:{self._mic_level_text()}",
        ]
        if self.capture.frames_total and self.capture.level_rms < 1e-6:
            lines += [
                "→ 采集本身在跑,但进来的全是静音,说明声音没有路由到这个设备:",
                "   1) pactl list short sinks 确认虚拟声卡还在(重启后要重新 load-module)",
                "   2) 让游戏正在出声时开 pavucontrol → Playback 页,把 VRChat 的输出切到",
                "      对应虚拟声卡(游戏重启后流会重建,之前设过的可能已失效)",
                "   3) pavucontrol → Output Devices 页看该声卡电平条是否随声音跳动",
            ]
        return "\n".join(lines)

    def status_text(self) -> str:
        cfg = self.cfg
        if not self._capture_ok:
            hearing = "不可用"
        elif self.capture.gated:
            hearing = "说话门控中"
        else:
            hearing = f"开(电平 {self._mic_level_text()})"
        lines = [
            f"名字:{cfg.core.name}(大脑 {cfg.brain.gemini.model},STT {cfg.stt.provider})",
            f"参与:{self.state.describe()}",
            f"OSC:发 {cfg.osc.host}:{cfg.osc.send_port} / "
            f"收 {cfg.osc.recv_port}({'有数据' if self.self_state.alive else '无数据'})",
            f"听觉:{hearing}",
            f"对话历史:{len(self.history)} 条",
            f"成本:{self.cost.summary()}",
        ]
        return "\n".join(lines)

    async def stop_actions(self) -> None:
        await self.executor.stop_everything()

    async def panic(self) -> None:
        """急停:打断说话 + 停动作 + Avatar 安全模式。"""
        self.speech.interrupt()
        await self.executor.stop_everything()
        await self.motor.panic()

    def set_mute(self, muted: bool) -> None:
        self.motor.voice(not muted)

    async def say(self, text: str) -> None:
        await self.speech.speak(text)

    def request_shutdown(self) -> None:
        self._shutdown.set()
