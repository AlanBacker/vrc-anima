# vrc-anima

让一个 AI 以具身形态生活在 VRChat 里:她通过屏幕看世界、通过麦克风听人说话、用自己的声音回答、用 OSC 移动身体,还带一份跨会话的长期记忆。

- **不改客户端、不用 headless**:跑的是 Steam 上原版 VRChat(Linux 下经 Proton),只用官方开放的 OSC 接口和屏幕/音频采集,不注入、不修改游戏,遵守 VRChat 服务条款与社区准则。
- **级联管线,回合制**:VAD 断句 → STT 转写 → 多模态大模型(文字 + 当前画面帧)→ 文本回复即语音(TTS 朗读 + 聊天框字幕)+ 动作工具调用。模型说的话就是嘴里说出的话,说话不是工具。
- **省 tokens 的设计**:语音默认走 STT 文字(原始音频只是可选开关);历史里只保留最新一帧画面,旧帧自动替换为占位符;记忆只注入索引、正文按需用工具读。

设计定稿见 [DESIGN.md](DESIGN.md)(20 个决策问答,含实机验证清单)。

## 架构一览

```
VRChat(Proton,原版客户端,--osc=9000:127.0.0.1:9001)
   │ 画面(mss 截屏)          │ 声音(PipeWire 虚拟声卡)
   ▼                          ▼
ScreenGrabber            MicCapture ── VAD(silero/energy)── 断句
   │                                                          │
   │            ┌─────────────────────────────────────────────┘
   ▼            ▼ STT(SenseVoice 本地 / OpenAI 兼容接口)
 [现场]状态行 + [听到]转写 + 画面帧 + [记忆索引]
                │
                ▼
        gemini-3.7-flash(级联大脑)
                │
    ┌───────────┴───────────────┐
    ▼ 文本 = 说话               ▼ 工具调用
 TTS(edge-tts)→ 虚拟麦克风   move/turn/jump/emote/snapshot/记忆…
 聊天框字幕镜像                 │ OSC /input/*(轴看门狗自动归零)
    └── 半双工:说话时关闭采集门,防止自己听自己
```

## 安装

系统要求:Linux(X11 会话用于截屏),Python ≥ 3.11,`ffmpeg`(TTS 解码),PipeWire(虚拟声卡)。

```bash
sudo apt install ffmpeg          # TTS 播放依赖
python3 -m venv .venv
.venv/bin/pip install -e .
# 可选:本地 STT(SenseVoice-small,中文效果好,无需联网)
.venv/bin/pip install -e ".[stt-local]"
# 开发:测试依赖
.venv/bin/pip install -e ".[dev]"
```

### 虚拟声卡(PipeWire)

Anima 需要「听到游戏声音、并把 TTS 说进游戏麦克风」,用两个 null-sink 桥接:

```bash
# 游戏声音 → Anima 的耳朵
pactl load-module module-null-sink sink_name=anima_ears sink_properties=device.description=AnimaEars
# Anima 的嘴 → 游戏麦克风
pactl load-module module-null-sink sink_name=anima_mouth sink_properties=device.description=AnimaMouth
```

然后在 `pavucontrol` 里把 VRChat 的输出指到 `AnimaEars`,VRChat 的输入(录音)指到 `AnimaMouth` 的 monitor;`config.toml` 里 `[audio]` 的 `input_device` 填 `AnimaEars` 的 monitor,`output_device` 填 `AnimaMouth`。

### VRChat 侧设置

1. Steam 启动参数加:`--osc=9000:127.0.0.1:9001`
2. 游戏内 Action Menu → Options → OSC → **Enabled**
3. 游戏内把 **Toggle Voice 关掉**(默认即关):Anima 用 `/input/Voice` 按「按住说话」语义开闭麦
4. 分辨率建议窗口化,确保截屏拿到的是游戏画面

## 配置

```bash
cp config.example.toml config.toml
cp persona.example.md persona.md      # 人设正文,完全由你掌控
export GEMINI_API_KEY=...             # 或 config.toml [brain.gemini] api_key
# 用 OpenAI 兼容 STT 时:export ANIMA_STT_API_KEY=...
```

