"""记忆服务:写→索引同步→读→搜,路径约束。"""

from anima.brain.base import ToolCall
from anima.memory.service import MemoryService

CONTENT = """---
name: friend-xiao-ming
description: 小明喜欢猫,常去 The Black Cat
---

小明是在 The Black Cat 认识的朋友,养了两只猫。
"""


def make_service(tmp_path):
    return MemoryService(tmp_path)


async def test_write_syncs_index(tmp_path):
    svc = make_service(tmp_path)
    assert await svc.index_text() == ""  # 空库
    result = await svc.run_tool(
        ToolCall("memory_write", {"path": "friend_xiao_ming.md", "content": CONTENT})
    )
    assert result["status"] == "ok"
    index = await svc.index_text()
    assert "friend_xiao_ming.md" in index
    assert "小明喜欢猫" in index  # frontmatter description 成为索引钩子


async def test_read_back(tmp_path):
    svc = make_service(tmp_path)
    await svc.run_tool(
        ToolCall("memory_write", {"path": "note.md", "content": CONTENT})
    )
    result = await svc.run_tool(ToolCall("memory_read", {"path": "note.md"}))
    assert result["status"] == "ok"
    assert "养了两只猫" in result["content"]


async def test_read_missing_is_error(tmp_path):
    svc = make_service(tmp_path)
    result = await svc.run_tool(ToolCall("memory_read", {"path": "nope.md"}))
    assert result["status"] == "error"
    assert "没有这个记忆文件" in result["detail"]


async def test_search(tmp_path):
    svc = make_service(tmp_path)
    await svc.run_tool(
        ToolCall("memory_write", {"path": "note.md", "content": CONTENT})
    )
    hit = await svc.run_tool(ToolCall("memory_search", {"query": "两只猫"}))
    assert hit["status"] == "ok"
    assert any("note.md" in r for r in hit["results"])
    miss = await svc.run_tool(ToolCall("memory_search", {"query": "不存在的词xyz"}))
    assert miss["status"] == "ok" and "没有找到" in miss["detail"]


async def test_directory_traversal_rejected(tmp_path):
    svc = make_service(tmp_path)
    result = await svc.run_tool(
        ToolCall("memory_write", {"path": "../evil.md", "content": "x"})
    )
    assert result["status"] == "error"
    assert "不合法" in result["detail"]
    # 作用域目录之外没有落盘任何文件
    assert not (tmp_path / "memory" / "session" / "evil.md").exists()
    assert not (tmp_path / "evil.md").exists()


async def test_index_not_directly_writable(tmp_path):
    svc = make_service(tmp_path)
    result = await svc.run_tool(
        ToolCall("memory_write", {"path": "MEMORY.md", "content": "篡改索引"})
    )
    assert result["status"] == "error"


async def test_missing_args_are_errors(tmp_path):
    svc = make_service(tmp_path)
    assert (await svc.run_tool(ToolCall("memory_read", {})))["status"] == "error"
    assert (await svc.run_tool(ToolCall("memory_search", {"query": " "})))[
        "status"
    ] == "error"
    assert (await svc.run_tool(ToolCall("memory_write", {"path": "a.md"})))[
        "status"
    ] == "error"
    assert (await svc.run_tool(ToolCall("不存在", {})))["status"] == "error"
