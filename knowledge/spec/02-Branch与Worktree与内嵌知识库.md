---
domain: [kb-gateway, git]
systems: [git, kb-gateway]
doc_type: guide
status: frozen
owner: david
version: 1.0
related: [spec/01-多Agent共享知识库架构.md]
---
# Branch + Worktree 与内嵌知识库的搭配使用指南

> 配套文档：《多 Agent 并发开发 · 共享知识库架构设计》
> 本文只解决一个问题：**当知识库的物理载体是项目仓库里的 `knowledge/` 目录时，多分支并行开发下它该怎么索引、怎么检索、怎么避免读到错误的版本。**
> 版本：v1.0

---

## 1. 先把三个东西分清

很多混乱来自把这三者的层级搞混了。它们不在同一个层面上：

| 概念 | 本质 | 在哪一层 | 它是"一个"还是"多个" |
|---|---|---|---|
| **Branch（分支）** | 一条提交链的指针，`.git/refs/heads/*` 下的一个引用 | Git 的**逻辑版本层** | 一个仓库可有任意多个 |
| **Worktree（工作树）** | 一个绑定到某分支的**磁盘工作目录** + 它专属的 HEAD | Git 的**物理视图层** | 一个仓库可有多个，但同一分支同时只能被一个 worktree 签出 |
| **内嵌知识库（`knowledge/`）** | 仓库里被 Git 跟踪的普通目录，内容是 Markdown/YAML/图片 | 项目**内容层**，随分支走 | 每个 worktree 磁盘上各有一份副本，Git 对象库里按 blob 内容去重 |

一句话概括三者的关系：

> **branch 是"哪个版本"，worktree 是"这个版本被摊开在哪个磁盘目录"，内嵌知识库是"跟着版本一起被搬运的内容"。**
> 改了分支 → 内容跟着变；开了 worktree → 内容被复制成多份磁盘副本。

**关键推论**：`knowledge/` 的副本数量 = 当前活跃 worktree 数量 + 主工作区。这就是所有问题的根源。

---

## 2. 为什么单靠 Branch 不行

只用 `git checkout` 切分支时，磁盘上**永远只有一份 `knowledge/`**：

```
erp-core/                  ← 唯一目录
├── src/
└── knowledge/             ← 切到哪个分支，它就是哪个分支的内容
```

此时索引看起来很"简单"——扫一遍目录就行。但代价是：

| 问题 | 表现 |
|---|---|
| 无法并行 | 两个 Agent 要同时开发 OMS 同步和 PDM 下发，只能抢同一个目录 |
| 切换污染 | `checkout` 会把未提交的文档改动带到另一个分支，或者直接被覆盖 |
| 索引抖动 | 每次切分支，整个 `knowledge/` 的 mtime 全变，watcher 触发全量重索引 |
| 版本不可溯源 | 索引里只知道"这个文件长这样"，不知道它属于哪个 commit |

所以只要进入"多 Agent 并行"，就必然要 worktree。而一旦用了 worktree，"扫目录建索引"这个最直觉的做法就死了。

---

## 3. 用了 Worktree 之后，目录长什么样

```
~/work/erp-core/                        ← 主工作区（main），knowledge 基线
├── .git/
├── src/
└── knowledge/                           ← 索引的"基准源"

~/work/.worktrees/
├── w-t1-oms-sync/                     ← worktree#1，feature/oms-sync 分支
│   ├── src/
│   └── knowledge/                     ← feature 分支上的副本（可能已被改）
├── w-t2-pdm-masterdata/               ← worktree#2，feature/pdm 分支
│   ├── src/
│   └── knowledge/                     ← 另一份副本
└── w-t3-rps-reconcile/                ← worktree#3
    ├── src/
    └── knowledge/
```

**此时磁盘上有 4 份 `knowledge/`**（主工作区 + 3 个 worktree）。三个 Worker 各自在自己的目录里开发，读自己那份副本——这对**代码**是正确行为，但对**知识库**是灾难：

