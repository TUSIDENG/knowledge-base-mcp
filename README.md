# knowledge-base-mcp

> 自建的多 Agent 共享知识库网关。用一套 MCP 协议，让 Cursor / Codex / OpenCode / Gemini 这些编码 Agent 共用同一份项目知识，并可单 Agent 直连开发、或由现成调度 Agent（OpenCode / Claude Code 原生 subagent）编排多个子代理并行开发。

**当前状态：P0 已实现**。包含 uv 工程、`kb_registry.yaml` 解析、Markdown 解析分块、LanceDB 索引，以及 `kb_init_guide` / `kb_list` / `kb_search` / `kb_read` 四个 MCP 工具与 CLI。P1 起的 BM25、watcher、持久化队列、Web 管理面和提案尚未实现，设计见 `knowledge/spec/`。

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
     ↑                              ↑        ↑
 调度 Agent(现成,可选)              │        └─ Watcher / Git Hook / 定时对账
                              Web 管理 UI + /api
```

- **KB Gateway**：单实例 MCP + Web 服务，7 个工具（`kb_init_guide` / `kb_list` / `kb_search` / `kb_read` / `kb_write_candidate` / `kb_stat` / `kb_reindex`）
- **初始化指导**：其他项目调用 `kb_init_guide` 即可获得目录、front matter、registry、首次索引与接入清单；工具本身不写文件
- **检索链路**：授权裁剪 → front matter 过滤 → 向量召回（每库可关闭）→ BM25 → RRF 融合 → 权威加权；向量不可用时明确降级
- **索引**：committed 内容走 Git 对象库（`git show <ref>:<path>`）；watcher、hook 和定时对账统一进入 SQLite 持久化队列，支持幂等、重试和重启恢复
- **Web 管理**：新增和配置知识库、查看队列与索引版本、审查提案 diff、管理线上/本地向量模型
- **模型依赖**：uv 管理项目；线上模型零本地 ML 依赖，只有首次运行本地模型才安装 `local-models` extra 并下载模型
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
    ├── pyproject.toml + uv.lock   # uv 项目；local-models 为可选 extra
    ├── kb_gateway/               # MCP + Web：registry / parser / indexer.queue / search / proposal / models
    ├── orchestrator/             # 薄层编排（不自研 DAG）：worktree / blackboard / artifacts / merger
    ├── workers/                  # WorkerAdapter + codex/opencode/gemini/claude 适配器
    └── config/                   # kb_registry.yaml / agent_grants.yaml / orchestrator.yaml
```

> 注意这个仓库自身的示范性：**它既是网关的代码工程，同时又是一个 `source_type: embedded` 的知识库**。`knowledge/` 目录随代码一起 Git 版本管理，改了设计文档和改代码走同一套评审。

---

## 快速开始（P0）

```bash
# 1. 基础安装（使用线上向量模型或内置占位模型；不安装本地机器学习依赖）
uv sync

# 2. 首次使用：复制本地注册表（含本机路径，不提交 Git）
cp config/kb_registry.example.yaml config/kb_registry.yaml
# PowerShell：Copy-Item config/kb_registry.example.yaml config/kb_registry.yaml

# 3. 按需注册其他知识库（也可用 kb_init_guide 获取清单）
uv run kb-gateway kb add --config config/kb_registry.yaml --id demo --source-type external --repo D:/code/kb-demo --knowledge-root knowledge

# 4. 构建索引（默认增量；--full 忽略内容哈希全量重建）
uv run kb-gateway reindex --config config/kb_registry.yaml --kb knowledge-base-mcp --full

# 5. 查看索引状态与分区
uv run kb-gateway list --config config/kb_registry.yaml --prefixes

# 6. 启动 MCP 服务（常驻 HTTP，客户端用 url 连接 http://127.0.0.1:8787/mcp）
uv run kb-gateway serve --config config/kb_registry.yaml --transport streamable-http --host 127.0.0.1 --port 8787

#   由客户端拉起进程时改用：--transport stdio

# 仅当准备运行本地向量模型时才安装；纯线上模式不需要
uv sync --extra local-models
```

