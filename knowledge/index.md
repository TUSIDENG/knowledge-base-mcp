---
domain: [kb-gateway]
doc_type: guide
status: frozen
owner: david
version: 1.3
---

# knowledge-base-mcp · 知识库入口

这是本项目的内嵌知识库（`kb_id: knowledge-base-mcp`），随项目 Git 仓库一起版本管理。

## 这个库放什么

| 分区 | 内容 | 权威级 |
|---|---|---|
| `spec/` | 已经定稿、不再频繁变动的设计文档 | contract（可作为实现依据） |
| `guides/` | 操作类指南（接入配置、使用手册） | note（随工具版本更新） |
| `decisions/` | ADR、选型取舍、踩坑结论 | note（过程性，Agent 可提案写入） |
| `_draft/` | 未定稿草稿 | note（默认降权，不进正式检索） |
| `attachments/` | 架构图、流程图等图片资源 | — |

## 当前文档

| 文档 | 说明 | 状态 |
|---|---|---|
| [spec/01-多Agent共享知识库架构.md](spec/01-多Agent共享知识库架构.md) | 整体架构：MCP 初始化指导、持久化索引队列、Web 管理、向量开关、线上/本地模型、uv 可选依赖、现成 Agent 调度 + 薄层编排、黑板与提案回流 | frozen v1.3 |
| [spec/02-Branch与Worktree与内嵌知识库.md](spec/02-Branch与Worktree与内嵌知识库.md) | Git 对象索引、ref 路由、watcher/hook/轮询对账、未提交 dirty 语义、异步入队、薄层 worktree 分配与重启恢复 | frozen v1.2 |
| [guides/agent-mcp-配置.md](guides/agent-mcp-配置.md) | kb-gateway 以 HTTP MCP 常驻，Cursor / Trae / Codex / OpenCode / Claude Code 的 URL 接入、单 Agent 直连与调度 Agent + 子代理用法、排错清单 | active v1.1 |

## 约定

1. **不要直接改 `spec/` 里的冻结文档**，改走提案（`kb_write_candidate`）或人工评审后升版本。
2. 每篇文档必须有 front matter（`doc_type` / `status` / `version`），它是检索过滤器的数据源。
3. 文件名用 `类型-主题.md`，前缀可选 `rfc-` / `adr-` / `api-` / `flow-`，提升关键词命中。
4. 跨系统契约（会被多个仓库消费的映射表、接口定义）不放在这里，放独立的 `standards` 仓库。
</content>
