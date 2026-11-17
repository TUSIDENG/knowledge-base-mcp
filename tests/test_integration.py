import yaml
import pytest

from kb_gateway import tools
from kb_gateway.indexer import index_kb
from kb_gateway.registry import load_registry

DOC = """---
title: 订单同步契约
doc_type: contract
status: active
domain: [oms]
systems: [order-service]
authority: contract
---

# 订单同步契约

订单同步接口要求幂等，重复请求必须返回同一结果。

## 字段

| 字段 | 说明 |
| --- | --- |
| order_id | 订单号 |
"""

DEPRECATED = """---
title: 旧版订单同步
status: deprecated
---

# 旧版订单同步

旧版订单同步已下线。
"""


@pytest.fixture
def context(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    api = repo / "knowledge" / "api"
    api.mkdir(parents=True)
    (repo / "knowledge" / "index.md").write_text("# 索引\n\n- 订单同步\n", encoding="utf-8")
    (api / "order-sync.md").write_text(DOC, encoding="utf-8")
    (api / "legacy.md").write_text(DEPRECATED, encoding="utf-8")

    config = tmp_path / "cfg" / "kb_registry.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "embedding": {
                    "active_profile": "hash",
                    "profiles": [{"profile_id": "hash", "type": "builtin", "dim": 64}],
                },
                "storage": {
                    "lancedb_path": "../data/lance",
                    "sqlite_path": "../data/meta.db",
                },
                "knowledge_bases": [
                    {
                        "kb_id": "demo",
                        "source_type": "external",
                        "repo": "../repo",
                        "knowledge_root": "knowledge",
                        "index_mode": "filesystem",
                        "vector_enabled": True,
                        "default_authority": "note",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KB_CONFIG", str(config))
    return load_registry(), repo, api


def test_index_list_and_search(context) -> None:
    ctx, _repo, _api = context
    report = index_kb(ctx, ctx.kb("demo"), full=True)
    assert report.added == 3 and report.chunks > 0

    listing = tools.kb_list(ctx, include_prefixes=True)
    item = listing["knowledge_bases"][0]
    assert item["doc_count"] == 3
    assert item["status"] == "ready"
    assert item["prefixes"] == ["api"]

    result = tools.kb_search(ctx, "demo", "订单同步 幂等", prefixes=["api"], top_k=3)
    assert result["effective_mode"] == "vector"
    assert result["results"], "应检索到订单同步文档"
    top = result["results"][0]
    assert top["path"] == "knowledge/api/order-sync.md"
    assert top["authority"] == "contract"
    assert 0.0 <= top["score"] <= 1.0
    assert all(item["status"] != "deprecated" for item in result["results"])


def test_read_by_path_and_doc_id(context) -> None:
    ctx, _repo, _api = context
    index_kb(ctx, ctx.kb("demo"), full=True)

    doc = tools.kb_read(ctx, kb_id="demo", path="api/order-sync.md", with_frontmatter=False)
    assert doc["indexed"] is True
    assert doc["front_matter"]["doc_type"] == "contract"
    assert "# 订单同步契约" in doc["content"]
    assert not doc["content"].lstrip().startswith("---")

    by_id = tools.kb_read(ctx, doc_id=doc["doc_id"], with_frontmatter=True)
    assert by_id["path"] == doc["path"]
    assert by_id["content"].startswith("---")


def test_incremental_and_delete(context) -> None:
    ctx, _repo, api = context
    index_kb(ctx, ctx.kb("demo"), full=True)

    (api / "legacy.md").unlink()
    (api / "order-sync.md").write_text(DOC + "\n补充：失败重试三次。\n", encoding="utf-8")
    report = index_kb(ctx, ctx.kb("demo"))
    assert report.updated == 1
    assert report.deleted == 1
    assert tools.kb_list(ctx)["knowledge_bases"][0]["doc_count"] == 2


def test_prefix_outside_partitions_rejected(context) -> None:
    ctx, _repo, _api = context
    index_kb(ctx, ctx.kb("demo"), full=True)
    with pytest.raises(ValueError):
        tools.kb_search(ctx, "demo", "订单", prefixes=["adr"])


def test_init_guide_check(context) -> None:
    ctx, repo, _api = context
    guide = tools.kb_init_guide(ctx, source_type="embedded", repo=str(repo), check=True)
    assert "front matter" in guide["guide"].lower()
    assert guide["checks"]
    levels = {check["code"]: check["level"] for check in guide["checks"]}
    assert levels["knowledge_exists"] == "ok"
    assert levels["not_git"] == "error"
