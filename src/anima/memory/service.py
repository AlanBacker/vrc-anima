"""记忆服务:把 memstore 包装成 Anima 的三个记忆工具 + 索引注入。

作用域选择(DESIGN.md Q15):用共享的 session 作用域,session_key 默认
"vrchat:main"。M3 与 AstrBot 桥接后,插件侧用同一个 key 就能读写同一
份记忆——VRChat 里认识的人,QQ 上也记得。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..brain.base import ToolCall
from .memstore import MemoryStore, ScopeStore

log = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, data_dir: Path, session_key: str = "vrchat:main"):
        self._store = MemoryStore(data_dir / "memory")
        self._scope: ScopeStore = self._store.session_scope(session_key)
        self.session_key = session_key

    @property
    def scope_dir(self) -> Path:
        return self._scope.root

    async def index_text(self) -> str:
        """注入模型上下文的索引(memstore 负责截断与"还有 N 行"提示)。"""
        snapshot = await self._scope.load_index()
        return snapshot.text

    # ------------------------------------------------------------ 工具分发

    async def run_tool(self, call: ToolCall) -> dict:
        try:
            if call.name == "memory_read":
                return await self._read(str(call.args.get("path", "")))
            if call.name == "memory_search":
                return await self._search(str(call.args.get("query", "")))
            if call.name == "memory_write":
                return await self._write(
                    str(call.args.get("path", "")),
                    str(call.args.get("content", "")),
                )
        except Exception as e:
            log.warning("记忆工具 %s 异常:%s", call.name, e)
            return {"status": "error", "detail": f"记忆操作失败:{e}"}
        return {"status": "error", "detail": f"未知记忆工具:{call.name}"}

    async def _read(self, path: str) -> dict:
        if not path:
            return {"status": "error", "detail": "缺少 path 参数"}
        content = await self._scope.read(path)
        if content is None:
            return {
                "status": "error",
                "detail": f"没有这个记忆文件:{path}({self._scope.path_rules()})",
            }
        return {"status": "ok", "content": content}

    async def _search(self, query: str) -> dict:
        if not query.strip():
            return {"status": "error", "detail": "缺少 query 参数"}
        results = await self._scope.search(query)
        if not results:
            return {"status": "ok", "detail": "没有找到匹配的记忆"}
        return {"status": "ok", "results": results}

    async def _write(self, path: str, content: str) -> dict:
        if not path or not content.strip():
            return {"status": "error", "detail": "path 和 content 都不能为空"}
        report = await self._scope.write(path, content)
        return {
            "status": "ok" if report.ok else "error",
            "detail": report.message,
        }