- Worker A 读到的 `knowledge/db/mapping-oms-erp.md` 是 feature 分支上的草稿版本
- Worker B 读到的是 main 上的冻结版本
- 两个 Agent 基于不同的契约写代码，合并时必然对不上

**所以核心命题是：知识库的正确性不能由"Agent 在哪个目录"决定，必须由"知识库属于哪个版本"决定。**

---

## 4. 核心设计：索引建在 Git 对象库上，不建在文件系统上

这是整个搭配方案的技术支点，值得展开讲。

### 4.1 为什么不能扫目录

扫目录的方案有个隐含假设：**磁盘上的文件就是唯一事实**。在 worktree 场景下这个假设直接崩了——磁盘上有 N 份文件，谁是事实？

### 4.2 Git 对象库天然解决了这个问题

`git worktree` 的多份工作目录，**共享同一个 `.git/objects` 对象库**。也就是说：

```
4 份磁盘副本   →  1 个对象库（按 blob 内容哈希去重）
                      ↑
              每份内容只存一次，且带唯一 hash
```

于是可以完全绕开磁盘，直接从对象库读取某个 ref 下的 `knowledge/` 内容：

```bash
# 列出 main 分支上 knowledge/ 下所有文件及其 blob hash
git -C ~/work/erp-core ls-tree -r --long main -- knowledge/

# 直接读出某个 ref 下某个文件的内容（不碰磁盘）
git -C ~/work/erp-core show main:knowledge/db/mapping-oms-erp.md

# 拿到该文件最后一次变更的提交信息（天然的溯源）
git -C ~/work/erp-core log -1 --format='%H%n%an%n%ad%n%s' main -- knowledge/db/mapping-oms-erp.md
```

**这一套读法的三个好处：**

1. **版本确定**：索引的每一块都明确属于 `main@a1b2c3d`，与磁盘上有几个 worktree 无关
2. **自动去重**：blob hash 相同 → 内容相同 → 只 embed 一次。三个分支共享 95% 的未改动文档时，只付 5% 的 embedding 成本
3. **可溯源到提交**：检索结果能直接告诉你是哪个 commit、谁改的、什么时候改的

### 4.3 但也要处理"未提交的草稿"

Git 对象库只包含已提交内容。而特性开发中，Agent 往往在 worktree 里改了 `knowledge/` 但还没提交。这部分必须能被该分支的 Agent 读到，否则"边开发边更新文档"就用不了。

因此索引要分**两个通道**：

| 通道 | 数据源 | 何时启用 | 服务谁 |
|---|---|---|---|
| **committed 通道** | `git show <ref>:<path>`（对象库） | 常驻，随 ref 变化增量重建 | 所有 Agent |
| **working 通道** | 特定 worktree 的磁盘目录 | 按需，Agent 显式请求 `ref=working:<worktree>` | 仅该 worktree 内的 Agent |

两条通道的索引条目**打不同的 `index_source` 标记**，检索时默认只走 committed 通道，读草稿必须显式指定。这是第 6 章"路由规则"的基础。

---

## 5. 检索路由：谁该看到哪个版本

设计原则：**默认最严格，越权必须显式且留痕。**

### 5.1 三种 ref 语义

```yaml
# 调用 kb_search 时传入的 ref 参数
ref: main                    # 基准契约：所有 Agent 默认读这里
ref: feature/oms-sync        # 本分支已提交内容：只有该分支 Agent 可读
ref: working:<task_id>       # 本分支未提交草稿：只有该 worktree 的 Agent 可读
```

### 5.2 默认策略表

| Agent 所处位置 | 默认 ref | 可显式扩展到 | 永远不可读 |
|---|---|---|---|
| 主工作区（你本人 / 审查 Agent） | `main` | 任意已提交 ref | — |
| worktree 内的 Worker | **`main`** | 自己的 `feature/*` 和自己的 `working:*` | 其他分支的 `feature/*`、其他 worktree 的 `working:*` |
| 调度 Agent（Orchestrator） | `main` | 任务 DAG 中显式声明的 ref | `working:*`（草稿不进调度视野） |

