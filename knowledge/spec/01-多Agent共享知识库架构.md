---
domain: [kb-gateway, multi-agent]
systems: [kb-gateway]
doc_type: rfc
status: frozen
owner: david
version: 1.2
related: [spec/02-Branch与Worktree与内嵌知识库.md]
---
# 多 Agent 并发开发 · 共享知识库架构设计

> 版本：v1.2（自建方案 · 增加初始化指导、持久化索引队列、Web 管理与按需模型运行时）
> 适用场景：个人 / 小团队，维护多个独立项目（如 ERP 中台 + OMS + RPS + PDM），需要多个编码 Agent 各自开发或由一个调度 Agent 统一编排并行开发，所有 Agent 共享同一套项目知识库。
> 配套文档：《Branch + Worktree 与内嵌知识库搭配使用指南》

---

## 1. 设计目标与约束

### 1.1 必须满足的能力

| 编号 | 能力 | 验收标准 |
|---|---|---|
| G1 | 知识库共享 | 任意数量的 Agent（含并行运行的）可同时读到同一份知识，检索结果一致、权限一致 |
| G2 | 知识库物理位置灵活 | 既能是独立的专门知识库目录（标准性资产），也能是某个项目仓库内部的 `knowledge/` 目录，对 Agent 完全透明 |
| G3 | 库内隔离 | 一个仓库 = 一个 `kb_id`，库内用目录前缀分区，检索可精确限定到某个/某几个分区，默认不串区 |
| G4 | 两种开发模式 | 模式一：单 Agent 直连知识库+仓库自行开发；模式二：调度 Agent 拆任务、并发派发、汇总 |
| G5 | 并发安全 | 多个 Agent 并行改代码不互相覆盖；并行写知识库不产生脏数据 |
| G6 | 产出回流 | Agent 开发产出（方案、SQL、脚本、决策记录）可沉淀回知识库，且不污染已冻结的契约 |
| G7 | 增量更新 | 知识库文档新增/修改后，无需人工全量重建即可被检索到 |
| G8 | 可溯源 | 任何一条检索结果能回答：来自哪个库、哪个文件、哪个版本、哪个分支 |
| G9 | 初始化指导 | 任意项目可通过 MCP 获取知识库目录、front matter、注册与首次索引的完整初始化清单 |
| G10 | 队列化索引 | 向量任务持久化、幂等、可重试，服务重启不丢任务，失败原因和索引进度可查询 |
| G11 | Web 管理 | 可管理知识库、每库向量开关、索引任务、提案审查和线上/本地向量模型 |
| G12 | 按需模型运行时 | 纯线上模式不安装机器学习依赖；仅首次运行本地模型时通过 uv 安装可选运行时 |

### 1.2 明确不做的事（边界）

- 不做知识库的富文本编辑器（写文档交给你熟悉的编辑器 / IDE / Obsidian）
- 不做 Agent 内部推理的编排框架（不自研 LLM 编排引擎，Worker 用现成 CLI Agent）
- 不做多租户 SaaS、成员管理、计费（个人/小团队不需要）
- 不做跨机分布式向量集群（单机 + 可选的远程只读副本即可）

### 1.3 关键权衡决策

| 决策点 | 选择 | 理由 | 放弃的方案及原因 |
|---|---|---|---|
| 知识库服务形态 | **单实例服务（MCP + Web，stdio 可选）** | 一套 MCP 对接所有 Worker；HTTP 实例同时挂载管理 API/UI，统一管理 watcher、队列与索引写入 | 独立 Web 索引进程：会产生多写者竞争；自研 Agent REST SDK：无法复用 MCP 工具调用循环 |
| 索引任务 | **SQLite 持久化队列** | 与元数据共用事务和备份，支持重启恢复、幂等、重试，个人场景无需额外 MQ | 内存队列：重启丢任务；独立 MQ：增加部署与运维成本 |
| 模型运行时 | **线上 / 本地双通道，本地依赖按需安装** | 线上配置即连即用；本地模型满足离线需求，基础安装保持轻量 | 基础依赖预装 torch 等库：即使用户只用线上模型也承担大量下载 |
| 向量存储 | **LanceDB（嵌入式，单目录）** | 零服务、单文件夹可备份、支持多表（一个 kb 一张表）、支持标量过滤 | Milvus/Qdrant 服务端：个人场景运维成本不值；Chroma：并发写弱于 LanceDB |
| 元数据存储 | **SQLite（WAL 模式）** | 单文件、事务可靠、支持并发读+串行写，天然适合个人场景 | PostgreSQL：为个人知识库引入 PG 只增加了部署负担 |
| 分块与解析 | 自建 Parser（Markdown 优先） | 必须自己控制表格、图片、代码块的处理方式，通用加载器会丢结构 | 通用文档加载库：表格被拍平、代码块被误切 |
| 共享语义 | **共享读 + 受控写（提案制）** | 多 Agent 并发直接改契约库必然产生脏数据；契约变更必须人工确认 | 全开放读写：并行 Agent 会互相覆盖，且契约漂移无法审计 |
| 并发隔离 | **每 Worker 一个 git worktree** | 文件系统级隔离，最彻底，零额外锁逻辑 | 同一工作区加文件锁：Agent 的 CLI 工具链假设独占工作区，加锁会破坏其行为 |
| Agent 间通信 | **共享黑板（Blackboard）** | 可控、可审计、无死锁，异构 Agent 之间唯一可行的通用通信方式 | 点对点直连：异构 CLI 之间无法建立会话，且易循环等待 |

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          使用者（你 / IDE / CLI）                          │
└───────────────┬──────────────────────────────────────┬───────────────────┘
                │ 模式一：单 Agent 直连                 │ 模式二：调度指令
                ▼                                      ▼
┌──────────────────────────────┐        ┌──────────────────────────────────┐
│  Worker Agent（单一开发者）   │        │  Orchestrator（调度 Agent）       │
│  Codex / OpenCode / Gemini    │        │  · 需求→任务 DAG 拆解             │
│  或 IDE 内 Agent              │        │  · Worker 选型与并发派发          │
│                               │        │  · 黑板仲裁 / 结果合并 / 冲突检测 │
└──────┬───────────────────────┘        └───────┬──────────────────────────┘
       │                                        │ spawn / 回收
       │                          ┌─────────────┼─────────────┬────────────┐
       │                          ▼             ▼             ▼            ▼
       │                    ┌─────────┐   ┌─────────┐   ┌─────────┐  ┌─────────┐
       │                    │Worker A │   │Worker B │   │Worker C │  │Worker N │
       │                    │ worktree│   │ worktree│   │ worktree│  │ worktree│
       │                    └────┬────┘   └────┬────┘   └────┬────┘  └────┬────┘
       │                         │            │            │            │
       └──────────────┬──────────┴────────────┴────────────┴────────────┘
                      │  MCP (JSON-RPC)  ——  所有 Agent 走同一条协议
                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     KB Gateway（MCP + Web，单实例）                        │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Tool 面：kb_init_guide / kb_list / kb_search / kb_read             │  │
│  │          kb_write_candidate / kb_stat / kb_reindex                 │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ 管理面：Web UI + /api（知识库 / 索引任务 / 提案 / 模型）            │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ 检索面：Query Router → 向量召回（可关闭）→ BM25 → RRF → 重排        │  │
│  │          （按 kb 授权列表 + 标签过滤器做前置裁剪）                  │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ 索引面：Watcher / Git Hook / Ref 对账 → SQLite 持久化任务队列      │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ 模型面：线上配置 / 本地下载与运行 / uv 可选依赖                     │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ 共享面：读写配额、提案队列、版本快照、并发写仲裁                    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────┬───────────────────────────────────┬───────────────────────┘
               │                                   │
               ▼                                   ▼
┌──────────────────────────────┐   ┌───────────────────────────────────────┐
│  KB Registry（注册表）        │   │  存储层                                │
│  kb_registry.yaml             │   │  ├─ LanceDB：每 kb 一张表（向量+全文） │
│  · kb_id / source_type        │   │  ├─ SQLite：元数据/版本/提案/索引任务  │
│  · repo / knowledge_root      │   │  ├─ Blackboard：SQLite 表（多 Agent 共享│
│  · prefix_rules（目录分区）   │   │  └─ 原始文件：KB 目录本身（不复制）    │
│  · read_grants（Agent 白名单）│   └───────────────────────────────────────┘
└──────────────┬───────────────┘
               │ 解析出物理路径
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   知识库物理层（两类资产，共 N 个仓库）                     │
│                                                                          │
│  ① 标准性资产仓库（独立 git 仓库，跨项目共享，通常只有一个）               │
│    D:/code/kb-standards/        → kb_id: standards                       │
│      ├── standards/               分区：编码规范、通用模板               │
│      └── cross-system/            分区：跨系统契约、字段映射             │
│                                                                          │
│  ② 项目仓库内嵌知识库（每个项目一个 kb_id，随项目 git 走）                 │
│    D:/code/erp-core/            → kb_id: erp-core                        │
│      ├── knowledge/                                                      │
│      │   ├── spec/          分区：本项目已冻结设计（contract 级）         │
│      │   ├── decisions/     分区：ADR、决策记录                          │
│      │   └── _draft/        分区：草稿，允许直写                          │
│      └── src/                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

