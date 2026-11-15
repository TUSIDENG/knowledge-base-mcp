# knowledge-base-mcp

> 自建的多 Agent 共享知识库网关。用一套 MCP 协议，让 Cursor / Codex / OpenCode / Gemini 这些编码 Agent 共用同一份项目知识，并可单 Agent 直连开发、或由调度 Agent 编排多个 Worker 并行开发。

**当前状态：设计阶段（P0 未开工）**。仓库里目前只有两份冻结的设计文档，代码尚未落地。接口、配置、表结构都已在文档中定稿，可直接照做实现。

---

## 这是什么

一句话：**给 Agent 装一个外挂的、多项目共享的、可溯源的长期记忆。**

它解决三个现实问题：

1. **Agent 之间知识不共享** —— 每个 Agent 的上下文窗口是孤岛，上一个任务里踩过的坑，下一个任务重新踩。
2. **并行开发时契约不一致** —— 多个 Worker 同时开发，各自基于"记忆里的接口"写代码，联调必炸。
3. **知识无法沉淀** —— 开发产出散落在各个分支、各个窗口里，项目做完就丢了。

---

## 核心设计（三条规则）

1. **Agent 只认 `kb_id`，不认目录。**
   知识库物理上在哪（独立仓库，还是项目仓库里的 `knowledge/`），对 Agent 完全透明。

2. **仓库 = 知识库，目录 = 分区。**
   `kb_id` 绑定 Git 仓库边界，不绑定内容主题。库内分类用目录前缀（`prefixes`），不为分类拆出新库。
   实测结论：同一仓库里拆多个 kb_id 是纯负收益（索引遍历 ×N、ref 追踪 ×N、跨分区检索失效），收益为零。

3. **知识库只读，产出走提案。**
   Worker 不能直接改知识库正文，只能写黑板和提案队列。多 Agent 并发下，契约变更必须人工确认后归档。

---

## 关键概念

| 概念 | 定义 | 对应实现 |
|---|---|---|
| `kb_id` | 一个 Git 仓库 | `standards` / `knowledge-base-mcp` / 各项目仓库 |
| 分区（partition） | 仓库内的目录前缀 | `spec/`、`decisions/`、`cross-system/` |
| `authority` | 内容权威级 | `contract`（检索结果加权，提示词最高优先级）/ `note` |
| `write_policy` | 写入通道 | `read_only` / `proposal_only` / `true`（允许直写） |
| `ref` | 检索的 Git 版本 | `main`（默认基准）/ `feature/*` / `working:<task>`（未提交草稿） |
| 提案（proposal） | Worker 的知识写回请求 | 带 `base_version` 校验，防止并发覆盖 |
| 黑板（blackboard） | 异构 Agent 的共享状态 | SQLite 表，带版本号与写入租约 |

---

## 架构速览

```
Worker(codex/opencode/gemini) ──MCP──▶ KB Gateway ──▶ 存储
     ↑                                    ↑
 Orchestrator(可选)               KB Registry（仓库 + 分区规则）
```

- **KB Gateway**：单进程 MCP Server，6 个工具（`kb_list` / `kb_search` / `kb_read` / `kb_write_candidate` / `kb_stat` / `kb_reindex`）
- **检索链路**：授权裁剪 → front matter 过滤 → 向量召回 → BM25 召回 → RRF 融合 → 权威加权
- **索引**：走 Git 对象库（`git show <ref>:<path>`），不扫磁盘，因为 worktree 会让 `knowledge/` 变成 N 份副本
- **并发隔离**：每个 Worker 一个独立 `git worktree`
- **通信**：Worker 之间不直接对话，通过黑板读写同一 key 交换数据契约

---

## 仓库结构

```
knowledge-base-mcp/
├── README.md                     # 本文件
├── knowledge/                    # 本项目内嵌知识库（kb_id: knowledge-base-mcp）
│   ├── index.md                  # 库入口
│   ├── spec/                     # 冻结设计文档（contract 级）
│   │   ├── 01-多Agent共享知识库架构.md
│   │   └── 02-Branch与Worktree与内嵌知识库.md
│   ├── decisions/                # ADR（待积累）
│   └── _draft/                   # 草稿区
│
└── (待建) 代码目录
    ├── kb_gateway/               # MCP 网关：registry / parser / chunker / embedder / indexer / search / proposal
    ├── orchestrator/             # 调度层（可选启用）：planner / scheduler / dispatcher / blackboard / merger
    ├── workers/                  # WorkerAdapter + codex/opencode/gemini 三个适配器
    └── config/                   # kb_registry.yaml / agent_grants.yaml / orchestrator.yaml
```

