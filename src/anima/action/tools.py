"""工具声明:模型能调用的动作(8 个身体 + 3 个记忆)。

说话不在这里——模型的文本输出就是说话(DESIGN.md Q13)。
Schema 用 Gemini 原生风格(type 大写)。emote 名单和 look_pitch 开关
来自运行时配置,所以声明由 build_tool_decls() 动态组装:
- 没配置任何 emote 就不声明 emote 工具(别引诱模型调不存在的东西)
- enable_look_pitch=false 时不声明 look_pitch
"""

from __future__ import annotations

MOTION_TOOLS = frozenset(
    {"move", "turn", "look_pitch", "jump", "set_run", "emote", "snapshot", "stop_all"}
)
MEMORY_TOOLS = frozenset({"memory_search", "memory_read", "memory_write"})


def build_tool_decls(
    emote_names: list[str], enable_look_pitch: bool
) -> list[dict]:
    decls: list[dict] = [
        {
            "name": "move",
            "description": (
                "朝一个方向移动若干秒(异步执行,立即返回;新的 move 会打断旧的)。"
                "在陌生环境请小步移动(1~2 秒)再看画面。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "direction": {
                        "type": "STRING",
                        "enum": ["forward", "back", "left", "right"],
                        "description": "forward=前进 back=后退 left=左平移 right=右平移",
                    },
                    "seconds": {
                        "type": "NUMBER",
                        "description": "持续秒数(0.2~5,超出会被截断)",
                    },
                },
                "required": ["direction", "seconds"],
            },
        },
        {
            "name": "turn",
            "description": "原地转身。正数向右转,负数向左转,单位是角度(如 90=右转四分之一圈)。",
            "parameters": {
                "type": "OBJECT",
                "properties": {"degrees": {"type": "NUMBER"}},
                "required": ["degrees"],
            },
        },
        {
            "name": "jump",
            "description": "跳一下。",
        },
        {
            "name": "set_run",
            "description": "切换奔跑/步行。on=true 之后的移动是跑步,false 恢复步行。",
            "parameters": {
                "type": "OBJECT",
                "properties": {"on": {"type": "BOOLEAN"}},
                "required": ["on"],
            },
        },
        {
            "name": "snapshot",
            "description": "拍一张当前视野的高清照片,马上回传给你看。想看清楚眼前的东西时用。",
        },
        {
            "name": "stop_all",
            "description": "立刻停止所有正在进行的移动和转身。",
        },
        {
            "name": "memory_search",
            "description": "在长期记忆里按关键词搜索,返回命中的文件与片段。",
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING"}},
                "required": ["query"],
            },
        },
        {
            "name": "memory_read",
            "description": "读取一条长期记忆的全文。path 用 [记忆索引] 里列出的相对路径。",
            "parameters": {
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING"}},
                "required": ["path"],
            },
        },
        {
            "name": "memory_write",
            "description": (
                "写入/更新一条长期记忆(遇到值得记住的人和事就用)。"
                "内容用 markdown,开头带 frontmatter,其中 description 是一句话摘要,"
                "会出现在 [记忆索引] 里。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": (
                            "文件名(不允许目录分隔符),字母数字点下划线连字符,"
                            "以 .md 结尾,如 friend_xiao_ming.md"
                        ),
                    },
                    "content": {"type": "STRING"},
                },
                "required": ["path", "content"],
            },
        },
    ]

    if enable_look_pitch:
        decls.insert(
            2,
            {
                "name": "look_pitch",
                "description": "抬头/低头(实验性,部分情况可能无效)。正数抬头,负数低头,单位角度。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"degrees": {"type": "NUMBER"}},
                    "required": ["degrees"],
                },
            },
        )

    if emote_names:
        decls.insert(
            2,
            {
                "name": "emote",
                "description": "播放一个动作表情。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING", "enum": list(emote_names)}
                    },
                    "required": ["name"],
                },
            },
        )

    return decls