**架构的核心关系只有三条：**

1. **Agent 不认识目录，只认识 `kb_id`（= 仓库）。** 物理位置是 KB Registry 的实现细节：文档在独立仓库还是在项目仓库的 `knowledge/` 里，对 Agent 完全透明，换项目、换形态都不用改提示词。库内分类靠 `prefixes`，不用新增 kb_id。
2. **一切共享走 MCP，不走文件系统直读。** 这样才能在网关层做授权裁剪、并发控制、结果溯源。Agent 允许直读源文件（`kb_read` 返回路径），但检索必须经网关。
3. **知识库只读，产出走提案。** Worker 不能直接写知识库正文，只能写黑板和提案队列，人工/调度 Agent 审核后归档。这是多 Agent 并发下不污染契约的唯一可靠办法。

---

## 3. 知识库层设计

### 3.1 知识库边界：仓库 = 知识库，目录 = 分区

**核心原则一句话：`kb_id` 绑定 git 仓库边界，不绑定内容主题。**

早期方案曾按主题拆成 4 个库（领域模型、跨系统契约、工程规范、项目文档）。实践下来这是过度设计：**同一份 Git 对象库被拆成多个 kb_id，只会付出 N 倍索引代价，换不到任何一项能力增强。** 正确的关系是四层 1:N 映射：

| 层级 | 对应物 | 基数 | 作用 |
|---|---|---|---|
| **git 仓库** | `kb_id` | 1 : 1 | 唯一需要注册、需要授权、需要钩子的单位 |
| **仓库内目录** | 分区（prefix） | 1 : N | 表达分类、权威级、写入策略 |
| **文件** | 文档 | 1 : N | 检索返回与溯源的最小单位 |
| **文档切片** | chunk | 1 : N | 向量与 BM25 的实际索引单位 |

按这个模型，整台知识库只需要两类、少数几个 kb：

| 仓库 | kb_id | 放什么 | 判据 |
|---|---|---|---|
| `kb-standards`（独立仓库） | `standards` | 编码规范、**跨系统契约与字段映射**、通用模板 | 被多个系统消费、生命周期独立于任何一个系统、变更需评审 |
| 每个项目仓库（自带 `knowledge/`） | `<项目名>` | 本项目的 `spec/`、`decisions/`、模块设计 | 只被本项目的 Agent 消费，随代码一起演进 |

> **跨系统契约（OMS↔ERP↔PDM 映射）应归 `standards`，不归任何单个项目仓库。**
> 判据不是"它重不重要"，而是**它被几个系统消费、变更由谁评审**。被多方消费、又独立于任一系统生命周期的，就是标准性资产。

**判定要不要独立 `kb_id`：三个问题，全部命中才拆**

1. 它在**不同的 git 仓库**里吗？
2. 它有**独立的权限所有者**吗？（个人场景基本恒为否）
3. 它需要**独立的索引触发时机**吗？（不同 hook、不同 ref 策略）

三条全否 → 同一个 `kb_id`。按个人/小团队场景，通常只有第 1 条会命中，而它命中的原因本来就是"仓库不同"。

**为什么同一仓库内拆多个 kb_id 是纯负收益：**

| 代价 | 说明 |
|---|---|
| 索引遍历 ×N | 同一份对象库被 `ls-tree` 扫 N 遍，未改动内容也重复处理 |
| ref 追踪点 ×N | 每个库各记一个 `last-indexed-rev`，一次提交跨分区就触发 N 次钩子 |
| 跨分区检索失效 | "OMS 改字段对 ERP 有何影响"本可一个 query 融合排序，拆开后只能分别查再手工合并 |
| 注册表膨胀 | 加一个模块就要改注册表 + 加授权 + 配钩子 |
| 收益 | **零** —— 目录前缀 + front matter 过滤已经能干完所有隔离 |

### 3.2 KB Registry：最小化配置

注册表只登记"仓库级"信息，分类交给目录规则，新增目录不需要改配置。

```yaml
# ~/.kb-gateway/kb_registry.yaml
version: 1

embedding:
  active_profile: openai-default       # 全局唯一活动配置，切换后按代次重建向量
  profiles:
    - profile_id: openai-default
      type: online                     # 不安装任何本地机器学习依赖
      provider: openai                 # openai | gemini | 兼容 OpenAI 的服务
      endpoint: https://api.openai.com/v1
      model: text-embedding-3-small
      secret_ref: env:OPENAI_API_KEY   # API 响应永不回显密钥
      dim: 1536
      batch_size: 16
    - profile_id: local-bge-m3
      type: local                      # 首次启动时才安装 local-models extra
      model: BAAI/bge-m3
      revision: main                   # 实际使用应固定为 commit SHA
      dim: 1024
      batch_size: 16

storage:
  lancedb_path: ~/.kb-gateway/data/lance
  sqlite_path: ~/.kb-gateway/data/meta.db
  blackboard_path: ~/.kb-gateway/data/blackboard.db
  model_cache_path: ~/.kb-gateway/models  # 本地下载只能落在该目录内

knowledge_bases:
  # ---------- 唯一一个"标准性资产"仓库 ----------
  - kb_id: standards
    display_name: 通用规范与跨系统契约
    source_type: external              # 独立仓库，不属于任何业务工程
    repo: D:/code/kb-standards
    include: ["**/*.md", "**/*.yaml", "**/*.sql", "**/*.png"]
    exclude: ["**/.git/**", "**/.obsidian/**", "**/_archive/**"]
    default_authority: contract        # 契约：检索结果带 authoritative 标记
    default_write: read_only           # 只读，Agent 只能提案
    vector_enabled: true               # false 时只维护 BM25，不生成向量任务

  # ---------- 项目仓库（每个项目一条，kb_id 固定写死） ----------
  - kb_id: erp-core
    display_name: ERP 主工程知识库
    source_type: embedded              # 知识库目录在项目仓库内
    repo: D:/code/erp-core
    knowledge_root: knowledge          # 相对仓库根，随 git 版本走
    index_mode: git-object             # 走对象库，不扫磁盘（详见配套文档《Branch + Worktree 与内嵌知识库》）
    index_ref: main                    # 默认索引并服务 main
    serve_refs: [main]                 # 对外可检索的 ref 白名单
    allow_working_ref: true            # 允许 working:<task> 草稿索引
    default_authority: note
    default_write: proposal_only
    vector_enabled: true
    prefix_rules:                      # 目录驱动的分区策略，glob 匹配
      - glob: "knowledge/spec/**"
        authority: contract            # 本项目内已冻结的设计视为契约
      - glob: "knowledge/_draft/**"
        write: true                    # 草稿区允许直接写
      - glob: "knowledge/_archive/**"
        excluded: true                 # 归档区不进索引

  - kb_id: oms-service                 # 同一套规则复用到其他项目
    display_name: OMS 服务知识库
    source_type: embedded
    repo: D:/code/oms-service
    knowledge_root: knowledge
    index_mode: git-object
    index_ref: main
    default_authority: note
    default_write: proposal_only
    vector_enabled: false              # 示例：仅全文检索
```

**字段语义说明：**

| 字段 | 取值 | 作用 |
|---|---|---|
| `source_type` | `external` / `embedded` | `external` 用 `repo` 绝对路径；`embedded` 用 `repo + knowledge_root` 组合 |
| `default_authority` | `contract` / `note` | 默认语义等级；`contract` 的检索结果带 `authoritative: true`，提示词中优先级最高 |
| `default_write` | `read_only` / `proposal_only` / `true` | 默认写入通道；`read_only` 连提案都不收 |
| `prefix_rules` | glob 列表 | 按目录覆盖 `authority` / `write` / `excluded`，比逐个声明分区更省配置 |
| `index_mode` | `git-object` / `filesystem` | `git-object` 从对象库读（版本确定、按 blob 去重）；`filesystem` 直扫目录 |
| `index_ref` | `main` / 其他分支 | 默认索引的基准分支 |
| `serve_refs` | 分支白名单 | 对外可检索的 ref；新增分支必须显式登记 |
| `allow_working_ref` | 布尔 | 是否允许 `ref=working:<task>` 索引未提交草稿 |
| `vector_enabled` | 布尔 | 每库向量开关；关闭后仍维护 BM25，不产生 embedding 任务，检索明确降级 |
| `embedding.active_profile` | profile ID | 全局活动向量模型；切换模型或维度会创建新索引代次，完成后原子切换 |

**检索侧对应的参数变化：**

