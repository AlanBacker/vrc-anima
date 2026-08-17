"""edge-tts:免费、免 key、多语种,M1 独立运行的默认发声。"""

from __future__ import annotations

import logging
from typing import AsyncIterator

log = logging.getLogger(__name__)


class EdgeTts:
    def __init__(self, voice: str = "zh-CN-XiaoyiNeural", rate: str = ""):
        self._voice = voice
        self._rate = rate

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        import edge_tts  # 懒加载

        kwargs = {}
        if self._rate:
            kwargs["rate"] = self._rate
        communicate = edge_tts.Communicate(text, self._voice, **kwargs)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