P0 只做向量检索：`kb_search` 的 `effective_mode` 恒为 `vector`，请求 `hybrid`/`bm25` 会返回 `degraded_reason`。向量化默认使用 `type: builtin` 的离线哈希占位模型，仅用于冒烟，生产请配置 `online` 或 `local`；关闭 `vector_enabled` 的库在 P0 暂不可检索。

> P1+ 待实现：BM25 + RRF、watcher 与 Git hook、SQLite 持久化队列、HTTP 单实例与 Web 管理面（`http://127.0.0.1:8765/`，知识库/向量模型/索引进度/提案审查）、`kb_stat` 与 `doctor`。

最小可用配置：

```yaml
# config/kb_registry.yaml
knowledge_bases:
  - kb_id: standards                 # 唯一的跨项目标准库（独立仓库）
    source_type: external
    repo: D:/code/kb-standards
    default_authority: contract
    default_write: read_only
    vector_enabled: true             # 每库开关；false 时仅 BM25

  - kb_id: knowledge-base-mcp        # 本项目（内嵌 knowledge/）
    source_type: embedded
    repo: D:/code/knowledge-base-mcp
    knowledge_root: knowledge
    index_mode: git-object
    index_ref: main
    default_authority: note
    default_write: proposal_only
    vector_enabled: true
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
| **P0** | uv 项目 + registry + Markdown 分块 + LanceDB + `kb_search`/`kb_read`/`kb_list` + `kb_init_guide` | Cursor 能检索并溯源；其他项目仅凭工具即可获得完整建库清单 |
| **P1** | BM25 + RRF + front matter 过滤 + 每库 `vector_enabled` 与明确降级 | 搜字段命中映射行；关闭向量仍可 BM25 检索且不产生向量任务 |
| **P2** | git-object + watcher + hook + 定时对账 + SQLite 持久化队列 + Web 索引状态 | 提交后 30 秒内入队；重复事件幂等；hook 缺失可收敛；重启恢复任务 |
| **P3** | 提案 + base_version + 审计 + Web 知识库配置与提案 diff 审查 | 并发提案后者标 stale；Web 不能批准 stale，可新增和配置知识库 |
| **P4** | 现成 Agent 作调度器（OpenCode / Claude Code 原生 subagent）+ worktree 分配 + 黑板 + 产物合并 | 调度 Agent 自行拆解并派发子代理，各自在独立 worktree 工作，产物可合并 |
| **P5** | Web 线上/本地模型管理、按需 local-models、模型代次切换 + 产出归档/ADR/图片 caption | 基础安装无 ML 依赖；本地模型全程可观测；切换不混维度；知识流转闭环可运行 |

---

## 设计文档索引

两份文档是本仓库的知识源，也是实现依据：

| 文档 | 内容 |
|---|---|
| [knowledge/spec/01-多Agent共享知识库架构.md](knowledge/spec/01-多Agent共享知识库架构.md) | 架构与四层映射、Registry、7 个 MCP 工具、持久化队列、检索降级、Web 知识库/索引/提案/模型管理、uv 可选依赖、黑板与调度、工程部署和风险清单 |
| [knowledge/spec/02-Branch与Worktree与内嵌知识库.md](knowledge/spec/02-Branch与Worktree与内嵌知识库.md) | branch 与 worktree、Git 对象索引、ref 授权、watcher/hook/轮询对账、未提交变更处理、异步 hook、五种典型场景与反模式 |

---

## 明确不做

- 不做知识库的富文本编辑器（写文档用你的编辑器 / IDE / Obsidian）
- 不做 LLM 编排引擎（Worker 用现成 CLI Agent，本仓库只做调度与知识层）
- 不做多租户、成员管理、计费（个人 / 小团队场景不需要）
- 不做跨机分布式向量集群（单机 + 可选只读副本足够）
</content>