```python
# 旧（4 库方案）：一次要列一堆 kb_id
kb_search(kb_ids=["oms-service", "cross-system", "engineering-std"], query=...)

# 新（仓库 + 分区）：
kb_search(kb_id="oms-service", prefixes=["spec/", "decisions/"], query=...)
kb_search(kb_id="standards",  prefixes=["cross-system/"],        query=...)
```

一个 `kb_id` + 若干前缀，语义更清楚，也不会出现"某个 kb 里混着两类文档"的模糊地带。

### 3.3 项目内嵌知识库（形态 B）的三个必须处理的坑

项目内嵌知识库看起来简单，但和 worktree 并发方案叠加后有三个硬问题，必须提前设计：

**坑 1：worktree 复制导致索引重复。**
`git worktree add` 会把 `knowledge/` 一起复制过去。若 Worker A、B、C 各有 worktree，网关会看到 4 份 `knowledge/`（主工作区 + 3 个 worktree）。

处理：注册表 `index_ref: main`，网关启动时用
```bash
git -C <project_root> worktree list --porcelain | head -1   # 取主工作区路径
```
只索引主工作区。worktree 内的 `knowledge/` 变更在 Worker 分支合并回主干后，由 `post-merge` hook 触发增量重索引。

**坑 2：项目仓库克隆/移动后 kb_id 漂移。**
使用派生规则：`kb_id = <project_basename> + "-" + <knowledge_dir_basename>`（`~/work/erp-core/knowledge` → `erp-core`）。注册表里显式写死 kb_id，不用运行时推导，避免 Agent 提示词里写死的 kb_id 失效。

**坑 3：项目内文档与代码的边界模糊。**
`knowledge/` 里既可能放 PRD，也可能放生成物。用 `exclude` 明确排除 `**/.kb-cache/**`、`**/dist/**`、`**/*.tmp`，并且要求 `knowledge/` 下按 `spec/ decisions/ _draft/` 固定分层（见 3.4）。

### 3.4 目录内容规范（两类仓库共用）

```
① 标准性资产仓库  kb_id = standards
D:/code/kb-standards/
├── index.md                     # 库入口：用途、维护约定（检索必读）
├── standards/                   # 分区：通用规范
│   ├── code-style.md
│   ├── api-convention.md
│   └── db-naming.md
├── cross-system/                # 分区：跨系统契约（被多方消费）
│   ├── mapping-oms-erp.md       # 字段映射表（Markdown 表格）
│   ├── event-order-lifecycle.md # 跨系统事件契约
│   └── openapi/
│       └── oms-to-erp-order.yaml
└── _archive/                    # 已废弃但需留档（不进索引）

② 项目仓库  kb_id = erp-core
D:/code/erp-core/
knowledge/
├── index.md                     # 本项目知识库入口
├── spec/                        # 分区：本项目已冻结设计（contract 级）
│   ├── schema-erp-核心表.md
│   └── rfc-2026-11-库存冻结策略.md
├── decisions/                   # 分区：ADR、决策记录（proposal_only）
│   ├── adr-001-为什么用最终一致性.md
│   └── adr-002-库存快照的取舍.md
├── _draft/                      # 分区：草稿区（可直接写，默认降权）
│   └── wip-订单拆分方案.md
└── attachments/                 # 图片、图表
    └── erp-state-machine.png
```

**两个仓库的分工是一条硬边界：**

| 内容 | 放哪 | 原因 |
|---|---|---|
| 跨系统契约、字段映射、事件定义 | `standards/cross-system/` | 被 OMS/ERP/PDM 多方消费，生命周期独立于任何单个系统 |
| 通用规范、命名约定、模板 | `standards/standards/` | 所有项目共用，变更需全量生效 |
| 本项目内部已冻结的设计 | `erp-core/knowledge/spec/` | 只被本项目消费，随代码版本演进 |
| 本次任务的决策与取舍 | `erp-core/knowledge/decisions/` | 过程性资产，需要提案写入 |
| 未定稿方案 | `erp-core/knowledge/_draft/` | 允许直写，检索默认降权或排除 |

**约定：**

- 文件名用 `类型-主题.md` 前缀，便于 BM25 命中（`rfc-`、`adr-`、`api-`、`flow-`）
- 每个文件头部必须有 YAML front matter，是过滤器的数据源：

```markdown
---
domain: [oms, erp]
systems: [oms, erp]
doc_type: mapping            # mapping | api | schema | rfc | adr | flow | guide
status: frozen               # draft | review | frozen | deprecated
owner: david
version: 1.3
effective_from: 2026-11-10
related: [cross-system/mapping-oms-erp.md]
---
```

注意：原来还有一个 `kb_scope` 字段，用于声明"这是跨库文档"。**在仓库=kb 的模型下这个字段已经多余**——文档属于哪个 kb 由它在哪个仓库里决定，不需要在文档里再声明一次。这也是单库收敛带来的一个直接简化：**元数据少一个维度，少一类不一致。**

`status: frozen` 是契约语义的落地，`deprecated` 会被检索默认排除。这套 front matter 让"跨系统协同"场景中的检索能做到 `domain=oms AND doc_type=api` 这类精确定位，而不是语义模糊地捞一堆。

### 3.5 解析与分块策略

普通文档加载器在知识库里会丢两类信息：**表格结构**和**代码块边界**。而 ERP 多系统协同恰恰最依赖字段映射表。

| 内容类型 | 处理方式 | 分块粒度 |
|---|---|---|
| 正文段落 | 按标题层级 + 语义段落切分 | 512 token，重叠 64 |
| **Markdown 表格** | 整表不切分；行数 > 20 时按 10 行一组切分并**重复表头** | 每块携带表头 + 前两行上下文 |
| **代码块** | 整块保留，不参与语义切分；`sql` 块额外抽取表名/字段名进 BM25 | 单块，超过 800 token 按 `;` 分割 |
| **接口 YAML（OpenAPI）** | 按 `paths` 下每个 endpoint 一块，块内拼入 `parameters + requestBody + responses` | 每 endpoint 一块 |
| 图片 | 抽取 alt 文本 + 文件名 + 所在段落作为 caption 向量化；文件路径存入元数据供 `kb_read` 返回 | 每图一块 |
| 图片（可选增强） | `embedding.vision: true` 时用多模态模型生成描述文本后走文本向量 | 每图一块 |

**表格为什么必须单独处理：** 如果按行拍平，"OMS 订单号 → ERP so_id"这条映射会散落在两个相邻块里，检索命中率骤降。保留表头 + 重复表头是唯一能让单行映射独立可检索的办法。

### 3.6 检索流程（混合召回 + RRF）

单一向量检索在技术文档上表现不稳定（字段名、接口名这类精确 token 容易被语义漂移掉），因此必须做混合：

```
query
  │
  ├─ 0. 授权裁剪：调用方传入的 (kb_id, prefixes) ∩ Agent grants → 有效范围
  │    （不在授权内的库或目录直接不参与召回，不存在"召回后再过滤"）
  │
  ├─ 1. 过滤器前置：从 front matter 解析出的标量字段
  │    doc_type / status / domain / systems / effective_from
  │    → LanceDB where 条件，先裁剪候选池
  │
  ├─ 2. 向量召回：top_k * 3（仅 vector_enabled=true 且向量代次 ready）
  │    若向量关闭、未就绪或模型不可用则跳过，并记录 degraded_reason
  │
  ├─ 3. BM25 召回：对 enriched_text（正文 + 表头 + 代码符号名）做全文检索，top_k * 3
  │
  ├─ 4. RRF 融合：score = Σ 1/(60 + rank_i)
  │    仅 BM25 可用时直接按全文排序，不伪造向量分数
  │
  ├─ 5. 权威加权：authoritative=true（来自 contract 库）的块 × 1.15
  │    status=frozen × 1.1；status=deprecated 直接剔除
  │
  └─ 6. 去重 + 截断 top_k，返回带溯源字段的结果
```

**返回结构（每个命中项）：**

```json
{
  "kb_id": "standards",
  "partition": "cross-system",
  "doc_id": "sha1:9f2c...",
  "path": "cross-system/mapping-oms-erp.md",
  "abs_path": "D:/code/kb-standards/cross-system/mapping-oms-erp.md",
  "chunk_index": 3,
  "doc_type": "mapping",
  "status": "frozen",
  "authoritative": true,
  "version": "1.3",
  "git_ref": "main@a1b2c3d",
  "score": 0.87,
  "matched_by": ["vector", "bm25"],
  "content": "| OMS 字段 | ERP 字段 | 转换规则 |\n|---|---|---|\n| order_no | so_id | 原值 |",
  "table_header": ["OMS 字段", "ERP 字段", "转换规则"]
}
```

`partition` 是从 `path` 前缀解析出来的分区名，用于让 Agent 知道"这条结果属于哪一类"，而不必再判断 kb_id。

`abs_path` 与 `git_ref` 是溯源能力的核心：Agent 拿到结果后可以直接 `kb_read(abs_path)` 读全文，也可以在你人工核对时一眼定位到"哪条分支的哪个提交"。

