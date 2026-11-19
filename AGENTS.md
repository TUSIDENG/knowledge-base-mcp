# AGENTS.md

本文件是 AI Agent 与贡献者的编码约束。修改代码前先读这里。

## 1. 换行符

- 所有文本文件统一使用 **LF**（`\n`），禁止提交 CRLF。
- 仓库通过 `.gitattributes` 强制 `eol=lf`；Windows 下编辑器请把默认换行设为 LF。
- 提交前自查：`git ls-files --eol` 输出中不应出现 `w/crlf`。

## 2. Python（Google 风格 docstring）

- 每个模块、类、函数、方法都必须有 docstring，不允许空 `pass` 或仅复述函数名的注释。
- 统一使用 **Google 风格**：首行一句话摘要，空行后按需写 `Args` / `Returns` / `Raises` / `Yields` / `Examples`，段落标题以冒号结尾、参数说明缩进对齐。
- 摘要用中文说明「做什么、为什么」；参数名保持英文，与签名一致。
- 公开函数补类型注解；仅内部实现细节时可省略。
- 缩进 4 空格，行宽 120。

```python
def index_kb(ctx: RegistryContext, kb: KnowledgeBase, full: bool = False) -> IndexReport:
    """重建或增量更新指定知识库的向量索引。

    Args:
        ctx: 注册表上下文，提供仓库路径与存储位置。
        kb: 待索引的知识库定义。
        full: 为真时忽略内容哈希全量重建，用于模型或维度变更后。

    Returns:
        本次索引的统计结果，包含增删改数量与错误列表。
    """
```

## 3. TypeScript / JavaScript（JSDoc）

- 前端代码统一使用 **JSDoc**（`/** ... */`），不要用行注释代替接口文档。
- 每个导出的类、函数、方法都必须有 JSDoc；标签按需使用 `@param` / `@returns` / `@throws` / `@template` / `@example`。
- 非导出的内部函数，逻辑不自明时也要注释；注释说明意图与副作用，不复述代码。
- 类的公有属性用 JSDoc 标注类型与含义；复杂对象可用 `@typedef` 描述结构。
- 缩进 2 空格，字符串用单引号，语句结尾带分号（与现有文件保持一致）。

```ts
/**
 * 按前缀检索知识库分块。
 *
 * @param kbId 知识库标识，对应注册表中的 kb_id。
 * @param prefixes 目录前缀，限定检索范围；为空表示全库。
 * @returns 按相关度降序排列的分块列表。
 */
export async function searchChunks(kbId: string, prefixes?: string[]): Promise<Chunk[]> {
  // ...
}
```

## 4. 通用约定

- 注释与文档使用中文，标识符使用英文。
- 不提交本机路径、密钥、真实注册表（`config/kb_registry.yaml` 已忽略）。
- 新增文件必须是 UTF-8 无 BOM、LF 换行。
- 修改行为时同步更新 `README.md` 与 `knowledge/` 下对应文档。
