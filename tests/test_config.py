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


def test_input_gain_default_and_range(tmp_path):
    assert load(None).audio.input_gain == 1.0
    p = tmp_path / "c.toml"
    p.write_text("[audio]\ninput_gain = 2.5\n", encoding="utf-8")
    assert load(p).audio.input_gain == 2.5
    for bad in ("0", "-1.0", "17"):
        p.write_text(f"[audio]\ninput_gain = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="input_gain"):
            load(p)


def test_thinking_level_default_and_whitelist(tmp_path):
    assert load(None).brain.gemini.thinking_level == ""
    p = tmp_path / "c.toml"
    p.write_text('[brain.gemini]\nthinking_level = "low"\n', encoding="utf-8")
    assert load(p).brain.gemini.thinking_level == "low"
    p.write_text('[brain.gemini]\nthinking_level = "ultra"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="thinking_level"):
        load(p)


def test_compress_defaults_and_section(tmp_path):
    cfg = load(None)
    assert cfg.compress.enabled and cfg.compress.keep_recent_turns == 3
    assert cfg.compress.max_context_tokens == 0
    p = tmp_path / "c.toml"
    p.write_text(
        "[compress]\nkeep_recent_turns = 5\nmax_context_tokens = 1000000\n"
        "threshold = 0.8\nextract_memories = false\n",
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.compress.keep_recent_turns == 5
    assert cfg.compress.max_context_tokens == 1000000
    assert cfg.compress.threshold == 0.8
    assert not cfg.compress.extract_memories


def test_compress_validation(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[compress]\nkeep_recent_turns = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="keep_recent_turns"):
        load(p)
    # 保留轮数逼近窗口:压缩永远触发不了,直接拦下
    p.write_text(
        "[brain]\nmax_history_turns = 6\n[compress]\nkeep_recent_turns = 5\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="keep_recent_turns"):
        load(p)
    # 但压缩关掉时不管这条(纯滑动窗口模式)
    p.write_text(
        "[brain]\nmax_history_turns = 6\n"
        "[compress]\nenabled = false\nkeep_recent_turns = 5\n",
        encoding="utf-8",
    )
    assert load(p).compress.keep_recent_turns == 5
    for bad in ("0", "1", "1.5"):
        p.write_text(f"[compress]\nthreshold = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="threshold"):
            load(p)
    p.write_text("[compress]\nmax_context_tokens = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_context_tokens"):
        load(p)


def test_puppet_defaults_and_validation(tmp_path):
    cfg = load(None)
    assert cfg.puppet.motion_tool is True
    assert cfg.puppet.rate_hz == 20.0
    assert cfg.puppet.height_m == 1.60  # trackers 实验层预留
    p = tmp_path / "c.toml"
    p.write_text(
        "[puppet]\nmotion_tool = false\nheight_m = 1.72\nrate_hz = 30\n",
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.puppet.motion_tool is False
    assert cfg.puppet.height_m == 1.72
    assert cfg.puppet.rate_hz == 30.0
    p.write_text("[puppet]\nheight_m = 3.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="height_m"):
        load(p)
    p.write_text("[puppet]\nrate_hz = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="rate_hz"):
        load(p)


def test_audio_barge_in_flag(tmp_path):
    assert load(None).audio.barge_in  # 默认开
    p = tmp_path / "c.toml"
    p.write_text("[audio]\nbarge_in = false\n", encoding="utf-8")
    assert not load(p).audio.barge_in