`kb_search` 的响应头另含 `effective_mode`（`hybrid` / `bm25` / `vector`）与 `degraded_reason`。`vector_enabled=false` 时，默认 `hybrid` 自动降级为 BM25；调用方显式请求 `mode=vector` 则返回 `vector_disabled`，提示在 Web 或注册表中开启。重新开启后，服务按内容哈希与模型代次补建缺失向量，ready 前仍明确降级检索。

### 3.7 共享语义：为什么必须是"共享读 + 提案写"

多 Agent 并发开发时，知识库的共享不是一个"能不能读"的问题，而是一个"谁能改"的问题。

| 模式 | 并发表现 | 契约完整性 | 结论 |
|---|---|---|---|
| 全开放读写 | 两个 Worker 同时改同一份映射表 → 后写覆盖先写，无冲突提示 | 破坏，无法审计 | ❌ 禁止 |
| 只读（Agent 完全不写） | 稳定 | 完整 | ⚠️ 可行但知识无法沉淀，产出散落在各 worktree |
| **共享读 + 提案写** | 稳定 | 完整，变更全程留痕 | ✅ 采用 |

**提案机制（三段式）：**

```
Worker 产出                提案队列                    归档
    │                         │                          │
    ├─ kb_write_candidate ───▶│ proposal 表（状态 pending）│
    │  (kb_id, target_path,   │  带 diff / 原文 / 摘要     │
    │   content, reason,      │  带 agent_id / task_id     │
    │   task_id)              │  带 base_version（防覆盖）  │
    │                         │                          │
    │                    ┌────┴────┐                     │
    │                    ▼         ▼                     │
    │              Web/人工/调度审核  自动拒绝              │
    │                    │                                 │
    │                    ▼                                 │
    │              kb_commit_proposal ────────────────────▶ 写入 KB 目录
    │              （校验 base_version 未变）                + 触发增量索引
    │                                                     + 记入 audit 表
```

关键点：
- `base_version` 校验：提案基于的文档版本若已被别人改过，提案自动标 `stale`，需重新生成。这解决了并发写冲突。
- `target_path` 必须在 `proposal_allow` 白名单内（如 `decisions/`、`rfc/`、`artifacts/`），**不允许直接提案改 `api/`、`db/mapping-*`**——契约类文件的变更必须走人。
- 提案内容存 SQLite，Agent 不需要有 KB 目录写权限（注册表只给网关进程写权限，Worker 进程无）。
- Web 审查页必须展示 diff、提交者、理由、`base_version` 与审计记录；批准前再次校验版本，`stale` 提案禁止批准，只能拒绝或要求重新生成。批准后的文档变更统一进入持久化索引队列。

---

## 4. MCP 接口设计

网关对外暴露 7 个工具，收敛得越少越好，Agent 提示词才好写。

| 工具 | 入参 | 返回 | 用途 |
|---|---|---|---|
| `kb_init_guide` | `source_type`, `repo?`, `kb_id?`, `knowledge_root=knowledge`, `check=false` | 初始化步骤、目录/front matter/registry 模板、接入命令；`check=true` 时附校验结果 | 指导其他项目创建并接入知识库 |
| `kb_list` | `include_prefixes?` | kb 清单 + 每个库的 `doc_count / last_indexed / vector_enabled / default_authority / default_write` | Agent 启动时自检可用知识域 |
| `kb_search` | `query`, `kb_id`, `prefixes[]`, `refs?`, `filters?`, `top_k=5`, `mode=hybrid` | 命中块数组（见 3.6 结构）+ `effective_mode / degraded_reason` | 主力检索入口 |
| `kb_read` | `path` 或 `doc_id`, `with_frontmatter=true` | 原文全文 + front matter + git_ref | 精读单篇；也用于 Agent 读源码级文档 |
| `kb_write_candidate` | `kb_id`, `target_path`, `content`, `reason`, `task_id` | `proposal_id` + 状态 | 产出沉淀 |
| `kb_stat` | `kb_id`, `prefixes?` | 文档/分块/向量块统计、向量维度、`observed_rev/indexed_rev`、dirty 状态、队列数量、索引延迟、模型代次与最后错误 | 排查"文档明明有却搜不到" |
| `kb_reindex` | `kb_id`, `ref=main`, `full=false` | `job_id` + `queued` 状态 | 异步手动修复索引 |

**`kb_init_guide` 的安全边界：** 它只返回指导和可选校验结果，不创建目录、不写 registry、不安装 hook、不下载模型。返回内容按 `source_type` 区分独立库与项目内嵌库，包含 `knowledge/index.md`、front matter、registry 片段、CLI/Web 创建方式、首次索引和 MCP 接入步骤；`check=true` 时只读校验提供的 `repo`。实际变更必须由 CLI 或 Web 的明确操作执行，避免 Agent 借指导工具获得隐式文件写权限。

**`kb_search` 为什么要强制传 `kb_id` + `prefixes`（不给默认全局搜索）：**

多项目场景下，"默认搜全部库"是事故来源——OMS 的 Worker 捞到 PDM 的旧契约，照着写代码，联调必炸。强制显式传库和前缀，等于强制每次检索都做一次授权确认。调度 Agent 在派发任务时就把 `kb_id + prefixes` 写进 Worker 的任务描述，Worker 无法自我扩权。

> 注意：这里从"多个 kb_id"收敛成了"一个 kb_id + 若干目录前缀"，但**授权强度没有降低**。前缀白名单同样在网关侧硬校验，越权直接报错而不是返回空结果。

**MCP 接入配置（三种典型客户端）：**

```json
// Cursor: ~/.cursor/mcp.json
{
  "mcpServers": {
    "kb-gateway": {
      "command": "uv",
      "args": ["run", "--project", "D:/code/knowledge-base-mcp", "kb-gateway", "serve", "--transport", "stdio"],
      "env": {
        "KB_CONFIG": "/home/david/.kb-gateway/kb_registry.yaml",
        "KB_AGENT_ID": "cursor-main"
      }
    }
  }
}
```

```toml
# Codex CLI: ~/.codex/config.toml
[mcp_servers.kb-gateway]
command = "uv"
args = ["run", "--project", "D:/code/knowledge-base-mcp", "kb-gateway", "serve", "--transport", "stdio"]
env = { KB_CONFIG = "/home/david/.kb-gateway/kb_registry.yaml", KB_AGENT_ID = "codex-worker-1" }
```

```json
// OpenCode / Gemini CLI: 项目级 mcp.json
{
  "mcpServers": {
    "kb-gateway": {
      "url": "http://127.0.0.1:8765/mcp",     // HTTP 模式，多 Worker 共享一个网关进程
      "headers": { "X-Agent-Id": "opencode-worker-2" }
    }
  }
}
```

> **stdio vs HTTP 的选择**：单 Agent 用 stdio（进程内、零网络、最快启动）；**多 Worker 并发必须用 HTTP 单实例**。stdio 模式下每个 Worker 各起一个网关进程，会各自维护一份索引写入器和 watcher，导致同一文件被多进程重复索引、写入竞争。HTTP 单实例 + `X-Agent-Id` 头是所有 Agent 共享同一份索引的前提。

---

## 5. Agent 执行层设计

### 5.1 统一 Worker Adapter

三种 CLI Agent 的调用方式差异很大（参数、会话保持、输出格式），用一个薄适配层吸收：

```python
# workers/base.py —— 伪代码，示意接口契约
class WorkerAdapter(Protocol):
    agent_id: str
    backend: Literal["codex", "opencode", "gemini"]

    def prepare(self, task: Task, worktree: Path) -> None:
        """注入任务规则文件（AGENTS.md / CLAUDE.md 等），声明本任务的 (kb_id, prefixes) 授权"""

    def run(self, task: Task, worktree: Path, timeout: int) -> WorkerResult:
        """以子进程方式执行，捕获 stdout/stderr，解析结构化结果"""

    def collect_artifacts(self, worktree: Path) -> list[Artifact]:
        """收集本 Worker 产出的 diff、新增文件、SQL 脚本"""
```

| Adapter | 调用方式 | 关键注意 |
|---|---|---|
| `CodexAdapter` | `codex exec --cwd <worktree> "<prompt>"` 或 exec 模式读 stdin | 需 `--full-auto` 或按需审批；超时必须 `SIGTERM` 后回收子进程组 |
| `OpenCodeAdapter` | `opencode run --dir <worktree>` | BYOK，模型走代理时注意 `baseURL` 指向 |
| `GeminiAdapter` | `gemini -p "<prompt>" --cwd` | 上下文窗口大，适合前端/长文档任务 |

三个 Adapter 共享同一套 `Task` 结构，**Worker 的差别只体现在后端能力和成本，不影响调度逻辑**。这也是异构并行能成立的原因。

### 5.2 模式一：单 Agent 直连

```
你 ──▶ Worker Agent ──MCP──▶ KB Gateway ──▶ 知识库
              │
              └──▶ 项目仓库（普通工作区，非 worktree）
```