关键配置项(全量见 `config.example.toml` 注释):

| 段 | 项 | 说明 |
|---|---|---|
| `[core]` | `name` | 她的名字,也是唤醒词 |
| `[brain.gemini]` | `model` / `base_url` | 默认 `gemini-3.7-flash`;经 New API 等网关时填网关地址 |
| `[stt]` | `provider` | `sensevoice`(本地)/ `openai`(兼容接口)/ `astrbot`(M3 桥接) |
| `[state]` | `mode` | `always_on` 常开 / `gated` 门控 / `wakeword` 唤醒词 |
| `[limits]` | `daily_usd` | 每日预算熔断,默认 0=不设限;填数额则超线暂停回应(`budget reset` 解除) |
| `[calibration]` | `turn_deg_per_sec` | 转身标定:实测你的灵敏度后填入 |
| `[emotes]` | — | 你的 Avatar 表情参数表,配了才会声明 emote 工具 |

## 运行

```bash
.venv/bin/anima                # 读当前目录 config.toml
.venv/bin/anima --config /path/to/config.toml --log-level DEBUG
```

启动后进入控制台(stdin):

| 命令 | 作用 |
|---|---|
| `state` | 当前状态(参与阶段/成本/动作/记忆) |
| `stop` / `停` | 停止所有动作 |
| `panic` / `急停` | 急停:静音 + 归零 + PanicButton |
| `mute on|off` | 强制闭麦/恢复 |
| `say 你好` / `说 你好` | 手动让她说话 |
| `cost` / `成本` | 今日花费 |
| `budget reset` | 解除预算熔断 |
| `memory` / `记忆` | 记忆索引一览 |
| `quit` / `退出` | 退出 |

**降级策略**(缺什么关什么,不崩):没有麦克风 → 只剩控制台;ffmpeg/声卡缺失 → 只打字幕不出声;截屏失败 → 纯文字回合;silero 模型下载失败 → 能量 VAD;OSC 接收端口被占 → 无状态感知。

## 成本

`gemini-3.7-flash` 按 $0.75 / $3.75 每百万 tokens 计。一个典型回合(768px 一帧 + 文字)约 1.5k 输入 + 100 输出 tokens ≈ **$0.0015/回合**;连续闲聊一小时(百余回合)约 $0.2。控制台 `cost` 随时看今日花费;担心失控(bug 死循环、被人刷话)可在 `[limits]` 填 `daily_usd` 启用每日熔断,默认不设限。

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
```

纯逻辑模块(断句、历史帧策略、执行器抢占、聊天框限速、记忆库、状态机、成本计)全部离线可测,不需要 VRChat 或 API key。实机验证按 [DESIGN.md](DESIGN.md) §12 的清单逐项做。

## 路线图

- **M1(当前)**:Proton + OSC 跑通;双向音频;VAD→STT→大脑→TTS 回合环;动作工具;CLI 控制台;半双工回声抑制
- **M2**:门控参与(旁听判断)、说话人分离、移动避障、Live API 低延迟通道(可选备用)
- **M3**:AstrBot 桥接——共用 STT/TTS/记忆配置,VRChat 里认识的人 QQ 上也记得

## 许可证与致谢

- 本项目:[Apache-2.0](LICENSE)
- `src/anima/memory/memstore.py` 移植自 [astrbot_plugin_memory_beyond](https://github.com/AlanBacker/astrbot_plugin_memory_beyond)(MIT © 2026 AlanBacker),完整声明见 [NOTICE](NOTICE)

## AI 披露与隐私

- 她是 AI,被问到会大方承认,不冒充人类;不骚扰、不刷屏,对方要求停止立即照做
- 屏幕帧与语音转写只用于当回合推理,不落盘;长期记忆仅保存模型主动写下的事实文件,明文存放在 `data/memory/`,随时可查可删
- 请在遵守 VRChat 服务条款、社区准则及所在世界规则的前提下使用
