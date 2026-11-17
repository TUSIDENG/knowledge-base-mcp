"""MCP stdio 服务：P0 只读工具。"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import tools
from .registry import load_registry

INSTRUCTIONS = (
    "本服务提供共享知识库的检索能力。step1 先调用 kb_list 确认 kb_id 与可检索分区；"
    "step2 用 kb_search 带 kb_id + prefixes 做窄范围检索；step3 用 kb_read 按 path 或 doc_id 取原文。"
    "结果中的 git_ref 是溯源依据，回答时请引用 path 与 heading。"
)

mcp = MCPServer("kb-gateway", version="0.1.0", instructions=INSTRUCTIONS)


def _run(handler) -> dict[str, Any]:
    try:
        return handler(load_registry())
    except (KeyError, ValueError, FileNotFoundError, RuntimeError) as exc:
        return {"error": str(exc).strip("'\"")}


@mcp.tool()
def kb_init_guide(
    source_type: str = "embedded",
    repo: str | None = None,
    knowledge_root: str = "knowledge",
    check: bool = False,
) -> dict[str, Any]:
    """返回创建知识库的完整指引与模板（不写文件）；check=true 时只读校验指定仓库。"""
    return _run(
        lambda ctx: tools.kb_init_guide(ctx, source_type, repo, knowledge_root, check)
    )


@mcp.tool()
def kb_list(include_prefixes: bool = False) -> dict[str, Any]:
    """列出已注册知识库、向量开关、索引状态与文档/分块数量。"""
    return _run(lambda ctx: tools.kb_list(ctx, include_prefixes))


@mcp.tool()
def kb_search(
    kb_id: str,
    query: str,
    prefixes: list[str] | None = None,
    filters: dict | None = None,
    top_k: int = 8,
    mode: str = "vector",
) -> dict[str, Any]:
    """在单个知识库内检索；prefixes 限定目录，filters 支持 doc_type/status/domain/systems/authority。"""
    return _run(
        lambda ctx: tools.kb_search(ctx, kb_id, query, prefixes, filters, top_k, mode)
    )


@mcp.tool()
def kb_read(
    kb_id: str | None = None,
    path: str | None = None,
    doc_id: str | None = None,
    with_frontmatter: bool = True,
) -> dict[str, Any]:
    """按 path 或 doc_id 读取文档原文与 front matter。"""
    return _run(lambda ctx: tools.kb_read(ctx, kb_id, path, doc_id, with_frontmatter))


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8787) -> None:
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=host, port=port)
