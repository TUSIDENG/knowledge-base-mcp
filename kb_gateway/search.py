"""向量检索：过滤、加权与结果整形。"""

from __future__ import annotations

import re

from .embedder import Embedder, build_embedder
from .registry import KnowledgeBase, RegistryContext
from .store import StateStore, VectorStore

_ALLOWED = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff./\-]*$")
_CACHE: dict[str, Embedder] = {}

AUTHORITY_WEIGHT = {"contract": 1.15, "frozen": 1.10, "note": 1.0, "draft": 0.9}


def embedder_for(ctx: RegistryContext) -> tuple[Embedder, int]:
    profile = ctx.registry.embedding.active()
    key = f"{profile.profile_id}:{profile.type}:{profile.model}:{profile.dim}"
    if key not in _CACHE:
        _CACHE[key] = build_embedder(profile, ctx.registry.storage.model_cache_path)
    return _CACHE[key], profile.dim


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalize_prefixes(prefixes: list[str], knowledge_root: str) -> list[str]:
    root = knowledge_root.strip("./")
    cleaned: list[str] = []
    for prefix in prefixes:
        value = prefix.strip().strip("/")
        if value.startswith("./"):
            value = value[2:]
        if root and value == root:
            value = ""
        elif root and value.startswith(root + "/"):
            value = value[len(root) + 1:]
        if value in {"", "."}:
            continue
        if not _ALLOWED.match(value):
            raise ValueError(f"非法前缀：{prefix}")
        cleaned.append(value)
    return cleaned


def build_where(filters: dict | None, prefixes: list[str]) -> str | None:
    clauses: list[str] = []
    filters = filters or {}

    statuses = filters.get("status")
    if statuses:
        values = ", ".join(_quote(str(item)) for item in _as_list(statuses))
        clauses.append(f"status IN ({values})")
    else:
        clauses.append("status != 'deprecated'")

    for key in ("doc_type", "authority", "effective_from"):
        value = filters.get(key)
        if value:
            clauses.append(f"{key} = {_quote(str(value))}")

    for key in ("domain", "systems"):
        value = filters.get(key)
        if value:
            if not _ALLOWED.match(str(value)):
                raise ValueError(f"非法过滤值：{value}")
            clauses.append(f"{key} LIKE {_quote(f'%{value}%')}")

    if prefixes:
        likes = [f"rel_path LIKE {_quote(prefix + '/%')}" for prefix in prefixes]
        equals = [f"rel_path = {_quote(prefix)}" for prefix in prefixes]
        clauses.append("(" + " OR ".join([*equals, *likes]) + ")")

    return " AND ".join(clauses) if clauses else None


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _snippet(content: str, limit: int = 200) -> str:
    flat = " ".join(content.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def search(
    ctx: RegistryContext,
    kb_id: str,
    query: str,
    prefixes: list[str] | None = None,
    filters: dict | None = None,
    top_k: int = 8,
    mode: str = "vector",
) -> dict:
    kb = ctx.kb(kb_id)
    if not query.strip():
        raise ValueError("query 不能为空")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k 必须在 1~50 之间")
    if not kb.vector_enabled:
        raise ValueError(
            f"知识库 {kb_id} 已关闭向量化，P0 仅提供向量检索；请开启向量化或等待 BM25 实现"
        )

    prefixes = _normalize_prefixes(prefixes or [], kb.knowledge_root)
    state = StateStore(ctx.registry.storage.sqlite_path)
    try:
        if state.kb_state(kb_id) is None:
            raise ValueError(f"知识库 {kb_id} 尚未索引，请先执行 kb-gateway reindex")
        known = state.partitions(kb_id)
    finally:
        state.close()
    for prefix in prefixes:
        top = prefix.split("/", 1)[0]
        if known and top not in known:
            raise ValueError(f"前缀 {prefix} 不在已索引分区中（可用：{', '.join(known)}）")

    embedder, dim = embedder_for(ctx)
    vector = embedder.embed([query])[0]
    store = VectorStore(ctx.registry.storage.lancedb_path, kb_id, dim)
    where = build_where(filters, prefixes)
    rows = store.search(vector, where, top_k * 2)

    results = []
    for row in rows:
        distance = float(row.get("_distance", 1.0))
        # cosine 距离 ∈ [0, 2]，映射为 0~1 的相似度
        similarity = max(0.0, 1.0 - distance / 2.0)
        weight = AUTHORITY_WEIGHT.get(row.get("authority", "note"), 1.0)
        results.append(
            {
                "kb_id": row["kb_id"],
                "doc_id": row["doc_id"],
                "path": row["path"],
                "partition": row["partition"],
                "heading": row["heading_path"],
                "content": row["content"],
                "snippet": _snippet(row["content"]),
                "score": round(min(1.0, similarity * weight), 4),
                "authority": row["authority"],
                "status": row["status"],
                "git_ref": row["git_ref"],
                "chunk_index": row["chunk_index"],
                "matched_by": ["vector"],
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    degraded = None
    if mode in {"hybrid", "bm25"}:
        degraded = "bm25_pending_p1"
    return {
        "results": results[:top_k],
        "effective_mode": "vector",
        "degraded_reason": degraded,
    }
