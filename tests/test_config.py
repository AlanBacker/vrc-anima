"""配置层:默认值、别名归一、错误信息。"""

import pytest

from anima.config import ConfigError, load


def test_defaults_without_file():
    cfg = load(None)
    assert cfg.core.name == "Anima"
    assert cfg.osc.send_port == 9000
    assert cfg.state.mode == "always_on"
    assert cfg.brain.gemini.model == "gemini-3.7-flash"
    assert cfg.wakeword == "Anima"  # 未配置唤醒词时用名字


def test_chinese_mode_alias(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[state]\nmode = "常开"\n', encoding="utf-8")
    assert load(p).state.mode == "always_on"
    p.write_text('[state]\nmode = "唤醒"\n', encoding="utf-8")
    assert load(p).state.mode == "wakeword"


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[osc]\nsend_prot = 9000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="未知键"):
        load(p)


def test_unknown_section_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[oscc]\nsend_port = 9000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="未知小节"):
        load(p)


def test_chatbox_hard_limit(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[chatbox]\nmax_chars = 200\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="144"):
        load(p)


def test_type_mismatch_message(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[osc]\nsend_port = "九千"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="send_port"):
        load(p)


def test_nested_gemini_and_emotes(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        "\n".join(
            [
                "[brain.gemini]",
                'model = "gemini-x"',
                'base_url = "https://gw.example.com"',
                "[emotes]",
                'wave = { address = "/avatar/parameters/Wave", value = 1, hold_ms = 500, reset_value = 0 }',
            ]
        ),
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.brain.gemini.model == "gemini-x"
    assert cfg.brain.gemini.base_url == "https://gw.example.com"
    assert cfg.emotes["wave"].address == "/avatar/parameters/Wave"
    assert cfg.emotes["wave"].hold_ms == 500
    assert cfg.emotes["wave"].reset_value == 0.0


def test_missing_explicit_path(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load(tmp_path / "nope.toml")


def test_bad_provider(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[stt]\nprovider = "whisperx"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="provider"):
        load(p)
