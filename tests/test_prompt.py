"""系统提示词拼装。"""

from anima.brain.prompt import build_system_prompt


def test_name_injected_and_no_persona_section():
    p = build_system_prompt("小星", "")
    assert "「小星」" in p
    assert "你的人设" not in p


def test_persona_appended_after_frame_rules():
    p = build_system_prompt("Anima", "傲娇但热心,口头禅是「哼」。")
    assert "# 你的人设" in p and "傲娇但热心" in p
    assert p.index("# 边界") < p.index("# 你的人设")  # 框架规则在前,人设在后


def test_core_rules_present():
    p = build_system_prompt("Anima", "")
    for keyword in ("markdown", "语音识别", "snapshot", "memory_write", "AI"):
        assert keyword in p