**为什么 Worker 默认读 main 而不是自己的分支分支？**

因为 Agent 的自我认知不可靠。让 Worker 默认读自己分支上的草稿，等于让每个 Worker 都基于"可能是错的"契约写代码。反过来，默认读 main + 显式请求草稿，把"读未定稿"变成一个需要主动决定、且能记进审计日志的动作。

### 5.3 一个真实的任务走查

```
任务：给 OMS 新增「仓库冻结」字段，需同步到 ERP

T1（Worker A，codex）在 w-t1 开发 OMS 侧
  ├─ kb_search(kb_ids=[oms-service], ref=main, query="订单字段定义")
  │    → 读到 main 上已冻结的字段表，确认新增字段的命名规范 ✅
  ├─ 修改 w-t1/knowledge/api/oms-order.md（草稿，未提交）
  └─ kb_search(kb_ids=[oms-service], ref=working:w-t1, query="冻结字段")
       → 显式读自己刚写的草稿 ✅

T2（Worker B，gemini）在 w-t2 做映射校验
  ├─ kb_search(kb_ids=[cross-system, erp-core], ref=main, query="订单字段映射")
  │    → 读到 main 上的冻结映射表 ✅
  └─ 尝试 kb_search(kb_ids=[oms-service], ref=working:w-t1)  ← ❌ 拒绝
       → 报错 insufficient_grant，日志记录越权尝试
       → B 只能等 A 的提案合并进 main，或由调度 Agent 用黑板传递字段定义
```

这个例子体现了整套设计的目的：**并行开发，但契约只有一个真相源。**

---

## 6. 索引与回流：什么时候重建

### 6.1 触发时机

| 事件 | 触发什么 | 说明 |
|---|---|---|
| 手动 `kb reindex --kb erp-core --ref main` | committed 通道全量重建 | 首次建库、模型更换 |
| `post-merge` hook（合并进 main） | committed 通道增量重建（diff 出变更的 blob） | **最重要的一条** |
| `post-checkout` hook | 若切到被登记的分支，增量重建对应 ref | 可选 |
| worktree 内 Agent 主动 `kb reindex --ref working:<task>` | working 通道索引该目录 | 开发中看草稿 |
| 定时扫描（每 15 分钟） | 对已登记 ref 做 `git rev-parse` 比对，变了才重建 | 兜底，防止 hook 漏装 |

### 6.2 post-merge hook（可直接用）

```bash
#!/bin/bash
# .git/hooks/post-merge
# 作用：合并进当前分支后，只重建发生变化的 blob，不全量重跑
# 安装：chmod +x .git/hooks/post-merge

set -euo pipefail

KB_ID="erp-core"                 # 对应 kb_registry.yaml 里的 kb_id
KNOWLEDGE_DIR="knowledge"        # 内嵌知识库目录（相对仓库根）
OLD_REF_FILE=".git/kb-last-indexed-rev"   # 记录上次索引到的 commit

# 1) 取上次索引的 commit；没有就退化为全量
if [ -f "$OLD_REF_FILE" ]; then
  OLD_REV=$(cat "$OLD_REF_FILE")
else
  OLD_REV=$(git rev-list --max-parents=0 HEAD | tail -1)   # 首个提交
fi
NEW_REV=$(git rev-parse HEAD)

# 2) 只取 knowledge 目录下变更的文件列表（A/M/D）
CHANGED=$(git diff --name-status "$OLD_REV" "$NEW_REV" -- "$KNOWLEDGE_DIR" || true)

if [ -z "$CHANGED" ]; then
  echo "[kb] knowledge 无变更，跳过索引"
  exit 0
fi

# 3) 增量重建：带 ref + 变更文件清单交给网关
#    网关内部按 blob hash 判断是否需要重新 embed（内容没变就复用旧向量）
echo "$CHANGED" | kb-gateway reindex \
  --kb "$KB_ID" \
  --ref "$(git rev-parse --abbrev-ref HEAD)" \
  --source git-object \
  --changed-from-stdin \
  --repo "$(git rev-parse --show-toplevel)"

# 4) 记录本次已索引到的 commit
echo "$NEW_REV" > "$OLD_REF_FILE"
echo "[kb] 索引完成 → $NEW_REV"
```

