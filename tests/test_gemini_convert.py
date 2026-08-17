"""中立历史结构 → google.genai types 的翻译(需装 google-genai)。"""

import pytest

types = pytest.importorskip("google.genai.types")

from anima.brain.gemini import to_genai_contents  # noqa: E402


def test_roles_and_parts_translate():
    contents = [
        {"role": "user", "parts": [{"text": "[听到] 你好"}, {"jpeg": b"\xff\xd8x"}]},
        {
            "role": "model",
            "parts": [
                {"text": "我看看"},
                {"call": {"name": "snapshot", "args": {}, "id": "c1"}},
            ],
        },
        {
            "role": "tool",
            "parts": [
                {"resp": {"name": "snapshot", "result": {"status": "ok"}, "id": "c1"}}
            ],
        },
    ]
    out = to_genai_contents(contents, types)
    assert [c.role for c in out] == ["user", "model", "tool"]
    assert out[0].parts[0].text == "[听到] 你好"
    blob = out[0].parts[1].inline_data
    assert blob.mime_type == "image/jpeg" and blob.data == b"\xff\xd8x"
    fc = out[1].parts[1].function_call
    assert fc.name == "snapshot" and fc.id == "c1"
    fr = out[2].parts[0].function_response
    assert fr.name == "snapshot" and dict(fr.response) == {"status": "ok"}


def test_wav_part():
    out = to_genai_contents(
        [{"role": "user", "parts": [{"wav": b"RIFF"}]}], types
    )
    assert out[0].parts[0].inline_data.mime_type == "audio/wav"


def test_empty_text_content_dropped():
    out = to_genai_contents([{"role": "user", "parts": [{"text": ""}]}], types)
    assert out == []


def test_empty_call_id_becomes_none():
    out = to_genai_contents(
        [
            {
                "role": "model",
                "parts": [{"call": {"name": "jump", "args": {}, "id": ""}}],
            }
        ],
        types,
    )
    assert out[0].parts[0].function_call.id is None


def test_unknown_part_raises():
    with pytest.raises(ValueError):
        to_genai_contents([{"role": "user", "parts": [{"bogus": 1}]}], types)