流程：
1. Worker 启动，读项目根目录的 Agent 规则文件（内含本项目的 `kb_id` 与前缀授权清单）
2. 需要查资料 → `kb_search(kb_id="erp-core", prefixes=["spec/", "decisions/"], query=...)`
3. 需要精读 → `kb_read(path)`
4. 开发完 → 产出写回走 `kb_write_candidate`
5. 契约类变更由你人工改，Agent 只在提案里给出建议

**这种方式下 KB Gateway 就是"外挂长期记忆"**，Agent 的上下文窗口多大都不重要，因为它可以在任何一轮重新检索。

### 5.3 模式二：调度 Agent 并行

```
你 ──需求──▶ Orchestrator
                 │
                 ├─ 1. 需求解析 → 任务 DAG（节点=子任务，边=依赖）
                 ├─ 2. 节点绑定：backend 选型 + (kb_id, prefixes) 授权 + 是否需 worktree
                 ├─ 3. 无依赖节点并发派发（并发上限 N，建议 3~4）
                 ├─ 4. 回收结果 → 黑板写入 → 冲突检测
                 ├─ 5. 依赖节点解阻 → 下一批派发
                 └─ 6. 全部完成 → 合并 diff → 汇总报告 → 产出提案
```

**任务 DAG 节点结构：**

```yaml
- id: T1
  name: OMS 订单表结构与索引设计
  backend: codex
  kb: { id: oms-service, prefixes: ["spec/", "decisions/"] }   # 本项目库，只看自己分区
  depends_on: []
  output_contract: "sql_ddl + index_rationale"
  blackboard_write: ["schema.oms_order"]

- id: T2
  name: OMS→ERP 字段映射校验
  backend: gemini
  kb:
    - { id: standards,   prefixes: ["cross-system/"] }          # 跨系统契约的唯一来源
    - { id: oms-service, prefixes: ["spec/"] }
    - { id: erp-core,    prefixes: ["spec/"] }
  depends_on: []
  output_contract: "mapping_diff_report"
  blackboard_write: ["contract.mapping_check"]

- id: T3
  name: 订单同步服务实现
  backend: opencode
  kb:
    - { id: standards,   prefixes: ["cross-system/"] }
    - { id: oms-service, prefixes: ["spec/", "decisions/"] }
  depends_on: [T1, T2]                 # 需要表结构 + 映射确认
  blackboard_read: ["schema.oms_order", "contract.mapping_check"]
```

这里体现了一个关键设计：**Worker 之间不直接通信，但通过黑板共享数据契约。** T3 不需要"和 T1 对话"，它只需要读 T1 写在黑板的 `schema.oms_order`。这比让两个异构 CLI Agent 互相发消息可靠得多。

### 5.4 并发隔离：worktree 生命周期

```
派发前：  git -C <repo> worktree add .worktrees/w-<task_id> -b agent/<task_id>
执行中：  Worker 独占该目录，读写自由，不碰主工作区
回收后：  产物 diff 存 artifact 表 → worktree 保留（供人工审查）或按策略清理
合并时：  Orchestrator 按 DAG 拓扑顺序做三方合并；冲突则回退给对应 Worker 重做
```

**为什么必须是 worktree 而不是同目录 + 锁：**
Codex/OpenCode/Gemini 这些 CLI Agent 工具链假设自己独占工作目录（会执行 `git status`、`git diff`、写入临时文件、跑 build）。加文件锁无法阻止它们读别人半成品状态，也无法阻止 build 产物互相污染。worktree 是文件系统级硬隔离，是唯一零调试成本的做法。

**worktree 的额外收益**：每个 Worker 的改动天然在独立分支，审查时 `git diff agent/T1..main` 就是它的完整产出，不需要从日志里猜它改了什么。

---

## 6. 黑板（Blackboard）：异构 Agent 的共享状态

### 6.1 数据模型（SQLite，WAL 模式）

```sql
-- 黑板条目：所有跨 Worker 的共享事实都在这里
CREATE TABLE blackboard_entry (
    entry_id       TEXT PRIMARY KEY,              -- ULID，按时间可排序
    run_id         TEXT NOT NULL,                 -- 一次调度会话 ID
    key            TEXT NOT NULL,                 -- 逻辑键，如 schema.oms_order
    writer_agent   TEXT NOT NULL,                 -- 写入者 agent_id
    writer_task    TEXT NOT NULL,                 -- 来源任务 ID
    content        TEXT NOT NULL,                 -- 正文（Markdown / JSON / SQL）
    content_type   TEXT NOT NULL DEFAULT 'markdown'
                   CHECK (content_type IN ('markdown','json','sql','diff','text')),
    version        INTEGER NOT NULL DEFAULT 1,    -- 同 key 的版本号，递增
    status         TEXT NOT NULL DEFAULT 'ready'
                   CHECK (status IN ('draft','ready','superseded','conflict')),
    read_scope     TEXT NOT NULL DEFAULT 'run',   -- run=本调度内可见 | global=跨调度持久
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, key, version)                 -- 同 key 允许并存多版本，供仲裁
);

-- 谁读了什么：用于依赖判定、影响面分析，也是审计基础
CREATE TABLE blackboard_access (
    access_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     TEXT NOT NULL REFERENCES blackboard_entry(entry_id) ON DELETE CASCADE,
    agent_id     TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    access_type  TEXT NOT NULL CHECK (access_type IN ('read','write')),
    read_version INTEGER,                         -- 读到的是第几版，用于检测"读了旧版"
    accessed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 写入租约：防止同一 key 被两个 Worker 同时写
CREATE TABLE blackboard_lease (
    key          TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    holder_agent TEXT NOT NULL,
    acquired_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL,                   -- 超时自动释放，防死锁
    PRIMARY KEY (run_id, key)
);

-- 冲突记录：同 key 多版本且内容不一致时由 Orchestrator 登记
CREATE TABLE blackboard_conflict (
    conflict_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    key          TEXT NOT NULL,
    versions     TEXT NOT NULL,                   -- JSON: [1,2]
    diff_summary TEXT,
    resolution   TEXT DEFAULT 'pending'
                 CHECK (resolution IN ('pending','auto_merged','human_decided','rolled_back')),
    decided_by   TEXT,
    decided_at   TEXT
);

CREATE INDEX idx_bb_run_key    ON blackboard_entry(run_id, key, version DESC);
CREATE INDEX idx_bb_writer     ON blackboard_entry(writer_agent, created_at DESC);
CREATE INDEX idx_bb_status     ON blackboard_entry(run_id, status) WHERE status IN ('draft','conflict');
CREATE INDEX idx_acc_entry     ON blackboard_access(entry_id, access_type);
CREATE UNIQUE INDEX idx_lease_holder ON blackboard_lease(run_id, holder_agent);
```

**三个设计要点：**

1. `UNIQUE (run_id, key, version)` 而不是 `UNIQUE (run_id, key)` —— 允许同 key 并存多版本，冲突不是靠"锁死"而是靠"并存 + 事后仲裁"，这在异构 Agent 并行时更实际（你无法预先知道会不会撞）。
2. `blackboard_lease` 带 `expires_at` —— Worker 崩溃/超时后租约自动失效，避免整个调度被一个挂掉的 Agent 卡死。这是黑板模式最容易踩的坑。
3. `blackboard_access` 记录 `read_version` —— 若一个 Worker 读了 v1，而 v2 已出现，Orchestrator 能识别出"它基于过期信息开发"，主动要求重跑。

### 6.2 通信模型对比

| 模型 | 异构 CLI Agent 可行性 | 死锁风险 | 上下文污染 | 审计 | 结论 |
|---|---|---|---|---|---|
| 点对点直连（Agent 互调） | ❌ 三种 CLI 无法互相建立会话 | 高（A 等 B，B 等 A） | 高 | 难 | 不可行 |
| 消息队列（每个 Agent 一个 inbox） | 中（需为每种 CLI 包装轮询机制） | 中 | 中 | 中 | 包装成本高 |
| **共享黑板（读写代 key）** | ✅ 只需 MCP 4 个工具 | 低（租约超时兜底） | 低（按 key 隔离） | 高（全量表记录） | **采用** |
| LangGraph State（框架内共享） | ✅ 但绑定框架 | 低 | 低 | 中 | 作为 Orchestrator 内部实现，不对外 |

**注意**：黑板是 **Orchestrator 提供给 Worker 的共享存储**，不是 Worker 之间的实时对话通道。Worker 只在"开始前读入"和"完成后写出"两个时点接触黑板。这个约束看起来降低了通信能力，实际上正是它让异构 Agent 并行可用的原因——异构建模无法支持实时对话。

---

## 7. 编排层：自建 vs 借用

