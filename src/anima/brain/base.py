"""大脑层数据模型:与具体 SDK 解耦的回合表示。

设计要点(DESIGN.md Q13):模型的文本输出就是说话内容,说话不是工具;
工具调用立即返回、本地异步执行,结果作为 functionResponse 附在历史里
随下一回合送出,不额外烧一次 API(唯一例外:snapshot 触发立即追问)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass
class ToolCall:
    name: str
    args: dict
    call_id: str = ""
    # Gemini 3 系思考签名:响应里 functionCall 部件自带,回传历史时必须
    # 原样带回,缺了会被 400(strict validation)
    thought_signature: bytes | None = None


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    output_tokens: int = 0


@dataclass
class UserTurn:
    """一次"听到的话":STT 转写 + 现场状态 + 回合画面帧(可选原始音频)。"""

    text: str
    state_line: str = ""
    frame_jpeg: bytes | None = None
    audio_wav: bytes | None = None  # brain.attach_audio 开关打开时才带


@dataclass
class AssistantTurn:
    """模型的回应:text 即语音内容;tool_calls 是要做的动作。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    text_signature: bytes | None = None  # 文本部件的思考签名(有就回传)


@dataclass
class ToolResultTurn:
    """工具执行的立即回执;snapshot 的高清帧也挂在这里。"""

    name: str
    result: dict
    call_id: str = ""
    frame_jpeg: bytes | None = None


Turn = Union[UserTurn, AssistantTurn, ToolResultTurn]


@dataclass
class BrainReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    text_signature: bytes | None = None
