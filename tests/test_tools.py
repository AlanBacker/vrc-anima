"""工具声明的动态组装。"""

from anima.action.tools import MEMORY_TOOLS, MOTION_TOOLS, build_tool_decls


def names(decls):
    return [d["name"] for d in decls]


def test_no_emote_when_unconfigured():
    assert "emote" not in names(build_tool_decls([], True))


def test_emote_enum_lists_configured_names():
    decls = build_tool_decls(["wave", "clap"], True)
    emote = next(d for d in decls if d["name"] == "emote")
    assert emote["parameters"]["properties"]["name"]["enum"] == ["wave", "clap"]


def test_look_pitch_gated_by_config():
    assert "look_pitch" in names(build_tool_decls([], True))
    assert "look_pitch" not in names(build_tool_decls([], False))


def test_memory_tools_declared():
    assert MEMORY_TOOLS <= set(names(build_tool_decls([], True)))


def test_all_names_known_and_unique():
    decls = build_tool_decls(["wave"], True)
    n = names(decls)
    assert len(n) == len(set(n))
    assert set(n) <= MOTION_TOOLS | MEMORY_TOOLS


def test_schema_types_gemini_uppercase():
    for decl in build_tool_decls(["wave"], True):
        params = decl.get("parameters")
        if not params:
            continue  # 无参工具(jump/snapshot/stop_all)可以不带 schema
        assert params["type"] == "OBJECT"
        for prop in params["properties"].values():
            assert prop["type"] in {"STRING", "NUMBER", "BOOLEAN", "INTEGER"}
        for req in params.get("required", []):
            assert req in params["properties"]