> 注意这个仓库自身的示范性：**它既是网关的代码工程，同时又是一个 `source_type: embedded` 的知识库**。`knowledge/` 目录随代码一起 Git 版本管理，改了设计文档和改代码走同一套评审。

---

## 快速开始（P0，尚未实现）

```bash
# 1. 安装
pip install -e .

# 2. 注册本仓库自身为知识库
kb-gateway kb add --id knowledge-base-mcp --source-type embedded --repo . --knowledge-root knowledge

# 3. 首次索引（走 Git 对象库，不扫磁盘）
kb-gateway reindex --kb knowledge-base-mcp --ref main --full --progress

# 4. 启动服务（多 Agent 共享必须用 HTTP 单实例，避免多个进程重复索引）
kb-gateway serve --transport http --port 8765 --watch

# 5. 接入 Cursor / Codex / OpenCode / Gemini（配置见设计文档第 4 章）
kb-gateway doctor --agent cursor-main
```

最小可用配置：

```yaml
# config/kb_registry.yaml
knowledge_bases:
  - kb_id: standards                 # 唯一的跨项目标准库（独立仓库）
    source_type: external
    repo: D:/code/kb-standards
    default_authority: contract
    default_write: read_only

  - kb_id: knowledge-base-mcp        # 本项目（内嵌 knowledge/）
    source_type: embedded
    repo: D:/code/knowledge-base-mcp
    knowledge_root: knowledge
    index_mode: git-object
    index_ref: main
    default_authority: note
    default_write: proposal_only
    prefix_rules:
      - glob: "knowledge/spec/**"
        authority: contract         # 冻结设计 = 契约
      - glob: "knowledge/_draft/**"
        write: true                 # 草稿可直写
```

---

## 分阶段计划

| 阶段 | 范围 | 验收 |
|---|---|---|
| **P0** | registry 解析 + Markdown 解析分块 + LanceDB 索引 + `kb_search`/`kb_read`/`kb_list`（stdio） | 在 Cursor 里能检索到本项目 `knowledge/spec/` 的文档，结果带路径与 commit 溯源 |
| **P1** | BM25 + RRF + front matter 过滤 + 表格/代码块专项处理 | 搜单个字段名能命中映射表那一行 |
| **P2** | embedded 库的 git-object 索引 + post-merge 增量 + worktree ref 隔离 | 改了 `knowledge/` 合并后 30 秒内可检索 |
| **P3** | 提案队列 + base_version 校验 + 审计 | 两个 Worker 同时提案同一文件，后者标 stale 而非覆盖 |
| **P4** | Orchestrator：DAG 引擎 + worktree 分配 + 三个 Adapter + 黑板 + 冲突检测 | 三个异构 Worker 并行跑不同任务，产物可合并 |
| **P5** | 产出自动归档 + ADR 模板 + `kb_stat` + 图片 caption | 完整跑通"检索 → 开发 → 归档 → 再检索命中上次记录" |

---

## 设计文档索引

两份文档是本仓库的知识源，也是实现依据：

| 文档 | 内容 |
|---|---|
| [knowledge/spec/01-多Agent共享知识库架构.md](knowledge/spec/01-多Agent共享知识库架构.md) | 架构总览、四层映射（仓库/kb/分区/文档）、Registry 最小化配置、MCP 接口、检索链路、黑板表结构与并发控制、调度与 worktree、提案闭环、工程结构、部署、风险清单、与 TencentDB/Dify/Munder Difflin 的对比 |
| [knowledge/spec/02-Branch与Worktree与内嵌知识库.md](knowledge/spec/02-Branch与Worktree与内嵌知识库.md) | branch 与 worktree 的区别、为什么索引要建在 Git 对象库上、三种 ref 语义与授权矩阵、post-merge hook、四种典型场景走查、反模式清单 |

---

## 明确不做

- 不做知识库的富文本编辑器（写文档用你的编辑器 / IDE / Obsidian）
- 不做 LLM 编排引擎（Worker 用现成 CLI Agent，本仓库只做调度与知识层）
- 不做多租户、成员管理、计费（个人 / 小团队场景不需要）
- 不做跨机分布式向量集群（单机 + 可选只读副本足够）
</content>
