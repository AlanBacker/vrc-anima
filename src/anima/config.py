"""配置加载与校验:TOML → 数据类。

原则:出错时把话说清楚——哪个小节、哪个键、期望什么、拿到了什么;
默认值全部内置,空配置文件也能跑(只差 API key)。
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODE_ALIASES = {
    "always_on": "always_on",
    "常开": "always_on",
    "gated": "gated",
    "门控": "gated",
    "wakeword": "wakeword",
    "唤醒": "wakeword",
}


class ConfigError(ValueError):
    """配置错误,消息面向用户、可直接照着改。"""


@dataclass
class CoreConfig:
    name: str = "Anima"          # bot 名:唤醒词默认值、人设注入
    data_dir: str = "./data"     # 记忆 / 缓存 / 账单的根目录
    persona_file: str = "persona.md"
    log_level: str = "INFO"


@dataclass
class OscConfig:
    host: str = "127.0.0.1"
    send_port: int = 9000        # → VRChat /input 等
    recv_port: int = 9001        # ← VRChat 自身状态输出


@dataclass
class AudioConfig:
    input_device: str = ""       # 游戏混音分接源(名称或序号,空=系统默认)
    output_device: str = ""      # 虚拟麦克风 sink(名称或序号,空=系统默认)
    sample_rate: int = 16000
    echo_tail_ms: int = 300      # 半双工:说完话后额外静默的尾巴


@dataclass
class VadConfig:
    backend: str = "silero"      # silero | energy(能量门限,兜底/测试用)
    threshold: float = 0.5
    silence_ms: int = 700        # 静音多久判定一句话结束
    min_speech_ms: int = 300     # 短于此的语音段丢弃
    max_utterance_s: float = 30.0
    pre_roll_ms: int = 300       # 起始回溯,避免吃掉第一个字


@dataclass
class SttConfig:
    provider: str = "sensevoice"  # sensevoice | openai
    base_url: str = ""            # openai 兼容端点,如 https://newapi.example.com/v1
    api_key: str = ""             # 空则读环境变量 ANIMA_STT_API_KEY
    model: str = "whisper-1"
    language: str = ""            # 空=自动检测

    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get("ANIMA_STT_API_KEY", "")


@dataclass
class ScreenConfig:
    backend: str = "x11"         # x11 | none(Wayland portal 在 M2)
    monitor: int = 1             # mss 显示器序号(0=全部拼接)
    turn_frame_px: int = 768     # 回合帧长边像素(768≈258 tokens)
    jpeg_quality: int = 80


@dataclass
class GeminiConfig:
    model: str = "gemini-3.7-flash"
    api_key: str = ""            # 空则读 GEMINI_API_KEY / GOOGLE_API_KEY
    base_url: str = ""           # New API 网关地址(走 /v1beta 原生透传);空=直连 Google
    temperature: float = -1.0    # <0 表示用服务端默认

    def resolved_api_key(self) -> str:
        return (
            self.api_key
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )


@dataclass
class BrainConfig:
    provider: str = "gemini"     # gemini | openai(M2)
    max_history_turns: int = 20  # 滑动窗口保留的对话轮数
    attach_audio: bool = False   # Q20:原生音频增强开关(当回合附音频听语气)
    max_output_tokens: int = 1024
    gemini: GeminiConfig = field(default_factory=GeminiConfig)


@dataclass
class TtsConfig:
    provider: str = "edge"       # edge(目前唯一实现;provider 位可插拔)
    voice: str = "zh-CN-XiaoyiNeural"
    rate: str = "+0%"            # edge-tts 语速,如 +10%


@dataclass
class StateConfig:
    mode: str = "always_on"      # always_on/常开 | gated/门控 | wakeword/唤醒
    engaged_idle_timeout_s: float = 60.0
    wakeword: str = ""           # 空=用 core.name


@dataclass
class CalibrationConfig:
    turn_deg_per_sec: float = 90.0   # ⚠️ 实机标定:LookHorizontal 满轴角速度
    enable_look_pitch: bool = True   # ⚠️ /input/LookVertical 实测不通则关
    look_deg_per_sec: float = 90.0
    move_max_seconds: float = 5.0    # 单次 move 时长封顶(安全绳)


@dataclass
class ChatboxConfig:
    enabled: bool = True
    max_chars: int = 144
    notify_sound: bool = False


@dataclass
class LimitsConfig:
    daily_usd: float = 0.0       # 当日熔断线;默认 0=不设限,填数额才启用熔断
    max_reply_chars: int = 600   # 单次回复长度上限(防止长篇大论)


@dataclass
class CostsConfig:
    # gemini-3.7-flash 优惠价(2026-12-31 止);换模型请同步改
    input_per_mtok: float = 0.75
    output_per_mtok: float = 3.75


@dataclass
class EmoteDef:
    address: str                 # 如 /avatar/parameters/EmoteWave
    value: float = 1.0
    reset_value: float | None = None
    hold_ms: int = 0             # >0 时:发 value,等 hold_ms,再发 reset_value


@dataclass
class MemoryConfig:
    enabled: bool = True
    session_key: str = "vrchat:main"  # 兼容程序指向同一数据目录+同键即共享记忆


@dataclass
class AnimaConfig:
    core: CoreConfig = field(default_factory=CoreConfig)
    osc: OscConfig = field(default_factory=OscConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    state: StateConfig = field(default_factory=StateConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    chatbox: ChatboxConfig = field(default_factory=ChatboxConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    costs: CostsConfig = field(default_factory=CostsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    emotes: dict[str, EmoteDef] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return Path(self.core.data_dir)

    @property
    def wakeword(self) -> str:
        return self.state.wakeword or self.core.name


_SCALARS = (int, float, str, bool)


def _build(cls: type, raw: Any, where: str) -> Any:
    """按数据类字段逐个取值 + 类型校验;未知键报错(拼错键不该被静默吞掉)。"""
    if not isinstance(raw, dict):
        raise ConfigError(f"配置小节 [{where}] 应是表(table),得到 {type(raw).__name__}")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(raw) - set(fields)
    if unknown:
        raise ConfigError(
            f"配置小节 [{where}] 有未知键:{', '.join(sorted(unknown))}"
            f"(可用键:{', '.join(sorted(fields))})"
        )
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in raw:
            continue
        value = raw[name]
        if dataclasses.is_dataclass(f.type) or (
            isinstance(f.type, str) and f.type in _DATACLASS_NAMES
        ):
            sub_cls = f.type if isinstance(f.type, type) else _DATACLASS_NAMES[f.type]
            kwargs[name] = _build(sub_cls, value, f"{where}.{name}")
            continue
        expected = _expected_scalar(f)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if expected is not None and (
            not isinstance(value, expected) or isinstance(value, bool) is not (expected is bool)
        ):
            raise ConfigError(
                f"配置 [{where}].{name} 应为 {expected.__name__},"
                f"得到 {value!r}({type(value).__name__})"
            )
        kwargs[name] = value
    return cls(**kwargs)


def _expected_scalar(f: dataclasses.Field) -> type | None:
    """从字段注解推断标量类型;复杂类型(dict/Optional)返回 None 跳过校验。"""
    t = f.type
    if isinstance(t, str):
        t = {"int": int, "float": float, "str": str, "bool": bool}.get(t)
    return t if t in _SCALARS else None


def _build_emotes(raw: Any) -> dict[str, EmoteDef]:
    if not isinstance(raw, dict):
        raise ConfigError("配置小节 [emotes] 应是表:emote 名 → { address, value, ... }")
    emotes: dict[str, EmoteDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "address" not in spec:
            raise ConfigError(
                f"配置 [emotes].{name} 应形如 "
                '{ address = "/avatar/parameters/XXX", value = 1, hold_ms = 0 }'
            )
        emotes[name] = EmoteDef(
            address=str(spec["address"]),
            value=float(spec.get("value", 1.0)),
            reset_value=(
                float(spec["reset_value"]) if "reset_value" in spec else None
            ),
            hold_ms=int(spec.get("hold_ms", 0)),
        )
    return emotes


_DATACLASS_NAMES: dict[str, type] = {
    "GeminiConfig": GeminiConfig,
}


def load(path: str | Path | None) -> AnimaConfig:
    """从 TOML 文件加载配置;path=None 或文件不存在时返回全默认配置。"""
    raw: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if p.is_file():
            try:
                raw = tomllib.loads(p.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"配置文件 {p} 不是合法 TOML:{e}") from e
        elif str(path) != "config.toml":
            # 显式指定的路径必须存在;默认路径允许缺省
            raise ConfigError(f"配置文件不存在:{p}")

    cfg = AnimaConfig()
    for section_name in (
        "core", "osc", "audio", "vad", "stt", "screen", "brain",
        "tts", "state", "calibration", "chatbox", "limits", "costs", "memory",
    ):
        if section_name in raw:
            cls = type(getattr(cfg, section_name))
            setattr(cfg, section_name, _build(cls, raw[section_name], section_name))
    if "emotes" in raw:
        cfg.emotes = _build_emotes(raw["emotes"])

    unknown_sections = set(raw) - {
        "core", "osc", "audio", "vad", "stt", "screen", "brain", "tts",
        "state", "calibration", "chatbox", "limits", "costs", "memory", "emotes",
    }
    if unknown_sections:
        raise ConfigError(f"配置文件有未知小节:{', '.join(sorted(unknown_sections))}")

    _validate(cfg)
    return cfg


def _validate(cfg: AnimaConfig) -> None:
    mode = MODE_ALIASES.get(cfg.state.mode.strip().lower() if cfg.state.mode else "")
    if mode is None:
        mode = MODE_ALIASES.get(cfg.state.mode.strip())
    if mode is None:
        raise ConfigError(
            f"配置 [state].mode 应为 always_on/常开、gated/门控、wakeword/唤醒 之一,"
            f"得到 {cfg.state.mode!r}"
        )
    cfg.state.mode = mode

    if cfg.vad.backend not in ("silero", "energy"):
        raise ConfigError(f"配置 [vad].backend 应为 silero 或 energy,得到 {cfg.vad.backend!r}")
    if cfg.stt.provider not in ("sensevoice", "openai"):
        raise ConfigError(
            f"配置 [stt].provider 应为 sensevoice 或 openai,得到 {cfg.stt.provider!r}"
        )
    if cfg.screen.backend not in ("x11", "none"):
        raise ConfigError(
            f"配置 [screen].backend 应为 x11 或 none(Wayland portal 将在 M2 提供),"
            f"得到 {cfg.screen.backend!r}"
        )
    if cfg.brain.provider != "gemini":
        raise ConfigError(
            f"配置 [brain].provider 目前仅支持 gemini(openai 兼容路线在 M2),"
            f"得到 {cfg.brain.provider!r}"
        )
    if not 0 < cfg.screen.turn_frame_px <= 4096:
        raise ConfigError("配置 [screen].turn_frame_px 应在 1–4096 之间")
    if cfg.chatbox.max_chars > 144:
        raise ConfigError("配置 [chatbox].max_chars 不能超过 144(VRChat 聊天框硬上限)")
