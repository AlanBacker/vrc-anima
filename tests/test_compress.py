"""上下文压缩:对话稿渲染、提示词组装、响应解析、记忆文件渲染。"""

from anima.brain.base import AssistantTurn, ToolCall, ToolResultTurn, UserTurn
from anima.brain.compress import (
    MAX_EXTRACTED_MEMORIES,
    MAX_MEMORY_CONTENT_CHARS,
    build_summary_prompt,
    parse_summary_response,
    render_memory_file,
    render_transcript,
)


def test_render_transcript_roles_and_actions():
    turns = [
        UserTurn(text="带我去看喷泉"),
        AssistantTurn(
            text="走吧", tool_calls=[ToolCall("move", {}), ToolCall("jump", {})]
        ),
        ToolResultTurn(name="move", result={"status": "ok"}),
        AssistantTurn(text="", tool_calls=[ToolCall("snapshot", {})]),
        UserTurn(text="就是这里"),
    ]
    out = render_transcript(turns, "Cahill")
    lines = out.splitlines()
    assert lines[0] == "对方:带我去看喷泉"
    assert lines[1] == "Cahill:走吧"
    assert lines[2] == "(Cahill 做了动作:move、jump)"
    assert lines[3] == "(Cahill 做了动作:snapshot)"  # 无文本回合只留动作行
    assert lines[4] == "对方:就是这里"
    assert "status" not in out  # 工具回执细节不进对话稿


def test_build_summary_prompt_previous_and_extract_toggle():
    p = build_summary_prompt("", "对方:你好", extract_memories=False)
    assert "(无)" in p and "对方:你好" in p
    assert "<memories>" not in p  # 不提取时不给 JSON 指引
    p2 = build_summary_prompt("## 在场的人\n小明", "对方:你好", extract_memories=True)
    assert "小明" in p2 and "<memories>" in p2 and "user-<名牌名>" in p2


def test_parse_tagged_summary_and_memories():
    text = """<summary>
## 在场的人
小明:名牌 小明,养猫
</summary>
<memories>
[{"type": "user", "name": "user-小明", "description": "养两只猫", "content": "小明养了两只猫。"}]
</memories>"""
    summary, drafts = parse_summary_response(text)
    assert summary.startswith("## 在场的人")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.type == "user" and d.name == "user-小明"
    assert d.filename == "user-小明.md"
    rendered = render_memory_file(d)
    assert rendered.startswith("---\nname: user-小明\n")
    assert "description: 养两只猫" in rendered
    assert "type: user" in rendered and rendered.endswith("小明养了两只猫。\n")


def test_parse_tagless_fallback_and_fenced_memories():
    """模型没按标签输出:去掉 memories 块后整体当摘要;JSON 带 ``` 围栏也能解。"""
    text = """聊了猫和喷泉。
<memories>
```json
[{"type": "project", "name": "Fountain Trip", "description": "约了周六", "content": "约好 2026-08-22 去喷泉广场。"}]
```
</memories>"""
    summary, drafts = parse_summary_response(text)
    assert summary == "聊了猫和喷泉。"
    assert len(drafts) == 1
    assert drafts[0].name == "fountain-trip"  # 空格转连字符、小写化


def test_parse_rejects_garbage_entries():
    text = """<summary>摘要</summary>
<memories>
[{"type": "secret", "name": "a", "content": "越权类型"},
 {"type": "user", "name": "", "content": "没名字"},
 {"type": "user", "name": "ok-name", "content": ""},
 "不是字典",
 {"type": "feedback", "name": "说话慢点", "description": "", "content": "说话别太快。"}]
</memories>"""
    summary, drafts = parse_summary_response(text)
    assert summary == "摘要"
    assert [d.name for d in drafts] == ["说话慢点"]
    assert drafts[0].description == "说话慢点"  # 空 description 回退到 name


def test_parse_broken_json_and_empty_input():
    assert parse_summary_response("") == ("", [])
    summary, drafts = parse_summary_response(
        "<summary>还行</summary><memories>这不是 JSON</memories>"
    )
    assert summary == "还行" and drafts == []
    # 前后带杂讯但方括号切片能救回来
    summary, drafts = parse_summary_response(
        '<summary>s</summary><memories>好的,数组如下:'
        '[{"type":"user","name":"n","content":"c"}] 完毕</memories>'
    )
    assert len(drafts) == 1 and drafts[0].name == "n"


def test_extracted_memories_capped_and_truncated():
    import json

    entries = [
        {"type": "user", "name": f"p{i}", "content": "x" * (MAX_MEMORY_CONTENT_CHARS + 500)}
        for i in range(MAX_EXTRACTED_MEMORIES + 3)
    ]
    text = f"<summary>s</summary><memories>{json.dumps(entries)}</memories>"
    _, drafts = parse_summary_response(text)
    assert len(drafts) == MAX_EXTRACTED_MEMORIES
    assert all(len(d.content) == MAX_MEMORY_CONTENT_CHARS for d in drafts)
