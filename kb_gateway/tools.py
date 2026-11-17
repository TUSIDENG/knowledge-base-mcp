"""MCP 工具实现：kb_init_guide / kb_list / kb_search / kb_read。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .indexer import read_source
from .parser import parse_markdown
from .registry import KnowledgeBase, RegistryContext
from .search import search as vector_search
from .store import StateStore

_TEMPLATES = {
    "front_matter": """---
title: 订单同步接口契约
doc_type: contract           # contract | spec | adr | api | guide | runbook | report
status: active               # draft | active | frozen | deprecated
domain: [oms, pricing]
systems: [order-service, pricing-service]
owner: david
authority: contract          # contract | frozen | note | draft
effective_from: 2026-11-01
version: 1.0
---""",
    "index_md": """---
title: 知识库索引
owner: david
doc_type: index
status: active
---

# 知识库索引

- [接口契约](api/order-sync.md) — 订单同步字段与幂等约束
- [架构决策](adr/0001-event-driven.md) — 为什么改为事件驱动""",
    "registry": """version: 1
embedding:
  active_profile: openai-default
  profiles:
    - profile_id: openai-default
      type: online
      provider: openai-compatible
      endpoint: https://api.example.com/v1
      model: text-embedding-3-large
      secret_ref: env:KB_EMBEDDING_KEY
      dim: 3072
      batch_size: 32
storage:
  lancedb_path: ~/.kb-gateway/data/lance
  sqlite_path: ~/.kb-gateway/data/meta.db