**这段 hook 的三个设计要点：**

- `--source git-object`：明确让网关走对象库读取，而不是磁盘扫描
- 传「变更文件清单」而不是「变更 commit 范围」：网关可以只对这几个 path 做 `ls-tree`，开销与文件数成正比而非与提交数成正比
- 记录 `kb-last-indexed-rev`：避免每次 merge 都从头 diff；也让异常中断后能从断点续

### 6.3 合并即回流的语义

这里有个容易被忽略但很关键的点：

> **`knowledge/` 的变更是分两步生效的：**
> 第一步，在 feature 分支上是「草稿」，只对该分支的 Agent 可见；
> 第二步，合并进 main 后，才升级为「契约」，对所有 Agent 可见。

所以 `knowledge/` 里必须有清晰的状态标记，否则合并后无法区分"这份文档是否已经过评审"：

```yaml
---
doc_type: mapping
status: draft              # draft → review → frozen
version: 1.3
owner: david
reviewed_by: null          # 合并前为空
---
```

`status: draft` 的块默认不进检索结果（或降权），`frozen` 才作为权威事实。这样即使某个 Agent 不慎读到了分支草稿，也不会把它当作契约实现。

---

## 7. 派生 kb_id：让 Agent 不用关心路径

内嵌知识库的 `kb_id` 必须**在注册表里显式写死**，不能运行时从目录名推导。原因：Worker 的提示词里写的是 `kb_ids=[erp-core]`，如果 kb_id 随项目改名/移动漂移，所有提示词同时失效。

```yaml
knowledge_bases:
  - kb_id: erp-core                  # 固定，永不变
    source_type: embedded
    project_root: ~/work/erp-core
    root_dir: knowledge
    index_mode: git-object           # 关键：走对象库，不扫目录
    index_ref: main                  # 默认索引并服务 main
    serve_refs: [main]               # 对外可检索的 ref 白名单
    allow_working_ref: true          # 允许 working:<task> 草稿索引
    worktree_root: ~/work/.worktrees # worktree 存放根，用于解析 working ref
```

`serve_refs` 是白名单而不是黑名单——新增分支默认不可检索，必须显式登记，避免有人随手开个 20 个分支把索引撑爆。

---

## 8. 四种典型场景的完整走法

### 场景 A：日常单分支维护（无 worktree）
```
你在 ~/work/erp-core 直接改 knowledge/*.md → 提交到 main
→ post-commit/post-merge hook → 增量索引 ref=main
→ Agent 检索立刻可见
```
这是最常用的路径，与是否有 worktree 无关。

### 场景 B：多 Agent 并行特性开发（worktree 隔离）
```
Orchestrator 派发 T1/T2/T3
→ git worktree add .worktrees/w-t1 -b feature/oms-sync
→ Worker A 在 w-t1 内开发，kb 检索默认 ref=main
→ A 改了自己分支的 knowledge → 只能通过 ref=working:w-t1 自己看到
→ A 提交 feature/oms-sync → Orchestrator 登记该 ref（可选）
→ 合并进 main → post-merge 重建 ref=main → 全场可见
```

