---
title: Agent MCP 接入配置（Cursor / Trae / Codex / OpenCode / Claude Code）
doc_type: guide
status: active
domain: [kb-gateway, mcp]
systems: [cursor, trae, codex, opencode, claude-code]
owner: david
version: 1.1
---

# Agent MCP 接入配置

本文给出 `kb-gateway` 以 **HTTP MCP 服务**常驻运行（PowerShell 窗口启动一次，客户端通过 URL 连接）时，在五种客户端里的配置方式，并区分**单 Agent 直连**与**调度 Agent + 子代理**两种用法。所有示例使用本仓库的绝对路径；其他项目把路径和 `kb_id` 换成自己的即可。

## 0. 启动 MCP 服务（HTTP 模式）

本项目**不由客户端拉起进程**，而是在独立 PowerShell 窗口里常驻一个 MCP 服务，客户端通过 URL 连接。传输协议用 `streamable-http`：

```powershell
# 打开一个 PowerShell 窗口，保持运行；关闭窗口即停止服务
D:\code\knowledge-base-mcp\.venv\Scripts\python.exe -m kb_gateway.cli serve `
  --config D:\code\knowledge-base-mcp\config\kb_registry.yaml `
  --transport streamable-http --host 127.0.0.1 --port 8787
```

连接信息：

| 项 | 值 |
|---|---|
| MCP 端点 | `http://127.0.0.1:8787/mcp` |
| 传输 | Streamable HTTP（POST + SSE 响应） |
| 监听范围 | 仅本机回环，不对外暴露 |

启动成功的标志是窗口里出现 `Uvicorn running on http://127.0.0.1:8787`。另开一个窗口做自检：

```powershell
D:\code\knowledge-base-mcp\.venv\Scripts\python.exe -m kb_gateway.cli list --config D:\code\knowledge-base-mcp\config\kb_registry.yaml
```

`status: ready` 且 `doc_count > 0` 才算就绪；未索引则先执行：

```powershell
D:\code\knowledge-base-mcp\.venv\Scripts\python.exe -m kb_gateway.cli reindex --config D:\code\knowledge-base-mcp\config\kb_registry.yaml --kb knowledge-base-mcp --full
```

环境变量：

| 变量 | 作用 |
|---|---|
| `KB_CONFIG` | 注册表路径；命令行未传 `--config` 时使用 |
| `KB_EMBEDDING_KEY` | online 向量模型密钥（`secret_ref: env:KB_EMBEDDING_KEY`） |
| `NO_PROXY` | 必设 `127.0.0.1,localhost`；否则客户端可能把本机请求走代理，表现为 502 |

> 备用：把 `--transport streamable-http` 换成 `--transport sse` 时端点为 `http://127.0.0.1:8787/sse`；换成 `stdio` 则由客户端通过 `command` 拉起进程（旧方式，多客户端会各起一份，不推荐）。

## 1. Cursor

配置位置（二选一，项目级优先）：

- 项目级：`<repo>/.cursor/mcp.json`
- 全局：`~/.cursor/mcp.json`

本仓库已写入 [.cursor/mcp.json](../../.cursor/mcp.json)，内容：

