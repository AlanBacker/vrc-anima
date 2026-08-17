"""Gemini 级联大脑:google-genai SDK 适配。

- 经 New API 网关时走原生 /v1beta 直通:HttpOptions(base_url=网关地址)
- 中立历史结构 → types.Content 的翻译在 to_genai_contents(),纯函数可测
- 工具声明用 Gemini 原生 Schema 风格(type 大写),见 action/tools.py
"""

from __future__ import annotations

import logging

from ..config import BrainConfig, ConfigError
from .base import BrainReply, TokenUsage, ToolCall

log = logging.getLogger(__name__)


def to_genai_contents(contents: list[dict], types) -> list:
    """中立结构 → google.genai.types.Content 列表。"""
    out = []
    for content in contents:
        parts = []
        for part in content["parts"]:
            if "text" in part:
                if part["text"]:
                    parts.append(types.Part(text=part["text"]))
            elif "jpeg" in part:
                parts.append(
                    types.Part.from_bytes(data=part["jpeg"], mime_type="image/jpeg")
                )
            elif "wav" in part:
                parts.append(
                    types.Part.from_bytes(data=part["wav"], mime_type="audio/wav")
                )
            elif "call" in part:
                call = part["call"]
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=call["name"],
                            args=call["args"],
                            id=call["id"] or None,
                        )
                    )
                )
            elif "resp" in part:
                resp = part["resp"]
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=resp["name"],
                            response=resp["result"],
                            id=resp["id"] or None,
                        )
                    )
                )
            else:
                raise ValueError(f"未知 part:{part.keys()}")
        if parts:
            out.append(types.Content(role=content["role"], parts=parts))
    return out


class GeminiBrain:
    def __init__(
        self,
        cfg: BrainConfig,
        system_prompt: str,
        tool_decls: list[dict],
    ):
        self._cfg = cfg
        self._system = system_prompt
        self._tool_decls = tool_decls
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise ConfigError("需要 google-genai:pip install google-genai") from e
        g = self._cfg.gemini
        api_key = g.resolved_api_key()
        if not api_key:
            raise ConfigError(
                "缺少 Gemini API Key:设置环境变量 GEMINI_API_KEY,"
                "或在 config.toml [brain.gemini] 里填 api_key"
            )
        http_options = None
        if g.base_url:
            http_options = types.HttpOptions(base_url=g.base_url)
        self._client = genai.Client(api_key=api_key, http_options=http_options)
        self._types = types
        log.info(
            "Gemini 客户端就绪:model=%s base_url=%s",
            g.model,
            g.base_url or "官方",
        )
        return self._client

    async def reply(self, contents: list[dict]) -> BrainReply:
        """一次级联回合:历史进,文本(=语音)+工具调用出。"""
        client = self._ensure_client()
        types = self._types
        g = self._cfg.gemini

        config_kwargs: dict = {
            "system_instruction": self._system,
            "max_output_tokens": self._cfg.max_output_tokens,
        }
        if self._tool_decls:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(**decl) for decl in self._tool_decls
                    ]
                )
            ]
        if g.temperature >= 0:
            config_kwargs["temperature"] = g.temperature

        response = await client.aio.models.generate_content(
            model=g.model,
            contents=to_genai_contents(contents, types),
            config=types.GenerateContentConfig(**config_kwargs),
        )

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        if response.candidates:
            content = response.candidates[0].content
            for part in content.parts or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    calls.append(
                        ToolCall(
                            name=fc.name,
                            args=dict(fc.args or {}),
                            call_id=fc.id or "",
                        )
                    )

        usage = TokenUsage()
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage.prompt_tokens = meta.prompt_token_count or 0
            usage.output_tokens = (meta.candidates_token_count or 0) + (
                getattr(meta, "thoughts_token_count", 0) or 0
            )

        return BrainReply(
            text="".join(text_parts).strip(), tool_calls=calls, usage=usage
        )
