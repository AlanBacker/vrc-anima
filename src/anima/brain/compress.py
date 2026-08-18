"""上下文压缩:滚动摘要 + 压缩时联动提取长期记忆。

改编自 AlanBacker 的 astrbot_plugin_memory_beyond(core/prompts.py 的
摘要提示词、响应解析与记忆文件渲染;MIT License,声明见根目录 NOTICE)。
与上游的差异都是平台适配:
- 转写材料来自 Anima 自己的 Turn 对象,不是 OpenAI 消息 dict
- 记人锚点从 QQ 数字 ID 换成 VRChat 头顶名牌显示名(允许中文 slug)
- 摘要小节按具身陪聊场景调整(在场的人/约定/去过哪,而非工具输出)
上游的水位线状态机与 token 估算器没有搬:AstrBot 的历史归平台管、
每轮被回存所以要锚点对账,估算是因为拿不到实报用量;Anima 的 History
归自己管,压缩就是"摘掉旧轮、换上摘要"一步到位,token 用 Gemini
每回合实报的 prompt_tokens,不用猜。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .base import AssistantTurn, ToolResultTurn, Turn, UserTurn

MAX_EXTRACTED_MEMORIES = 5
MAX_MEMORY_CONTENT_CHARS = 2000
MAX_MEMORY_DESC_CHARS = 120

# 上游是纯 ASCII kebab;VRChat 名牌多为中日文,放宽到 \w(含 CJK)
_SLUG_RE = re.compile(r"^[\w][\w-]{0,60}$")
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S | re.I)
_MEMORIES_RE = re.compile(r"<memories>(.*?)</memories>", re.S | re.I)
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.M)

SUMMARY_PROMPT = """你是对话上下文压缩器。请把下面这段 VRChat 里的语音对话压缩成一份结构化摘要,\
它将替代对话原文继续充当上下文,后续对话只能依靠它了解这段历史。

保留:在场的人(名牌名)与关系、聊过的话题与关键信息、做过的约定与承诺、\
去过的地方与做过的动作、正在进行的事。
丢弃:逐字对话、寒暄、动作工具的回执细节。

已有摘要(非空时表示更早的对话已被压缩过;把其中仍然有效的信息合并进新摘要,不要丢失):
{previous_summary}

待压缩的对话:
{transcript}

把摘要放在 <summary></summary> 标签内,按以下小节组织(无内容的小节可省略):
## 在场的人
## 聊了什么
## 约定与待办
## 做了什么、去了哪
## 正在进行"""

EXTRACT_INSTRUCTIONS = """

另外:这次压缩会让上述对话的细节从上下文中淡出,请顺手甄别其中值得长期保留的事实,\
放入 <memories></memories> 标签内的 JSON 数组(与 <summary> 并列输出)。数组元素格式:
{"type": "user 或 project 或 feedback", "name": "文件名主干", "description": "一句话钩子", "content": "事实正文"}
- user:关于某个人的事实,name 用 user-<名牌名>(以头顶名牌显示名为锚,自称昵称写进正文);\
project:进行中的事、约定(相对日期转绝对日期);feedback:别人对你行为方式的指导(附原因)
- 只收长期有效、下次见面仍有价值的事实;只对本段对话有意义的不收;与主线无关的他人隐私不收
- 没有值得保留的就输出空数组 []"""


def render_transcript(turns: list[Turn], bot_name: str) -> str:
    """把待压缩的回合渲染成给摘要模型看的纯文本对话稿。"""
    lines: list[str] = []
    for turn in turns:
        if isinstance(turn, UserTurn):
            if turn.text:
                lines.append(f"对方:{turn.text}")
        elif isinstance(turn, AssistantTurn):
            if turn.text:
                lines.append(f"{bot_name}:{turn.text}")
            if turn.tool_calls:
                acts = "、".join(c.name for c in turn.tool_calls)
                lines.append(f"({bot_name} 做了动作:{acts})")
        elif isinstance(turn, ToolResultTurn):
            continue  # 回执细节对摘要没价值,动作本身已在上一行
    return "\n".join(lines)


def build_summary_prompt(
    previous_summary: str, transcript: str, extract_memories: bool
) -> str:
    prompt = SUMMARY_PROMPT.format(
        previous_summary=previous_summary.strip() or "(无)",
        transcript=transcript,
    )
    if extract_memories:
        prompt += EXTRACT_INSTRUCTIONS
    return prompt


# ---------------------------------------------------------------- 响应解析


@dataclass
class MemoryDraft:
    """压缩时抽取出的一条待写入记忆。"""

    type: str
    name: str
    description: str
    content: str

    @property
    def filename(self) -> str:
        return f"{self.name}.md"


def _slugify(raw: str) -> str:
    slug = re.sub(r"[\s_]+", "-", str(raw).strip().lower())
    slug = re.sub(r"[^\w-]", "", slug).strip("-")[:60]
    return slug if slug and _SLUG_RE.match(slug) else ""


def _parse_memory_entries(raw_json: str) -> list[MemoryDraft]:
    text = _FENCE_RE.sub("", raw_json).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return []
    if not isinstance(data, list):
        return []

    drafts: list[MemoryDraft] = []
    for entry in data:
        if len(drafts) >= MAX_EXTRACTED_MEMORIES or not isinstance(entry, dict):
            continue
        mem_type = str(entry.get("type", "")).strip().lower()
        if mem_type not in ("user", "project", "feedback"):
            continue
        name = _slugify(entry.get("name", ""))
        content = str(entry.get("content", "")).strip()
        if not name or not content:
            continue
        description = " ".join(str(entry.get("description", "")).split())
        drafts.append(
            MemoryDraft(
                type=mem_type,
                name=name,
                description=description[:MAX_MEMORY_DESC_CHARS] or name,
                content=content[:MAX_MEMORY_CONTENT_CHARS],
            )
        )
    return drafts


def parse_summary_response(text: str) -> tuple[str, list[MemoryDraft]]:
    """从摘要模型的输出里解出 (摘要正文, 抽取的记忆列表),尽量容错。"""
    if not text or not text.strip():
        return "", []

    memories: list[MemoryDraft] = []
    memories_match = _MEMORIES_RE.search(text)
    if memories_match:
        memories = _parse_memory_entries(memories_match.group(1))

    summary_match = _SUMMARY_RE.search(text)
    if summary_match:
        summary = summary_match.group(1).strip()
    else:
        # 模型没按格式输出标签:去掉 memories 块后整体当摘要
        summary = _MEMORIES_RE.sub("", text).strip()
        summary = _FENCE_RE.sub("", summary).strip()
    return summary, memories


def render_memory_file(draft: MemoryDraft) -> str:
    """渲染记忆文件:frontmatter(name / description / metadata.type)+ 正文。"""
    return (
        "---\n"
        f"name: {draft.name}\n"
        f"description: {draft.description}\n"
        "metadata:\n"
        f"  type: {draft.type}\n"
        "---\n\n"
        f"{draft.content}\n"
    )