```json
{
  "mcpServers": {
    "kb-gateway": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

步骤：

1. 先确认第 0 节的 PowerShell 窗口仍在运行。
2. Cursor 设置 → MCP，`kb-gateway` 应显示为已连接（绿色）；首次新增后执行一次 `Developer: Reload Window`。
3. 对话里让它调用 `kb_list` 验证。

注意：

- 连不上时先看窗口日志；`502` 基本都是代理导致，设置 `NO_PROXY=127.0.0.1,localhost` 后重开 Cursor。
- 服务是全局单例，多个 Cursor 窗口共用同一份索引，不会各起一份进程。
- Cursor 没有原生子代理派发，多任务并行靠多个窗口 + 多个 worktree，由人做调度。

## 2. Trae

配置位置：

- 项目级：`<repo>/.trae/mcp.json`
- 或在设置面板的 MCP 页签中手动添加（字段与下面一致）

本仓库没有自动写入该文件，避免覆盖个人配置；需要时创建：

```json
{
  "mcpServers": {
    "kb-gateway": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

保存后在 MCP 面板刷新，确认工具列表出现 `kb_init_guide` / `kb_list` / `kb_search` / `kb_read`。若该版本面板只接受命令式配置，退回 stdio（`command` 指 venv python，`args` 为 `["-m","kb_gateway.cli","serve","--config","..."]`），两者工具集完全一致。

## 3. Codex CLI

Codex 的 MCP 配置在全局 `~/.codex/config.toml`：

```toml
[mcp_servers.kb-gateway]
url = "http://127.0.0.1:8787/mcp"
startup_timeout_sec = 20
```

步骤：

1. 写入配置后执行 `codex mcp list`（旧版本无此命令则直接重启会话）。
2. 新开一个 Codex 会话，让它先调用 `kb_list`。

注意：

- 旧版本 Codex 若不支持 `url`，改用 `command` / `args` 的 stdio 写法；但那样每个会话都会拉起一份独立进程。
- Codex 没有原生子代理派发，**并行只能靠多个会话 + 每个任务一个 worktree**。
- 想让某个仓库使用独立知识库时不要改客户端，改为另起一个端口的服务（`--port 8788` 配另一份 `--config`），客户端里换成对应 URL。

## 4. OpenCode

配置位置（项目级优先）：

- 项目级：`<repo>/opencode.json` 或 `opencode.jsonc`
- 全局：`~/.config/opencode/opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "kb-gateway": {
      "type": "remote",
      "url": "http://127.0.0.1:8787/mcp",
      "enabled": true
    }
  }
}
```

验证：`opencode` 启动后询问“有哪些 MCP 工具”，应能看到四个 `kb_*` 工具。

关于子代理：OpenCode 的 primary agent 与 `mode: subagent` 的子代理共享同一份 MCP 配置（项目级 `opencode.json` 会被子会话继承），因此**调度模式下把 MCP 配在项目级**，不要只配在个人全局。HTTP 方式下所有子会话连的是同一个服务端点，不存在重复拉进程的问题。

## 5. Claude Code

推荐用命令写入项目级 `.mcp.json`：

```powershell
claude mcp add --scope project --transport http kb-gateway http://127.0.0.1:8787/mcp
```

等价的 `.mcp.json`：

```json
{
  "mcpServers": {
    "kb-gateway": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

验证：`claude` 会话内执行 `/mcp`，确认 `kb-gateway` 连接成功；工具会以 `mcp__kb-gateway__kb_search` 这样的全名出现。

## 6. 单 Agent 直连

适用：Cursor、Trae、Codex、OpenCode、Claude Code 均可。

流程固定为三步，不要让 Agent 直接读磁盘：

1. `kb_list` 拿 `kb_id`、`doc_count`、可检索分区；
2. `kb_search(kb_id, query, prefixes=["spec"], top_k=5)` 窄范围检索；
3. `kb_read(doc_id=...)` 取原文，回答里引用 `path` 与 `heading`。

可直接粘贴的提示词：

```text
先用 kb_list 确认 knowledge-base-mcp 已就绪。
然后用 kb_search(kb_id="knowledge-base-mcp", prefixes=["spec"]) 检索与问题最相关的 3 个分块，
再用 kb_read 读取原文。回答末尾列出引用文件的 path、heading 和 git_ref。
不要修改知识库文件。
```

边界：

- P0 只有只读工具，没有写入 / 提案工具，Agent 不会改库。
- `prefixes` 必须是实际存在的分区（`kb_list(include_prefixes=true)` 可查），否则会报错。
- 结果里的 `git_ref` 是溯源依据；内嵌库为 `master@<sha>`，未提交改动不在索引里。

## 7. 调度 Agent + 子代理

适用：OpenCode、Claude Code（原生 subagent）。Cursor / Trae / Codex 没有原生派发，退回第 6 节或人工多开。

### 7.1 配置要点

1. **MCP 配在项目级**，子代理才会继承：OpenCode 用 `opencode.json`，Claude Code 用 `.mcp.json`。
2. **每个子任务在提示词里写明授权范围**：`kb_id`、`prefixes`、工作目录。MCP 服务本身不区分调用者，靠提示词约束检索范围。
3. **每个子任务一个 worktree**（P0 用原生命令；`kb-gateway worktree` 尚未实现）：

   ```powershell
   git worktree add .worktrees/w-t1 -b agent/t1
   ```

4. 并发建议 3~4，超过后模型调度质量下降。

### 7.2 Claude Code 子代理

`.claude/agents/kb-researcher.md`：

```markdown
---
name: kb-researcher
description: 只在指定知识库分区内检索并给出带溯源的结论
tools: mcp__kb-gateway__kb_list, mcp__kb-gateway__kb_search, mcp__kb-gateway__kb_read
model: sonnet
---

你在 `D:\code\knowledge-base-mcp\.worktrees\w-<task>` 下工作。
只允许检索 kb_id=knowledge-base-mcp、prefixes=["spec"] 的范围。
每次回答必须给出 path、heading、git_ref；找不到就明确说没有，不要编造。
```

主代理派发时说清楚“用 kb-researcher 子代理处理 T1”，子代理会带着这四个 MCP 工具运行。

### 7.3 OpenCode 子代理

`opencode.json` 增加：

```json
{
  "agents": {
    "kb-researcher": {
      "description": "只在指定分区检索知识库并输出带溯源的结论",
      "mode": "subagent",
      "system": "只检索 kb_id=knowledge-base-mcp、prefixes=[\"spec\"]；回答必须带 path、heading、git_ref。",
      "permissions": [
        { "action": "edit", "resource": "*", "effect": "deny" },
        { "action": "subagent", "resource": "*", "effect": "deny" }
      ]
    }
  }
}
```

主代理用 `subagent` 工具启动它。子代理继承项目级 `mcp` 配置，但不要再往下派生（权限已禁止）。

### 7.4 结果汇总

- 每个子代理只提交自己 worktree 的 diff；
- 调度 Agent 按任务依赖顺序做三方合并，冲突回退给对应子代理；
- 提案与写回能力属于 P1+，P0 阶段检索结论直接回给调度会话。

## 8. 排错清单

| 现象 | 原因 | 处理 |
|---|---|---|
| 客户端连不上、报 502 | 本机请求被系统代理拦截 | 设置 `NO_PROXY=127.0.0.1,localhost` 后重启客户端与服务 |
| 客户端连不上、连接被拒绝 | PowerShell 窗口已关闭或端口不对 | 重新启动第 0 节命令，核对 `url` 端口 |
| 端口被占用 `WinError 10048` | 已有实例在跑 | 复用已有实例，或换端口并同步改客户端 `url` |
| 客户端显示 server 启动失败 | 仍在用 stdio 的 `command` 配法且路径写了相对路径 | 改用 HTTP `url`，或把路径改成绝对路径 |
| `ModuleNotFoundError: kb_gateway` | venv 未安装本项目 | 在仓库根执行 `uv sync` |
| `注册表不存在` | `--config` 指错 | 用绝对路径指向 `config/kb_registry.yaml` |
| `知识库尚未索引` | 未执行 reindex 或注册表换了 storage | 重新 `reindex --full` |
| `git rev-parse main 失败` | 仓库默认分支不是 main | 注册表里 `index_ref` 改成 `master`（本仓库即为 master） |
| `doc_count` 一直是旧值 | 内嵌库只索引已提交内容 | 先 `git commit`，再 `reindex` |
| 向量维度报错 | 换了 embedding profile | `reindex --full` 重建 |
| 工具列表为空 | 客户端未刷新 / 配置未保存 | 重载窗口或重启客户端 |

## 9. 安全边界

- 不把含 `secret_ref` 解析值的注册表提交到仓库；密钥只放环境变量。
- 服务默认只监听 `127.0.0.1`，不要改成 `0.0.0.0` 后暴露到公网；本服务没有认证。
- 每个项目使用独立注册表与独立 `.kb-data/`，避免跨项目串库；多库场景用「多端口 + 多注册表」隔离。
- 调度模式下子代理只应有只读工具权限；P0 本身没有写入工具。