| 维度 | 纯自建（Python asyncio） | LangGraph | 现成成品（Munder Difflin 等） |
|---|---|---|---|
| DAG 依赖控制 | 完全自主，代码可控 | 原生支持，`Send` 并行 | 依赖 Agent 规划，不可控 |
| 异构 Worker 调用 | 完全自主 | 通过自定义节点 | 需按其规则适配 |
| 黑板共享 | 直接复用第 6 章 SQLite | 需与 State 分离，两套状态 | 内置但不开放 |
| 人工卡点 / 重试 / 并发上限 | 需自己实现 | 需自己实现（原生较弱） | 部分内置 |
| 可观测性 | 需自建（简单日志即可） | LangSmith | 自带 UI |
| 学习/维护成本 | 中（约 300~500 行核心代码） | 中 | 低（但黑盒） |
| 适配你的场景 | ✅ 推荐 | ✅ 可选 | ⚠️ 只适合验证，不适合长期 |

**建议**：Orchestrator 用 `asyncio + 一个 200 行的 DAG 引擎`自建。理由不是"炫技"，而是你的场景有 4 个现成框架都给不了的东西：

1. 三种异构 CLI 的超时/杀进程组语义各不相同，必须自己控
2. 黑板要和知识库提案队列打通（Worker 产出→黑板→提案→KB 归档）
3. 每个任务要绑定不同的 `(kb_id, 前缀)` 授权范围
4. worktree 生命周期要精确控制（保留供审查 vs 清理）

这四点都发生在框架的"边界外"，框架只能包住 DAG 本身，收益不大但增加一层抽象。

---

## 8. 知识流转闭环

这是整个方案最有价值的部分：让每个 Worker 的开发产出自动变成组织记忆。

```
          ┌─────────────────────────────────────────────┐
          │                                             │
          ▼                                             │
   ┌─────────────┐   检索    ┌──────────┐               │
   │  KB 知识库  │◀─────────▶│  Worker  │               │
   │ (只读契约)  │           │ 开发中    │               │
   └──────┬──────┘           └────┬─────┘               │
          │                       │ 产出                  │
          │                       ▼                       │
          │               ┌──────────────┐                │
          │               │  artifact 表 │  (diff/SQL/    │
          │               │              │   报告/脚本)    │
          │               └──────┬───────┘                │
          │                      │                        │
          │              ┌───────▼────────┐               │
          │              │  黑板 blackboard│  跨 Worker 共享 │
          │              └───────┬────────┘               │
          │                      │                        │
          │              ┌───────▼────────────┐           │
          │              │ proposal 提案队列   │           │
          │              └───────┬────────────┘           │
          │                      │ 人工/调度审核           │
          └──────────────────────┘ 归档 + 索引              │
                     (回到知识库，形成下一个任务的上下文)  ─┘
```

**分层归档规则（防止知识库变成垃圾场）：**

| 产出类型 | 归档位置 | 触发方式 | 是否需要人工 |
|---|---|---|---|
| 代码本身 | 不回 KB，留在 git | 分支合并 | 不需要 |
| 本次任务的决策/踩坑 | `decisions/adr-*.md` | 提案，每个任务最多 1 条 | 抽查 |
| 稳定的映射表、契约补充 | `db/`、`api/`（contract 库） | 提案但**必须人工合并** | 必须 |
| 可复用脚本/SQL | `artifacts/scripts/` | 自动归档（`write_policy` 允许） | 抽查 |
| 大段调试日志 | 不归档，只存 artifact 表 7 天后清理 | 自动 | 不需要 |

**这条闭环的价值**：第二次做同类任务时，`kb_search` 会先命中上次的 ADR 和踩坑记录，Worker 不需要从零推导。多项目协同场景下尤其明显——OMS 改字段，下次 ERP 的 Worker 一搜就能看到"上次这个字段转换踩了什么坑"。

---

## 9. 存储与性能

### 9.1 规模假设与选型验证

| 项目 | 假设值 | 选型是否能撑住 |
|---|---|---|
| 知识库文档数 | 3~5 个库，每库 500~3000 篇 | LanceDB 单表 10 万级向量无压力 |
| 分块总数 | 约 3~10 万块 | 单机内存索引约 200~400MB |
| 并发 Worker 数 | 3~6 | SQLite WAL 支持多读 + 单写串行，够用 |
| 检索 QPS | 峰值 20~30（6 Worker × 每任务 5 次检索） | LanceDB 单机 50~100 QPS 可满足 |
| 索引增量延迟 | 目标 < 30s | watcher debounce 5s + 增量 upsert 可达成 |

**性能瓶颈预判**：真正的瓶颈不是检索，而是 **embedding 计算**。本地 `bge-m3` 在 CPU 上约 20~40 块/秒，全量重建 1 万块需要 4~8 分钟。因此：

- 增量索引用 debounce 合并（同一文件 5 秒内多次修改只算一次）
- 全量重建放在后台线程 + 进度上报，不阻塞检索（检索读旧索引，重建完成后原子切换）
- embedding 批量 `batch_size=16`，可调至 32（内存换速度）

### 9.2 索引一致性策略

```
写入路径：变更事件 → debounce / 内容哈希 → SQLite 持久化入队 → 解析分块
         → BM25 / metadata 更新 → embedding（仅 vector_enabled=true）
         → LanceDB 按代次 upsert → 标记任务完成 → 推进索引版本

一致性保证：
  · 每次 upsert 以 doc_id + model_generation 为粒度替换旧块，避免残留
  · 同一 doc 的并发修改由单一 indexer 写者串行发布，embedding 可有界并行
  · 索引状态表记录每个 doc 的 content_hash，hash 未变则跳过（幂等）
  · 任一文档任务失败则不推进对应 ref 的 indexed_rev，旧索引继续服务
```

### 9.3 持久化索引队列与状态

索引任务与文档元数据共用 SQLite（WAL），不引入独立 MQ。任务类型至少包括 `index_document`、`delete_document`、`rebuild_kb`、`runtime_install` 和 `model_download`，统一在 Web 展示；文档任务字段如下：

| 字段组 | 内容 |
|---|---|
| 定位 | `job_id`、`kb_id`、`ref`、`path`、`operation` |
| 幂等 | `content_hash + model_generation`，与定位字段组成去重键 |
| 调度 | `status`、`priority`、`attempt`、`next_retry_at`、`lease_owner/lease_expires_at` |
| 结果 | `total_chunks / embedded_chunks`、`error`、`created_at / started_at / finished_at` |

状态机为 `queued → running → succeeded`；可恢复错误进入 `retry_wait` 后指数退避，超过上限进入 `failed`，也可手动取消为 `cancelled`。服务重启时将过期租约的 `running` 任务回收为 `queued`，chunk ID 由 `(kb_id, ref, path, content_hash, chunk_index, model_generation)` 确定化，保证重放不会制造重复块。

- **去重与合并**：watcher、hook、轮询和手动重建可以重复通知；相同去重键的未完成任务只保留一个。同一文件在去抖窗口内连续变化，只保留最新内容哈希，旧任务若已开始则其结果不得覆盖新代次。
- **进度与版本**：每库每 ref 记录 `observed_rev`、`indexed_rev`、dirty/待提交状态、各状态任务数、当前模型代次和最后错误。只有该 rev 的全部变更任务成功后才推进 `indexed_rev`；hook 不得提前写“已索引”。
- **向量开关**：`vector_enabled=false` 时只执行解析、元数据和 BM25 更新，不创建 embedding 子任务，并取消该库尚未开始的向量任务；旧向量不参与检索。重新开启后按内容哈希与模型代次补建，未 ready 前继续使用 BM25。
- **模型切换**：模型或维度变化生成新的 `model_generation`，为所有 `vector_enabled=true` 的库创建全量任务。旧代次继续检索，新代次按库完整构建后原子切换，失败不影响旧代。
- **背压**：embedding 采用有界并发和固定批大小，队列长度、重试和死信均可见；不得因本地模型推理阻塞 MCP/Web 请求。

---

## 10. 工程结构

