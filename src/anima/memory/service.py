"""记忆服务:把 memstore 包装成 Anima 的三个记忆工具 + 索引注入。

作用域选择:用共享的 session 作用域,session_key 默认 "vrchat:main"。
memstore 是纯文件协议——任何 memory_beyond 兼容程序指向同一数据目录、
同一 key,读写的就是同一份记忆(跨程序共享不需要任何桥)。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..brain.base import ToolCall
from .memstore import MEMORY_FILE_SUFFIX, MemoryStore, ScopeStore

log = logging.getLogger(__name__)

# 上游 memstore 的文件名是纯 ASCII(QQ 场景数字 ID 锚定用不到别的);
# VRChat 记人以头顶名牌为锚,名牌多是中日文,这里放宽到 \w(含 CJK)。
# 为保 memstore.py 与上游逐字节一致,放宽以子类实现,不改 vendor 文件。
_CJK_NAME_RE = re.compile(r"^[\w][\w.-]{0,120}$")


class CjkScopeStore(ScopeStore):
    def _resolve(self, name: str) -> Path | None:
        name = (name or "").strip().lstrip("/")
        if not name or not name.endswith(MEMORY_FILE_SUFFIX):
            return None
        if not _CJK_NAME_RE.match(name):
            return None
        path = (self.root / name).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return path

    @staticmethod
    def path_rules() -> str:
        return (
            "文件名可用中文、字母、数字、点、下划线、连字符,"
            f"必须以 {MEMORY_FILE_SUFFIX} 结尾,不允许目录分隔符"
        )


class CjkMemoryStore(MemoryStore):
    def _scope(self, path: Path) -> ScopeStore:
        path = path.resolve()
        store = self._scopes.get(path)
        if store is None:
            store = CjkScopeStore(path)
            self._scopes[path] = store
        return store


class MemoryService:
    def __init__(self, data_dir: Path, session_key: str = "vrchat:main"):
        self._store = CjkMemoryStore(data_dir / "memory")
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
                    bool(call.args.get("delete", False)),
                )
        except Exception as e:
            log.warning("记忆工具 %s 异常:%s", call.name, e)
            return {"status": "error", "detail": f"记忆操作失败:{e}"}
        return {"status": "error", "detail": f"未知记忆工具:{call.name}"}

    async def _read(self, path: str) -> dict:
        # 与上游 memory_beyond 同约定:path 省略读 MEMORY.md 索引全文
        # (注入的索引超长会被截断,这是模型看到完整目录的途径)
        path = path or "MEMORY.md"
        content = await self._scope.read(path)
        if content is None:
            existing = self._scope.list_files()
            listing = "、".join(existing) if existing else "(还没有任何记忆文件)"
            return {
                "status": "error",
                "detail": f"没有这个记忆文件:{path}。现有文件:{listing}",
            }
        return {"status": "ok", "content": content}

    async def _search(self, query: str) -> dict:
        if not query.strip():
            return {"status": "error", "detail": "缺少 query 参数"}
        results = await self._scope.search(query)
        if not results:
            return {"status": "ok", "detail": "没有找到匹配的记忆"}
        return {"status": "ok", "results": results}

    async def _write(self, path: str, content: str, delete: bool = False) -> dict:
        if not path:
            return {"status": "error", "detail": "缺少 path 参数"}
        if delete:
            report = await self._scope.delete(path)
        elif not content.strip():
            return {
                "status": "error",
                "detail": "content 为空。写入需提供完整内容;要删除请设 delete=true",
            }
        else:
            report = await self._scope.write(path, content)
        return {
            "status": "ok" if report.ok else "error",
            "detail": report.message,
        }
