---
domain: [kb-gateway]
doc_type: guide
status: frozen
owner: david
version: 1.0
---

# knowledge-base-mcp · 知识库入口

这是本项目的内嵌知识库（`kb_id: knowledge-base-mcp`），随项目 Git 仓库一起版本管理。

## 这个库放什么

| 分区 | 内容 | 权威级 |
|---|---|---|
| `spec/` | 已经定稿、不再频繁变动的设计文档 | contract（可作为实现依据） |
| `decisions/` | ADR、选型取舍、踩坑结论 | note（过程性，Agent 可提案写入） |
| `_draft/` | 未定稿草稿 | note（默认降权，不进正式检索） |
| `attachments/` | 架构图、流程图等图片资源 | — |

## 当前文档

| 文档 | 说明 | 状态 |
|---|---|---|
| [spec/01-多Agent共享知识库架构.md](spec/01-多Agent共享知识库架构.md) | 整体架构：MCP 知识库网关、仓库=知识库、分区、检索链路、黑板、调度、提案回流 | frozen v1.1 |
| [spec/02-Branch与Worktree与内嵌知识库.md](spec/02-Branch与Worktree与内嵌知识库.md) | 分支与工作树如何与内嵌知识库配合：索引建在 Git 对象库上、ref 路由、post-merge 回流 | frozen v1.0 |

## 约定

1. **不要直接改 `spec/` 里的冻结文档**，改走提案（`kb_write_candidate`）或人工评审后升版本。
2. 每篇文档必须有 front matter（`doc_type` / `status` / `version`），它是检索过滤器的数据源。
3. 文件名用 `类型-主题.md`，前缀可选 `rfc-` / `adr-` / `api-` / `flow-`，提升关键词命中。
4. 跨系统契约（会被多个仓库消费的映射表、接口定义）不放在这里，放独立的 `standards` 仓库。
</content>
