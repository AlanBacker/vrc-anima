"""对话历史:帧策略、裁剪、functionCall/Response 渲染。"""

from anima.brain.base import AssistantTurn, ToolCall, ToolResultTurn, UserTurn
from anima.brain.history import FRAME_OMITTED, History


def texts_of(content):
    return [p["text"] for p in content["parts"] if "text" in p]


def test_only_latest_frame_kept():
    h = History()
    h.add(UserTurn(text="第一句", frame_jpeg=b"OLD"))
    h.add(AssistantTurn(text="嗯"))
    h.add(UserTurn(text="第二句", frame_jpeg=b"NEW"))
    rendered = h.render()
    assert len(rendered) == 3
    # 旧帧被替换成占位文字
    assert FRAME_OMITTED in texts_of(rendered[0])
    assert not any("jpeg" in p for p in rendered[0]["parts"])
    # 新帧保留
    assert any(p.get("jpeg") == b"NEW" for p in rendered[2]["parts"])


def test_trim_starts_at_user_turn():
    h = History(max_user_turns=2)
    h.add(UserTurn(text="一"))
    h.add(AssistantTurn(text="1", tool_calls=[ToolCall("jump", {})]))
    h.add(ToolResultTurn(name="jump", result={"status": "ok"}))
    h.add(UserTurn(text="二"))
    h.add(AssistantTurn(text="2"))
    h.add(UserTurn(text="三"))
    turns = h.turns
    # 只剩 2 个 UserTurn,且第一条必须是 UserTurn(不能从孤儿 functionResponse 开始)
    assert isinstance(turns[0], UserTurn) and turns[0].text == "二"
    assert sum(isinstance(t, UserTurn) for t in turns) == 2


def test_tool_result_renders_as_tool_role():
    h = History()
    h.add(UserTurn(text="看看周围"))
    h.add(AssistantTurn(text="我看看", tool_calls=[ToolCall("snapshot", {}, "id1")]))
    h.add(
        ToolResultTurn(
            name="snapshot", result={"status": "ok"}, call_id="id1", frame_jpeg=b"HI"
        )
    )
    rendered = h.render()
    roles = [c["role"] for c in rendered]
    assert roles == ["user", "model", "tool", "user"]
    # model 内容带 functionCall
    call = next(p["call"] for p in rendered[1]["parts"] if "call" in p)
    assert call["name"] == "snapshot" and call["id"] == "id1"
    # tool 内容带 functionResponse
    resp = next(p["resp"] for p in rendered[2]["parts"] if "resp" in p)
    assert resp["name"] == "snapshot" and resp["result"] == {"status": "ok"}
    # 高清帧独立成一条 user 内容,且作为最新帧被保留
    assert any(p.get("jpeg") == b"HI" for p in rendered[3]["parts"])


def test_memory_index_attached_to_last_content_only():
    h = History()
    h.add(UserTurn(text="一"))
    h.add(AssistantTurn(text="1"))
    h.add(UserTurn(text="二"))
    rendered = h.render(memory_index="- [note](note.md) — 钩子")
    joined_last = "".join(texts_of(rendered[-1]))
    assert "记忆索引" in joined_last and "note.md" in joined_last
    for content in rendered[:-1]:
        assert "记忆索引" not in "".join(texts_of(content))


def test_state_line_composed():
    h = History()
    h.add(UserTurn(text="你好", state_line="着地=是"))
    rendered = h.render()
    joined = "".join(texts_of(rendered[0]))
    assert "[现场] 着地=是" in joined and "[听到] 你好" in joined


def test_empty_assistant_turn_skipped():
    h = History()
    h.add(UserTurn(text="……"))
    h.add(AssistantTurn(text="", tool_calls=[]))
    assert [c["role"] for c in h.render()] == ["user"]