### 场景 C：Agent 之间要传递"我改了契约"这个信息
**不要靠读对方 worktree 的 knowledge。** 走黑板：
```
Worker A 完成 → kb_write_candidate(提案) + blackboard_write("schema.oms_order")
Worker B ← blackboard_read("schema.oms_order") 拿到结构化定义
人工/调度审核提案 → 合并进 main → 索引重建
```
原因：worktree 里的草稿可能随时被 Agent 继续改，拿去给另一个 Agent 用就是移动的靶子。黑板里的内容是**某一时刻的快照**，有版本号，可仲裁，这是它比"读别人目录"可靠的根本原因。

### 场景 D：回滚 / 追溯
```
用户问："这个字段映射什么时候变成这样的？"
→ 检索结果带回 git_ref: main@a1b2c3d
→ git log -1 a1b2c3d -- knowledge/db/mapping-oms-erp.md  → 拿到提交人和时间
→ 需要看旧版：kb_search(..., ref=<old_commit>) 或直接 git show <old>:path
```

---

## 9. 反模式清单（这几条踩了会很难查）

| 反模式 | 为什么错 | 正确做法 |
|---|---|---|
| 让 watcher 扫描所有 worktree 目录 | 同一文档索引 N 份，检索结果随机命中不同版本 | 走 `git-object` 通道，只按 ref 索引 |
| 用目录路径当 kb_id | 项目改名/移动后提示词全失效 | kb_id 写死在注册表 |
| Worker 默认读自己分支的 knowledge | 每个 Agent 基于不同契约开发，合并必炸 | 默认 `ref=main`，草稿必须显式请求 |
| 用 `mtime` 判断文件是否变化 | `checkout`/`worktree add` 会把所有 mtime 刷新，触发无意义全量重建 | 用 blob hash / commit diff 判断 |
| 分支草稿直接供其他 Agent 检索 | 对方改一次你就过期一次 | 走黑板快照 + 提案合并 |
| 只装 hook 不做兜底扫描 | hook 漏装（老仓库、clone 后未安装）时索引静默不更新 | 保留 15 分钟 `rev-parse` 比对兜底 |
| 把 `knowledge/` 放进 `.gitignore` | 知识库失去版本、失去溯源，退回文件系统方案 | 必须被跟踪；大文件用 Git LFS |
| 每个 worktree 都跑一个网关进程 | 多进程抢同一份索引文件和 watcher 事件 | 单实例 HTTP 网关，多 Agent 共用 |

---

## 10. 落地检查清单

上线这套搭配前，逐条确认：

- [ ] `knowledge/` 已被 Git 跟踪，且在 `.gitattributes` 里对二进制用 LFS
- [ ] 注册表里 `index_mode: git-object`，`index_ref: main`
- [ ] `kb_id` 显式写死，不依赖目录名
- [ ] `serve_refs` 白名单已配置，新增分支需手动登记
- [ ] `post-merge` hook 已安装且可执行（`chmod +x`），并在克隆后自动安装（用 `core.hooksPath` 指向版本库内的 hooks 目录）
- [ ] `post-merge` 能正确记录 `kb-last-indexed-rev`
- [ ] 每篇 `knowledge/` 文档有 front matter，含 `doc_type` / `status` / `version`
- [ ] `draft` 状态默认降权或排除
- [ ] 网关为单实例 HTTP 模式，`X-Agent-Id` 用于授权解析
- [ ] 越权检索（不在 `serve_refs` 或不在 `agent_grants`）返回明确错误而非空结果
- [ ] 兜底：15 分钟定时 `git rev-parse` 比对
- [ ] 检索结果返回 `git_ref`，可人工追溯

---

## 11. 一句话总结

> **Branch 决定"哪个版本"，Worktree 决定"哪块磁盘"，内嵌知识库跟着两者走。**
> 所以索引不能建在磁盘上（会有 N 份），要建在 Git 对象库上（只有一份，且带 hash）。
> Agent 默认读 `main` 的提交态，读草稿必须显式指定 `working:<task>`；
> 跨 Agent 传契约不走目录，走黑板快照 + 提案合并。
> 这样多个 Worker 能在各自 worktree 里真正并行，而契约始终只有一个真相源。
</content>
