"""对话历史:裁剪与"只留最新一帧"的画面策略。

烧钱大头是历史里的图片(每帧约 258 tokens,还会每回合重复计费),
所以 render() 只保留最新一张画面帧,更旧的替换成一行占位文字;
记忆索引只挂在最后一条内容上,不随历史滚雪球。

render() 输出的是中立结构(dict),不依赖任何 SDK:
  {"role": "user"|"model"|"tool", "parts": [
      {"text": str} | {"jpeg": bytes} | {"wav": bytes}
      | {"call": {"name","args","id"}} | {"resp": {"name","result","id"}}
  ]}
由 brain/gemini.py 负责翻译成 google-genai 的类型。
"""

from __future__ import annotations

from .base import AssistantTurn, ToolResultTurn, Turn, UserTurn

FRAME_OMITTED = "[旧画面帧已省略]"


class History:
    def __init__(self, max_user_turns: int = 20):
        self._max_user_turns = max_user_turns
        self._turns: list[Turn] = []

    def add(self, turn: Turn) -> None:
        self._turns.append(turn)
        self._trim()

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    # ------------------------------------------------------------ 裁剪

    def _trim(self) -> None:
        """按 UserTurn 数量截断,永远从一个 UserTurn 开始(别把
        functionCall/functionResponse 劈成两半)。"""
        user_indices = [
            i for i, t in enumerate(self._turns) if isinstance(t, UserTurn)
        ]
        if len(user_indices) <= self._max_user_turns:
            return
        cut = user_indices[len(user_indices) - self._max_user_turns]
        self._turns = self._turns[cut:]

    # ------------------------------------------------------------ 渲染

    def render(self, memory_index: str = "") -> list[dict]:
        contents: list[dict] = []
        for turn in self._turns:
            contents.extend(self._render_turn(turn))
        self._apply_frame_policy(contents)
        if memory_index and contents:
            contents[-1]["parts"].append(
                {"text": f"[记忆索引]\n{memory_index}"}
            )
        return contents

    @staticmethod
    def _render_turn(turn: Turn) -> list[dict]:
        if isinstance(turn, UserTurn):
            lines = []
            if turn.state_line:
                lines.append(f"[现场] {turn.state_line}")
            lines.append(f"[听到] {turn.text}")
            parts: list[dict] = [{"text": "\n".join(lines)}]
            if turn.frame_jpeg is not None:
                parts.append({"jpeg": turn.frame_jpeg})
            if turn.audio_wav is not None:
                parts.append({"wav": turn.audio_wav})
            return [{"role": "user", "parts": parts}]

        if isinstance(turn, AssistantTurn):
            parts = []
            if turn.text:
                parts.append({"text": turn.text})
            for call in turn.tool_calls:
                parts.append(
                    {"call": {"name": call.name, "args": call.args, "id": call.call_id}}
                )
            if not parts:  # 空回应(理论上不会有):跳过,Gemini 不收空 parts
                return []
            return [{"role": "model", "parts": parts}]

        if isinstance(turn, ToolResultTurn):
            out: list[dict] = [
                {
                    "role": "tool",
                    "parts": [
                        {
                            "resp": {
                                "name": turn.name,
                                "result": turn.result,
                                "id": turn.call_id,
                            }
                        }
                    ],
                }
            ]
            if turn.frame_jpeg is not None:
                # 高清帧不能塞进 functionResponse,单独補一条 user 内容
                out.append(
                    {
                        "role": "user",
                        "parts": [
                            {"text": f"[{turn.name} 拍到的画面]"},
                            {"jpeg": turn.frame_jpeg},
                        ],
                    }
                )
            return out

        raise TypeError(f"未知回合类型:{type(turn)!r}")

    @staticmethod
    def _apply_frame_policy(contents: list[dict]) -> None:
        """从后往前,第一张 jpeg 保留,其余替换成占位文字(原地修改)。"""
        latest_kept = False
        for content in reversed(contents):
            new_parts = []
            for part in content["parts"]:
                if "jpeg" in part:
                    if latest_kept:
                        new_parts.append({"text": FRAME_OMITTED})
                        continue
                    latest_kept = True
                new_parts.append(part)
            content["parts"] = new_parts