knowledge_bases:
  - kb_id: order-platform
    source_type: external
    repo: D:/code/kb-order-platform
    knowledge_root: ./
    index_mode: filesystem
    index_ref: main
    serve_refs: [main]
    default_authority: contract
    default_write: proposal_only
    vector_enabled: true
    include: ["**/*.md", "**/*.yaml", "**/*.sql"]
    exclude: ["**/.obsidian/**", "**/.trash/**"]
    prefix_rules:
      - glob: api/**
        authority: contract
        write: proposal_only""",
    "claude_mcp": """claude mcp add kb-gateway \\
  --env KB_CONFIG=$HOME/.kb-gateway/kb_registry.yaml \\
  -- kb-gateway serve --transport stdio""",
    "opencode_mcp": """{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "kb-gateway": {
      "type": "local",
      "command": ["kb-gateway", "serve", "--transport", "stdio"],
      "environment": {
        "KB_CONFIG": "~/.kb-gateway/kb_registry.yaml"
      }
    }
  }
}""",
}

_GUIDE = """# 初始化共享知识库

## 1. 先选形态

| 形态 | source_type | 适用 | 索引方式 |
|---|---|---|---|
| 内嵌 | `embedded` | 知识库跟着业务仓库走，随 MR 评审 | `git-object`，只索引已提交内容 |
| 外部 | `external` | 独立知识库仓库，多个项目共享 | `filesystem` 或 `git-object` |

内嵌库放在业务仓库的 `knowledge/` 下，根目录必须有 `index.md`；未提交的改动**不会**进入检索结果。

## 2. 目录与文档

```
<repo>/knowledge/
├── index.md            # 必需：入口索引
├── api/                # 接口契约（prefix 即分区）
├── adr/                # 架构决策
└── spec/               # 规格说明
```

每个文档都要有 front matter：

{front_matter}

`index.md` 模板：

{index_md}

## 3. 注册知识库

写入 `~/.kb-gateway/kb_registry.yaml`（或用 `KB_CONFIG` 指向别处）：

{registry}

## 4. 首次索引

```bash
uv sync
uv run kb-gateway reindex --kb <kb_id> --full
uv run kb-gateway list
```

索引规则：文件内容哈希未变则跳过；文档删除会同步清理向量；向量维度变化必须 `--full`。
向量化默认使用 `type: builtin` 的离线哈希占位模型，仅用于冒烟；生产请配置 `online` 或 `local`。

## 5. 接入 Agent（MCP）

Claude Code：

```bash
{claude_mcp}
```

OpenCode（`opencode.json`）：

```json
{opencode_mcp}
```

## 6. 完成检查

- `kb_list` 能看到该库，`doc_count > 0`
- `kb_search` 能返回带 `path` 与 `git_ref` 的结果
- `kb_read` 能按 `path` 或 `doc_id` 取回原文
- 检索结果里的 `git_ref` 与预期提交一致（内嵌库应为 `main@<sha>`）
"""


def _check_repo(repo: Path, knowledge_root: str, source_type: str) -> list[dict]:
    checks: list[dict] = []

    def add(level: str, code: str, message: str) -> None:
        checks.append({"level": level, "code": code, "message": message})

    if not repo.exists():
        add("error", "repo_missing", f"仓库路径不存在：{repo}")
        return checks
    add("ok", "repo_exists", f"仓库存在：{repo}")

    if not (repo / ".git").exists():
        level = "error" if source_type == "embedded" else "warn"
        add(level, "not_git", "不是 git 仓库；内嵌库必须由 git 提交驱动索引")
    else:
        add("ok", "git_ok", "git 仓库校验通过")

    root = (repo / knowledge_root).resolve()
    if not root.exists():
        add("error", "knowledge_missing", f"知识目录不存在：{root}")
        return checks
    add("ok", "knowledge_exists", f"知识目录存在：{root}")

    docs = [file for file in root.rglob("*.md") if file.is_file()]
    if not docs:
        add("error", "no_docs", "知识目录下没有任何 Markdown 文档")
    else:
        add("ok", "docs_found", f"发现 {len(docs)} 个 Markdown 文档")

    if not (root / "index.md").exists():
        add("warn", "index_missing", "缺少 index.md，Agent 首次进入时没有导航入口")

    missing = 0
    for file in docs:
        if not file.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff").startswith("---"):
            missing += 1
    if missing:
        add("warn", "front_matter_missing", f"{missing}/{len(docs)} 个文档缺少 front matter")
    else:
        add("ok", "front_matter_ok", "所有文档均包含 front matter")
    return checks


def build_guide(source_type: str, knowledge_root: str, checks: list[dict] | None) -> str:
    guide = _GUIDE
    for key, value in _TEMPLATES.items():
        guide = guide.replace("{" + key + "}", value)
    guide += f"\n形态：`{source_type}`，知识目录：`{knowledge_root}`\n"
    if checks is not None:
        guide += "\n## 仓库检查结果\n\n"
        for check in checks:
            guide += f"- [{check['level']}] {check['message']}\n"
    return guide


def kb_init_guide(
    ctx: RegistryContext,
    source_type: str = "embedded",
    repo: str | None = None,
    knowledge_root: str = "knowledge",
    check: bool = False,
) -> dict:
    if source_type not in {"embedded", "external"}:
        raise ValueError("source_type 只能是 embedded 或 external")
    checks = None
    if check and repo:
        checks = _check_repo(Path(repo).expanduser().resolve(), knowledge_root, source_type)
    return {
        "guide": build_guide(source_type, knowledge_root, checks),
        "templates": {
            "front_matter": _TEMPLATES["front_matter"],
            "index_md": _TEMPLATES["index_md"],
            "registry": _TEMPLATES["registry"],
            "claude_mcp": _TEMPLATES["claude_mcp"],
            "opencode_mcp": _TEMPLATES["opencode_mcp"],
        },
        "checks": checks,
        "next_steps": [
            "按模板创建 knowledge/index.md 与文档 front matter",
            "在 kb_registry.yaml 注册知识库",
            "uv run kb-gateway reindex --kb <kb_id> --full",
            "在 MCP 客户端里添加 kb-gateway serve --transport stdio",
        ],
    }


def _maybe_state(ctx: RegistryContext) -> StateStore | None:
    path = Path(ctx.registry.storage.sqlite_path)
    if not ctx.registry.storage.sqlite_path or not path.exists():
        return None
    return StateStore(str(path))


def kb_list(ctx: RegistryContext, include_prefixes: bool = False) -> dict:
    state = _maybe_state(ctx)
    items: list[dict] = []
    try:
        for kb in ctx.registry.knowledge_bases:
            row = state.kb_state(kb.kb_id) if state else None
            item = {
                "kb_id": kb.kb_id,
                "display_name": kb.display_name or kb.kb_id,
                "source_type": kb.source_type,
                "repo": str(ctx.repo_path(kb)),
                "knowledge_root": kb.knowledge_root,
                "index_mode": kb.index_mode,
                "index_ref": kb.index_ref,
                "serve_refs": kb.serve_refs,
                "vector_enabled": kb.vector_enabled,
                "effective_mode": "vector" if kb.vector_enabled else "vector_disabled",
                "default_authority": kb.default_authority,
                "default_write": kb.default_write,
                "doc_count": row["doc_count"] if row else 0,
                "chunk_count": row["chunk_count"] if row else 0,
                "vector_dim": row["vector_dim"] if row else None,
                "last_indexed": row["last_indexed"] if row else None,
                "indexed_rev": row["indexed_rev"] if row else None,
                "status": "ready" if row else "not_indexed",
            }
            if include_prefixes:
                item["prefixes"] = state.partitions(kb.kb_id) if state else []
            if not kb.vector_enabled:
                item["degraded_reason"] = "vector_disabled"
            items.append(item)
    finally:
        if state:
            state.close()
    return {
        "knowledge_bases": items,
        "active_embedding_profile": ctx.registry.embedding.active_profile
        or (ctx.registry.embedding.profiles[0].profile_id if ctx.registry.embedding.profiles else None),
        "embedding_ready": bool(ctx.registry.embedding.profiles),
    }


def _resolve_kb(ctx: RegistryContext, kb_id: str | None, path: str | None) -> tuple[KnowledgeBase, str]:
    if path and Path(path).is_absolute():
        resolved = Path(path).expanduser().resolve()
        for kb in ctx.registry.knowledge_bases:
            repo = ctx.repo_path(kb)
            try:
                rel = resolved.relative_to(repo).as_posix()
            except ValueError:
                continue
            return kb, rel
        raise ValueError(f"路径不在任何已注册仓库内：{path}")
    if not kb_id:
        raise ValueError("相对路径或 doc_id 读取需要 kb_id")
    kb = ctx.kb(kb_id)
    if not path:
        raise ValueError("需要 path 或 doc_id")
    rel = PurePosixPath(path.strip("/")).as_posix()
    if kb.knowledge_root.strip("./") and not rel.startswith(kb.knowledge_root.strip("./") + "/"):
        rel = f"{kb.knowledge_root.strip('./')}/{rel}"
    return kb, rel


def kb_read(
    ctx: RegistryContext,
    kb_id: str | None = None,
    path: str | None = None,
    doc_id: str | None = None,
    with_frontmatter: bool = True,
) -> dict:
    state = _maybe_state(ctx)
    try:
        if doc_id:
            row = state.find_doc(doc_id) if state else None
            if not row:
                raise ValueError(f"未找到已索引文档：{doc_id}")
            kb = ctx.kb(row["kb_id"])
            rel = row["path"]
            indexed = row
        else:
            kb, rel = _resolve_kb(ctx, kb_id, path)
            indexed = state.get_doc(kb.kb_id, rel) if state else None
            if not indexed and path and Path(path).is_absolute():
                raise ValueError(f"路径未索引，先执行 reindex：{rel}")

        raw, git_ref = read_source(ctx, kb, rel)
        parsed = parse_markdown(raw) if rel.lower().endswith((".md", ".markdown")) else None
        content = raw if with_frontmatter or parsed is None else parsed.body
        return {
            "kb_id": kb.kb_id,
            "path": rel,
            "doc_id": indexed["doc_id"] if indexed else None,
            "git_ref": indexed["git_ref"] if indexed else git_ref,
            "indexed": bool(indexed),
            "front_matter": parsed.front_matter if parsed else {},
            "content": content,
        }
    finally:
        if state:
            state.close()


def kb_search(
    ctx: RegistryContext,
    kb_id: str,
    query: str,
    prefixes: list[str] | None = None,
    filters: dict | None = None,
    top_k: int = 8,
    mode: str = "vector",
) -> dict:
    return vector_search(ctx, kb_id, query, prefixes, filters, top_k, mode)