```
kb-dev-system/
├── pyproject.toml                  # uv 项目、基础依赖与 local-models optional extra
├── uv.lock                         # 锁定依赖；基础同步不包含机器学习运行时
├── kb_gateway/                     # MCP + Web 单实例知识库网关
│   ├── server.py                   # MCP / 管理 API / Web UI 入口
│   ├── tools.py                    # 7 个 MCP 工具实现（含 kb_init_guide）
│   ├── web/                        # 知识库、索引任务、提案和模型管理页面/API
│   ├── models/                     # 线上客户端、本地下载/运行、密钥与状态管理
│   ├── registry.py                 # kb_registry.yaml 解析、两种 source_type 统一
│   ├── parser/
│   │   ├── markdown.py             # 表格/代码块/front matter 结构化解析
│   │   ├── openapi.py              # OpenAPI 按 endpoint 切分
│   │   └── image.py                # caption 抽取 / 可选多模态描述
│   ├── chunker.py                  # 分块策略（表格保留表头）
│   ├── embedder.py                 # 活动模型路由；本地运行时延迟导入
│   ├── indexer/
│   │   ├── watcher.py              # watchdog 去抖 + Git ref / 文件指纹定时对账
│   │   ├── queue.py                # SQLite 持久化任务、租约、重试与幂等
│   │   ├── full_rebuild.py         # 后台全量重建 + 模型代次原子切换
│   │   └── state.py                # content_hash、observed_rev、indexed_rev 状态
│   ├── search/
│   │   ├── vector.py               # LanceDB 检索
│   │   ├── bm25.py                 # 全文检索（含代码符号名）
│   │   └── fusion.py               # RRF + 权威加权
│   ├── proposal.py                 # 提案队列、base_version 校验、归档
│   └── audit.py                    # 审计日志
│
├── orchestrator/                   # 调度层（可选启用）
│   ├── planner.py                  # 需求 → 任务 DAG
│   ├── scheduler.py                # asyncio DAG 引擎、并发上限、超时
│   ├── dispatcher.py               # worktree 分配 + Worker 派发
│   ├── blackboard.py               # 第 6 章 SQLite 实现
│   ├── merger.py                   # 产物合并、冲突检测
│   └── reporter.py                 # 汇总报告 + 产出提案
│
├── workers/
│   ├── base.py                     # WorkerAdapter 协议
│   ├── codex.py / opencode.py / gemini.py
│   └── rules/                      # 各后端注入的规则模板（含 kb + prefixes 声明）
│
├── config/
│   ├── kb_registry.yaml            # 见 3.2
│   ├── agent_grants.yaml           # Agent → 允许 (kb_id, 前缀) 白名单
│   └── orchestrator.yaml           # 并发上限、超时、重试
│
└── scripts/
    ├── kb_init.sh                  # 初始化目录、写 index.md 模板、注册
    ├── kb_index.sh                 # 手动触发索引
    └── git_hooks/post-merge        # 合并后触发增量索引
```

**`agent_grants.yaml` —— 授权的唯一来源：**

```yaml
grants:
  - agent_id: cursor-main
    allow:
      - { kb: erp-core,    prefixes: ["*"] }        # 你本人：全库可读
      - { kb: standards,   prefixes: ["*"] }
    write: proposal

  - agent_id: codex-worker-1                        # ERP 侧 Worker
    allow:
      - { kb: erp-core,    prefixes: ["spec/", "decisions/"] }
      - { kb: standards,   prefixes: ["standards/", "cross-system/"] }
    write: proposal

  - agent_id: gemini-worker-1                       # 映射校验 Worker
    allow:
      - { kb: standards,   prefixes: ["cross-system/"] }   # 只给契约分区
      - { kb: erp-core,    prefixes: ["spec/"] }
    write: proposal

  - agent_id: opencode-worker-1                     # OMS 侧 Worker
    allow:
      - { kb: oms-service, prefixes: ["spec/", "decisions/"] }
      - { kb: standards,   prefixes: ["standards/"] }
    write: none                                     # 只读，产出由调度 Agent 代提交
```

> `prefixes: ["*"]` 表示该 kb 全部分区。前缀同样支持 glob（`["api/**"]`），与注册表的 `prefix_rules` 用同一套匹配实现。

**默认拒绝**：`kb_search` 收到不在 grants 里的 `(kb_id, prefix)` 组合直接报错并记审计日志，而不是静默过滤。这样 Agent 的越权尝试会立刻暴露在你的日志里，而不是悄悄拿到空结果继续瞎写。

---

## 11. 部署与启动顺序

```bash
# 1. 基础安装（适用于线上模型，不安装 torch/transformers 等本地运行时）
uv sync
uv run kb-gateway init --config ~/.kb-gateway/kb_registry.yaml

# 2. 建唯一的"标准性资产"仓库（也可在 Web 新增）
mkdir -p D:/code/kb-standards/{standards,cross-system}
uv run kb-gateway kb add --id standards --source-type external --repo D:/code/kb-standards

# 3. 建项目内嵌知识库（Agent 可先调用 kb_init_guide 获取清单）
cd D:/code/erp-core
mkdir -p knowledge/{spec,decisions,_draft}
uv run --project D:/code/knowledge-base-mcp kb-gateway kb add --id erp-core --source-type embedded --repo . --knowledge-root knowledge

# 4. 在终端一启动常驻服务（MCP + Web + watcher + 队列消费者）
uv run --project D:/code/knowledge-base-mcp kb-gateway serve --transport http --host 127.0.0.1 --port 8765 --watch
# Web: http://127.0.0.1:8765/

# 5. 在终端二触发首次全量索引（异步入队；也可在 Web 操作）
uv run --project D:/code/knowledge-base-mcp kb-gateway reindex --all --full --ref main --progress

# 6. 接入各 Agent（见 4 章配置片段），验证
uv run --project D:/code/knowledge-base-mcp kb-gateway doctor --agent cursor-main

# 仅当准备运行本地模型时才需要安装；Web 首次启动本地模型会执行等价任务
uv sync --extra local-models
```

**`doctor` 子命令是必要的**——知识库这类基础设施的故障往往表现为"Agent 说没找到资料"，根因可能是授权配错、watcher 没起、队列积压、本地运行时缺失、embedding 维度不匹配、路径符号链接没解析。把这些检查收敛成一条命令，能省掉大量排查时间。

---

## 12. 分阶段实施

| 阶段 | 范围 | 预计工作量 | 验收 |
|---|---|---|---|
| **P0 打底** | uv 项目 + registry + Markdown 分块 + LanceDB + `kb_search`/`kb_read`/`kb_list` + `kb_init_guide` | 2~3 天 | Cursor 能检索并溯源；新项目仅凭初始化工具即可获得完整建库清单 |
| **P1 混合检索** | BM25 + RRF + front matter 过滤 + 每库 `vector_enabled` 与明确降级 | 2 天 | 精确字段命中映射行；关闭向量后仍可 BM25 检索且不产生向量任务 |
| **P2 双形态与队列** | embedded 库 + git hook + watcher + 定时对账 + SQLite 持久化队列 + Web 索引状态 | 1~2 天 | 提交后 30 秒内入队可检索；重复事件幂等；hook 缺失可收敛；重启任务恢复 |
| **P3 共享写与 Web** | proposal + base_version + 审计 + Web 知识库配置/提案 diff 审查 | 2 天 | 并发提案后者标 stale；Web 可新增配置知识库且不能批准 stale 提案 |
| **P4 并发编排** | Orchestrator：DAG 引擎 + worktree 分配 + 三个 Adapter + 黑板 + 合并冲突检测 | 3~5 天 | 三个异构 Worker 并行跑不同任务，产物可合并，黑板数据正确传递 |
| **P5 模型与闭环增强** | Web 线上/本地模型管理、按需 local-models、模型代次切换 + 产出归档/ADR/图片 caption | 2 天 | 基础安装无 ML 依赖；本地安装下载运行可观测；模型切换不混维度；知识流转闭环可运行 |

**建议严格按 P0→P5 推进，每个阶段结束你都应该能实际用起来。** 不要一次把 P4 的编排写完再回头调检索——检索质量不到位时，并行 Worker 只会更快地产出错误代码。

---

## 13. 风险与避坑清单

| 风险 | 表现 | 对策 |
|---|---|---|
| **索引重复** | worktree 或重复事件导致同一文档被索引 N 次 | committed 索引走 git-object + 队列去重键 + 单写者发布 |
| **手工修改漏索引** | 绕过 Worker 改文档，或 hook 漏装/事件丢失 | watcher 去抖 + hook 通知 + 定时 ref/内容指纹对账；git-object 未提交只标待提交 |
| **队列状态失真** | hook 已写完成但 embedding 失败，或服务重启丢任务 | hook 只入队；全部任务成功后推进 indexed_rev；持久化租约与重试 |
| **契约漂移** | 多个 Worker 各自"理解"接口，代码对不上 | contract 库标记 `authoritative`，检索结果加权，提示词强制"契约优先" |
| **提案污染** | Agent 大量低质提案淹没审核队列 | 每任务提案数上限（如 3 条）+ `target_path` 白名单 + 自动去重（content_hash） |
| **黑板死锁** | Worker 崩溃后 key 租约不释放 | 租约 `expires_at` + 调度器定期清理 |
| **检索噪音** | 语义检索把无关文档排前面 | 强制传 `kb_id + prefixes` + `doc_type`/`status` 前置过滤 + RRF 融合 |
| **embedding 打架** | 不同模型/维度混入同一索引，检索结果不可比 | 全局唯一活动配置；模型切换新建 model_generation，完整重建后按库原子切换 |
| **本地依赖过重** | 只用线上模型也被迫下载 torch 等大型库 | 基础 uv 依赖排除 ML 库；仅 Web 启动本地模型时安装 local-models extra |
| **密钥或模型路径泄露** | API 回显密钥，或模型下载写到任意目录 | secret_ref + 响应脱敏；下载路径限制在 model_cache_path 并拒绝目录穿越 |
| **Agent 越权** | Worker 检索到不该看的目录 | grants 的 (kb_id, prefix) 白名单 + 默认拒绝 + 审计日志告警 |
| **上下文爆炸** | 一次检索返回几十块塞满窗口 | `top_k` 默认 5，返回内容截断 + 只回 `chunk` 不回全文 + 需要时再 `kb_read` |
| **表格丢失** | 映射表按行拍平，字段搜不到 | 表格整表切分 + 重复表头（3.5） |
| **路径符号链接** | 知识库根目录是软链，watcher 不跟随 | 注册表加载时 `realpath` 归一化，全部内部用绝对真实路径 |

