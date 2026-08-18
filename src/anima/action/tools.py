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
                "朝一个方向平移若干秒(left/right 是横着挪,不是转身;转身用 turn)。"
                "异步执行:返回『已开始』不代表已到达,走完想确认位置就再 snapshot。"
                "新的 move 会打断进行中的 move。陌生环境小步走(1~2 秒)看一眼再继续,"
                "免得撞墙或掉下去。"
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
            "description": (
                "原地转身,单位角度:正=右转(顺时针),负=左转;"
                "90=右转四分之一圈,180=转身向后,±720 以内。按标定速度换算成"
                "按轴时长,异步执行;转完想核对朝向就 snapshot。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "degrees": {
                        "type": "NUMBER",
                        "description": "转多少度:正右负左,±720 以内",
                    }
                },
                "required": ["degrees"],
            },
        },
        {
            "name": "jump",
            "description": "原地跳一下。可以用来表达情绪(开心蹦一下)或引起注意。",
        },
        {
            "name": "set_run",
            "description": (
                "切换移动速度档:on=true 后续 move 都是跑步,on=false 恢复走路。"
                "这是持续状态,记得跑完调回来,贴着人跑来跑去很吓人。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {"on": {"type": "BOOLEAN"}},
                "required": ["on"],
            },
        },
        {
            "name": "snapshot",
            "description": (
                "拍一张你当前第一人称视野的高清照片。照片会在本轮工具结果后"
                "回传给你,下一轮你就能描述看到的东西——想看清眼前的人、文字、"
                "环境细节,或移动/转身后确认新位置时用。"
            ),
        },
        {
            "name": "stop_all",
            "description": (
                "立刻停止一切进行中的移动和转身(急刹)。用户喊『停』『别动』,"
                "或你发现快撞上东西/走出预期路线时马上调用。"
            ),
        },
        {
            "name": "memory_search",
            "description": (
                "在长期记忆里全文搜索,返回命中的文件路径和上下文片段。"
                "多个关键词用空格隔开。别人提到你似曾相识的人名、地点、约定时,"
                "先搜再回答,别硬编。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING"}},
                "required": ["query"],
            },
        },
        {
            "name": "memory_read",
            "description": (
                "读取一条长期记忆的完整内容。索引只是目录:回答涉及某人某事的"
                "细节前、更新某条记忆前,先读对应文件,别凭索引行猜。"
                "path 省略时读 MEMORY.md 索引全文(索引被截断时用)。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "文件名,如 friend_xiao_ming.md;省略=读索引全文",
                    }
                },
            },
        },
        {
            "name": "memory_write",
            "description": (
                "写入、修改或删除一条长期记忆(整文件覆盖;修改=先 memory_read "
                "读出旧文改写后重写入;删除=delete 设 true)。值得记:人的名字/"
                "长相/喜好、约定、重要事件、去过的世界;不值得:寒暄和闲聊流水账。"
                "写前先查重,同一个人同一件事始终更新同一个文件,不另建重复文件。"
                "格式:开头 frontmatter(---、description: 一句话摘要——它会进 "
                "[记忆索引] 也是搜索的依据、---),然后 markdown 正文。"
                "MEMORY.md 索引自动维护,不可直接写。"
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": (
                            "文件名(不允许目录分隔符),中文/字母/数字/点/下划线/"
                            "连字符,以 .md 结尾;记人用 user-<名牌名>.md,"
                            "如 user-小明.md"
                        ),
                    },
                    "content": {
                        "type": "STRING",
                        "description": "完整文件内容,含 frontmatter;delete=true 时可省略",
                    },
                    "delete": {
                        "type": "BOOLEAN",
                        "description": "true=删除该文件并移除其索引行,忽略 content",
                    },
                },
                "required": ["path"],
            },
        },
    ]

    if enable_look_pitch:
        decls.insert(
            2,
            {
                "name": "look_pitch",
                "description": (
                    "视线抬头/低头,单位角度:正=抬头看上方,负=低头看下方,"
                    "±90 以内。实验性:部分世界/相机模式下不生效——调用后画面"
                    "没变化就别反复重试,口头说明看不了就好。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "degrees": {
                            "type": "NUMBER",
                            "description": "抬/低多少度:正上负下,±90 以内",
                        }
                    },
                    "required": ["degrees"],
                },
            },
        )

    if emote_names:
        decls.insert(
            2,
            {
                "name": "emote",
                "description": (
                    "播放一个 Avatar 动作表情(挥手、跳舞等,可选名单见 enum)。"
                    "部分表情会持续几秒,期间照常可以说话。"
                ),
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