---

## 14. 与现成方案的对比（为什么自建是对的）

| 维度 | 本方案（自建） | TencentDB-Agent-Memory | Dify 知识库 | Munder Difflin |
|---|---|---|---|---|
| 知识库两种物理形态（独立/项目内嵌） | ✅ 原生支持 | ⚠️ 需手动同步 | ❌ 仅平台内 | ❌ 无 |
| 库/分区授权 + 越权拒绝 | ✅ 白名单硬校验 | ✅ 有 ACL | ⚠️ 权限较粗 | ❌ |
| 契约/过程/产出三类资产分层 | ✅ authority + write_policy | ✅ 有资产分类 | ⚠️ 需靠标签 | ❌ |
| Markdown 表格/代码块专项处理 | ✅ 自控 | ❌ 通用切片 | ❌ 通用切片 | ❌ |
| 增量索引（watcher + hook + 对账 + 持久化队列） | ✅ | ❌ 手动 ingest | ⚠️ 手动/部分 | ❌ |
| 异构 Worker（codex/opencode/gemini） | ✅ Adapter 自由 | ⚠️ 需走 Proxy | ❌ 不涉及 | ✅ 原生 |
| 黑板通信 | ✅ 完全自控 | ⚠️ 记忆层非通信层 | ❌ | ✅ 内置 |
| 部署复杂度 | 低（uv + 单进程 + 嵌入式存储，本地模型按需） | 中（PG/TCVDB + 服务） | 高（容器/DB/账号） | 低（桌面应用） |
| 可改造性 | ✅ 全代码在手 | ⚠️ 团队向，改动成本高 | ⚠️ 二次开发门槛 | ❌ 黑盒 |
| 适合你的场景（个人/多项目/契约协同） | ✅ | ⚠️ 团队包袱重 | ⚠️ 太重且不灵活 | ⚠️ 只能验证不能长期 |

**结论**：你的核心诉求是"共享 + 授权 + 双形态 + 契约优先 + 增量"，这五项组合没有任何现成系统同时满足。TencentDB-Agent-Memory 最接近，但它的定位是团队记忆底座（多租户、成员、权限体系都是为多人设计的），且缺文件监听和表格专项处理；Dify 定位是应用侧 RAG，物理形态被平台锁死。自建的成本集中在 P0~P2（约 5~7 天），换来的是这套底座未来所有项目都能复用、且随时可以按需扩展。

---

## 15. Web 管理与向量模型

### 15.1 单实例管理面

`kb-gateway serve --transport http` 在同一进程挂载 `/mcp`、管理 REST `/api` 和 Web UI。Watcher、定时对账、任务调度器、索引写入器和模型管理器都由这一实例持有；Web 不另起索引进程。默认仅监听 `127.0.0.1`，如绑定非回环地址必须另配访问认证。

| 页面 | 核心能力 | 主要 API |
|---|---|---|
| 知识库 | 新增、编辑、启停、校验路径与授权、设置 `vector_enabled`、触发全量/增量重建 | `GET/POST /api/kbs`、`GET/PATCH /api/kbs/{kb_id}`、`POST /api/kbs/{kb_id}/reindex` |
| 索引状态 | 展示 `observed_rev/indexed_rev`、向量块数/维度、dirty/待提交、队列进度、模型代次、最后错误；重试失败任务 | `GET /api/kbs/{kb_id}/index-status`、`GET /api/jobs`、`POST /api/jobs/{job_id}/retry` |
| 提案审查 | 查看 diff、提交者、理由、base_version 和审计；批准/拒绝，禁止批准 stale | `GET /api/proposals`、`GET /api/proposals/{id}`、`POST /api/proposals/{id}/approve|reject` |
| 向量模型 | 新增/编辑配置、线上连接测试、切换活动配置、本地安装/下载/启动/停止与进度 | `/api/models`、`/api/models/{id}/test|activate|install|download|start|stop` |

知识库新增和配置修改先校验 `kb_id` 唯一性、真实路径、Git 仓库边界和目录权限，再原子更新 registry 并记录审计。关闭向量不会删除文档和 BM25；模型、索引及提案操作全部复用第 9 章的持久化任务与状态，不通过 UI 同步阻塞执行。

### 15.2 线上与本地模型

模型配置支持多个 profile，但 `embedding.active_profile` 同时只有一个，所有启用向量的库共用该活动配置，避免同一检索链路混入不同模型或维度。

- **线上模型**：填写 provider、endpoint、model、dim、batch_size 和 API key。密钥保存为 `secret_ref`，读取接口只返回 `configured: true` 等脱敏状态；连接测试不会触发任何本地机器学习依赖安装。
- **本地模型**：填写受支持的模型 ID、固定 revision 和运行时参数。模型文件只能下载到 `storage.model_cache_path` 内由服务生成的目录，拒绝自定义越界路径与目录穿越；记录下载进度、校验结果、runtime 状态和错误。
- **状态展示**：运行时安装、模型下载、模型运行是三个独立状态。Web 分别展示 `installing/installed/failed`、下载百分比与 `stopped/starting/running/unhealthy`，失败可重试，切换模型后按第 9 章重建索引代次。

### 15.3 uv 与按需依赖

项目由 uv 管理，`pyproject.toml` 声明基础依赖和可选 extra，`uv.lock` 锁定版本：

```toml
[project.optional-dependencies]
local-models = [
  "torch",
  "transformers",
  "sentence-transformers",
]
```

基础 `uv sync` 和纯线上模式只安装 MCP、Web、解析、检索等基础依赖，**不得包含本地机器学习库**，服务入口也不得在启动时导入这些库。首次在 Web 点击“启动本地模型”时，服务先创建 `runtime_install` 任务，通过 uv 安装 `local-models` extra，再确保模型已下载并启动；本地运行时采用延迟导入/独立子进程隔离。**只有启动本地模型才触发运行时安装，仅下载模型文件不安装机器学习依赖**；CLI 可执行等价的 `uv sync --extra local-models`。

---

## 附录 A：Agent 提示词模板（直接放进规则文件）

```markdown
## 知识库使用规则

本项目可用知识范围（越权调用会直接报错，不是返回空结果）：
- `{project_kb}` + 前缀 `spec/`、`decisions/`：本项目已冻结设计与决策记录
- `{project_kb}` + 前缀 `_draft/`：本分支草稿（默认降权，仅自己使用）
- `standards` + 前缀 `cross-system/`：跨系统契约与字段映射
- `standards` + 前缀 `standards/`：通用编码与命名规范

强制规则：
1. 涉及接口、字段映射、状态机、同步规则时，**必须先检索 `standards/cross-system/` 和本项目 `spec/`**，
   不允许凭记忆或推测编写契约相关内容。
2. 检索结果中 authoritative=true 的片段是最高优先级事实，与之冲突的代码/注释一律以知识库为准。
3. 检索结果 status=deprecated 的片段不得作为实现依据。
4. 每次检索必须显式给出 kb_id 与 prefixes；需要未授权的资料时，报告缺少授权而不是绕过。
5. 任务完成后，若产生了新的决策、踩坑结论或可复用脚本，调用 kb_write_candidate 提交提案，
   target_path 只能是 `decisions/` 或 `_draft/`，不得提交 `spec/` 与 `standards/cross-system/`（契约必须人工合并）。
6. 需要读全文时用 kb_read(abs_path)，不要用 kb_search 反复试关键词。   ← 省 token 的关键
```

## 附录 B：关键技术参数建议

| 参数 | 建议值 | 说明 |
|---|---|---|
| 分块大小 | 512 token / 重叠 64 | 技术文档的甜点区；表格例外 |
| 向量召回宽度 | `top_k * 3` | 给 RRF 留融合空间 |
| RRF 常数 k | 60 | 通用经验值 |
| 权威加权 | contract ×1.15，frozen ×1.1 | 契约优先但不绝对压制 |
| 并发 Worker 上限 | 3~4（本地模型）/ 6~8（云端模型） | 受 embedding 与 LLM 限流约束 |
| watcher debounce | 5s | 兼顾实时性与 embedding 开销 |
| worktree 保留 | 合并后保留 7 天 | 供人工审查，之后按需清理 |
| 提案单任务上限 | 3 条 | 防审核队列被淹没 |
</content>
