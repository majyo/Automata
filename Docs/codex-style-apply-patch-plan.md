# Codex-style apply_patch 重构方案

## 背景

当前项目已经实现了真实的工具系统，内置工具在 `api/automata_api/agent/tools/registry.py` 中注册，包括 `read_file`、`write_file`、`rg`、`grep`、`run_bash`、`apply_patch` 和 `apply_patch_preview`。

现有 `apply_patch` 位于：

- `api/automata_api/agent/tools/patch.py`：向 LLM 暴露工具 spec。
- `api/automata_api/agent/tools/_core.py`：实现 unified diff 解析、hunk 校验、文件读取和写入。

现有实现接受 unified diff：

```diff
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 def hello():
-    print("hi")
+    print("hello")
```

这类格式的优点是标准、兼容 Git 和外部 patch 工具；缺点是 LLM 需要生成精确 hunk header、旧/新行数、`/dev/null` 新增删除语义以及每行前缀，容易出现格式细节错误。

Codex-style patch 采用面向 LLM 的编辑 DSL：

```text
*** Begin Patch
*** Update File: foo.py
@@
 def hello():
-    print("hi")
+    print("hello")
*** End Patch
```

它用显式文件操作替代 unified diff 的文件头，用上下文匹配替代行号匹配，更适合模型稳定生成。本方案将本项目 `apply_patch` 重构为 Codex-style 语法，并保留必要的迁移路径。

## 目标

1. 将 LLM 暴露的 `apply_patch` 主语法改为 Codex-style patch。
2. 保持 `apply_patch_preview` 为只读 dry-run 工具，Plan 模式仍然只允许预览。
3. 支持新增、修改、删除和可选移动文件。
4. 不依赖模型生成精确行号。
5. 通过严格上下文匹配避免错配：匹配不唯一或旧内容不一致时失败。
6. 保持现有工具结果 JSON 的基本字段约定：`simulated`、`ok`、`tool`、`dry_run`、`files`、`summary`、`error`。
7. 保留 workspace 路径边界校验，所有文件操作必须限制在当前 workspace 内。
8. 为现有 unified diff 调用提供兼容或迁移策略，避免一次性破坏已有测试和调用方。

## 非目标

- 不实现 Git binary patch 或二进制文件编辑。
- 不做模糊文本编辑、AI 推断式改写或自动格式化。
- 不跨文件系统边界支持 workspace 外路径。
- 不改变 `read_file`、`write_file`、`rg`、`grep`、`run_bash` 的工具语义。
- 不在本次方案中完成 Backend 抽象层重构；但实现应尽量把纯解析/应用逻辑与本地文件系统读写拆开，便于后续迁移。

## 建议语法

### 顶层结构

```text
*** Begin Patch
<file operation>+
*** End Patch
```

顶层要求：

- patch 必须以 `*** Begin Patch` 开始，以 `*** End Patch` 结束。
- 两者之间至少包含一个文件操作。
- 多个文件操作按出现顺序应用。
- 任何无法识别的顶层指令直接报错。

### 新增文件

```text
*** Begin Patch
*** Add File: path/to/file.txt
+first line
+second line
*** End Patch
```

规则：

- `Add File` 的目标文件不能已经存在。
- 内容行必须以 `+` 开头。
- 写入内容为去掉首个 `+` 后的文本。
- 空文件允许，但建议使用显式空操作：

```text
*** Begin Patch
*** Add File: empty.txt
*** End Patch
```

### 删除文件

```text
*** Begin Patch
*** Delete File: path/to/file.txt
*** End Patch
```

规则：

- 文件必须存在且是普通文件。
- 删除操作不需要 hunk。
- 如果希望更谨慎，可以后续扩展为允许携带校验 hunk；本次不强制。

### 修改文件

```text
*** Begin Patch
*** Update File: path/to/file.py
@@
 def hello():
-    print("hi")
+    print("hello")
*** End Patch
```

规则：

- 文件必须存在且是 UTF-8 文本。
- `Update File` 可以包含一个或多个 `@@` hunk。
- hunk 不需要行号。
- hunk 内支持三类行：
  - 空格开头：上下文行。
  - `-` 开头：必须从目标文件中删除的旧行。
  - `+` 开头：要插入的新行。
- 每个 hunk 的旧侧文本由空格行和 `-` 行组成。
- 每个 hunk 的新侧文本由空格行和 `+` 行组成。
- 旧侧文本必须在当前文件内容中唯一匹配；否则失败。
- 匹配成功后用新侧文本替换旧侧文本。

### 可选移动文件

Codex apply_patch 常见语法包含 move：

```text
*** Begin Patch
*** Update File: old/path.txt
*** Move to: new/path.txt
@@
-old
+new
*** End Patch
```

建议本项目分两阶段支持：

- 第一阶段：解析但拒绝 `Move to`，返回清晰错误：`Move to is not supported yet.`
- 第二阶段：实现移动文件，要求源文件存在、目标文件不存在、两者都在 workspace 内；先对源内容应用 hunk，再写入目标并删除源文件。

这样可以先完成主编辑路径，避免移动语义拖慢第一版落地。

## 与 unified diff 的关系

建议将工具能力拆分为：

- `apply_patch`：Codex-style patch，作为 LLM 默认编辑工具。
- `apply_patch_preview`：Codex-style patch dry-run，只验证和总结，不写入。
- `apply_unified_diff`：可选兼容工具，保留现有 unified diff 实现，供用户粘贴 Git diff 或外部 diff 时使用。
- `apply_unified_diff_preview`：可选 dry-run 版本。

如果希望减少工具数量，也可以让 `apply_patch` 在过渡期自动识别：

- 以 `*** Begin Patch` 开头：走 Codex-style parser。
- 包含 `--- ` / `+++ ` 文件头：走 unified diff parser，并在返回结果中标记 `syntax: "unified_diff"`。

但长期建议不要让一个工具承载两套主语义。LLM 的工具描述越单一，调用越稳定。因此推荐最终形态是：`apply_patch` 只接受 Codex-style，unified diff 另起工具名。

## 模型调用成功率设计

参考 `D:\workspace\projects\codex` 的实现，Codex 并不是只把 patch 规则写成自然语言提示，也不是只把 Lark grammar 当普通文本塞进 prompt。它采用的是三层组合：

1. `apply_patch` 是 freeform/custom tool，输入是一整段 patch 字符串，不是 JSON 参数。
2. 工具 spec 中携带 `format.type = "grammar"`、`format.syntax = "lark"` 和完整 grammar definition。
3. 另有自然语言说明、示例和运行时 parser/validator，parser 还会容忍部分模型常见格式偏差。

这说明更稳的方向不是“Lark grammar vs 规则说明”二选一，而是：

- **grammar 作为机器可读约束**：给支持 grammar/custom tool 的模型或 API 使用，减少模型输出非法结构的概率。
- **说明和示例作为模型可读教学材料**：告诉模型什么时候用、怎么写、常见模板是什么。
- **后端 parser 作为最终校验和容错层**：即使模型侧未严格受 grammar 约束，也能给出可恢复的错误。

因此本项目的推荐实现是：

- 如果当前 LLM provider 支持 grammar/custom/freeform tool：优先使用 Codex-style freeform grammar tool。
- 如果当前 provider 只支持 OpenAI-compatible JSON function calling：保留 JSON tool wrapper，但 `patch` 字段内部仍使用 Codex-style patch，并在 tool description 中提供短规则和示例。
- 无论哪种 provider，后端都必须使用同一套 parser/validator 执行，不依赖 prompt 约束保证正确性。

### 推荐的工具暴露形态

理想形态：

```json
{
  "type": "custom",
  "name": "apply_patch",
  "description": "Use the apply_patch tool to edit files. This is a FREEFORM tool, so do not wrap the patch in JSON.",
  "format": {
    "type": "grammar",
    "syntax": "lark",
    "definition": "start: begin_patch hunk+ end_patch\n..."
  }
}
```

兼容形态：

```json
{
  "type": "function",
  "function": {
    "name": "apply_patch",
    "description": "Apply a Codex-style patch. The patch string must start with *** Begin Patch and end with *** End Patch. Use Add File, Update File, or Delete File. Update hunks use @@ and enough context to match uniquely.",
    "parameters": {
      "type": "object",
      "properties": {
        "patch": { "type": "string" },
        "dry_run": { "type": "boolean" },
        "create_dirs": { "type": "boolean" }
      },
      "required": ["patch"]
    }
  }
}
```

本项目当前走的是 function-call 形态，所以第一版可以先实现兼容形态；后续如果接入支持 custom/freeform grammar 的 API，再新增 `AgentTool.spec()` 的 provider-aware 输出分支。

### 推荐的 Lark grammar

可直接对齐 Codex 的核心 grammar：

```lark
start: begin_patch hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

hunk: add_hunk | delete_hunk | update_hunk
add_hunk: "*** Add File: " filename LF add_line+
delete_hunk: "*** Delete File: " filename LF
update_hunk: "*** Update File: " filename LF change_move? change?

filename: /(.+)/
add_line: "+" /(.*)/ LF -> line

change_move: "*** Move to: " filename LF
change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF

%import common.LF
```

如果第一版不支持 `Move to`，不要从 grammar 中删除它再让模型猜；更好的做法是：

- function-call 兼容形态中暂不提 `Move to`。
- parser 可以识别 `Move to` 并返回明确错误：`Move to is not supported yet.`
- 后续实现移动文件时再把说明和测试打开。

## 匹配算法设计

### 核心原则

Codex-style 不使用 hunk 行号，因此必须靠严格文本匹配保证安全：

1. 每个 hunk 都构造旧侧文本 `old_text` 和新侧文本 `new_text`。
2. `old_text` 必须非空，除非该 hunk 是明确的文件首/尾插入扩展语法。
3. 在当前文件内容中查找 `old_text` 的所有精确出现位置。
4. 如果匹配次数为 0，返回上下文不匹配错误。
5. 如果匹配次数大于 1，返回上下文不唯一错误，要求提供更多上下文。
6. 如果匹配唯一，则替换为 `new_text`。
7. 同一文件多个 hunk 按 patch 顺序应用；后续 hunk 在前序 hunk 应用后的内容上匹配。

### 行尾处理

当前工具按 UTF-8 文本处理。Codex-style 实现建议：

- 读取时保留原始换行：`splitlines(keepends=True)` 或直接字符串匹配。
- patch 文本统一归一化 `\r\n` 和 `\r` 为 `\n`。
- 写入时沿用当前实现的 `write_text(..., newline="")`。
- hunk 行内容保留换行符，避免无意合并行。
- 对 `\ No newline at end of file` 第一版可以拒绝，后续再支持。

### 插入-only hunk

纯插入 hunk 容易错配，因为没有删除行。建议第一版仍要求至少一行上下文：

```text
@@
 context line
+inserted line
```

此时 `old_text` 为 `context line`，`new_text` 为 `context line + inserted line`，仍然可以唯一匹配。

如果 hunk 完全没有上下文和删除行：

```text
@@
+inserted line
```

第一版应拒绝，错误为：`Hunk must include at least one context or deletion line.`

### 多处相同上下文

如果旧侧文本出现多次，不要默认选择第一处。应返回：

```json
{
  "ok": false,
  "error": "Hunk context is not unique for path/to/file.py. Add more surrounding context."
}
```

这比 silent wrong edit 更重要。LLM 收到错误后可以读取文件并生成更具体上下文。

## 模块拆分建议

当前 `_core.py` 同时包含搜索、bash、文件读写、unified diff patch 等逻辑。为降低重构风险，建议先新增独立模块承载 Codex-style 解析和纯文本应用：

```text
api/automata_api/agent/tools/
  patch.py                  工具 spec，保持薄封装
  patch_codex.py            新增：Codex-style parser + planner + text apply
  patch_unified.py          可选：从 _core.py 迁出 unified diff 实现
  _core.py                  过渡期保留 run_apply_patch 入口
```

第一阶段可以只新增 `patch_codex.py`，并让 `_core.py::run_apply_patch()` 调用它。等测试稳定后，再决定是否把 unified diff 逻辑迁出。

建议核心数据结构：

```python
@dataclass(frozen=True)
class CodexPatch:
    operations: list[CodexFileOperation]

@dataclass(frozen=True)
class CodexFileOperation:
    kind: Literal["add", "update", "delete", "move"]
    path: str
    new_path: str | None
    hunks: list[CodexHunk]
    added_lines: list[str]

@dataclass(frozen=True)
class CodexHunkLine:
    kind: Literal["context", "remove", "add"]
    content: str

@dataclass(frozen=True)
class CodexHunk:
    lines: list[CodexHunkLine]
```

## 工具 spec 更新

`api/automata_api/agent/tools/patch.py` 中 `ApplyPatchTool` 描述更新为：

- 接受 Codex-style patch。
- 必须包含 `*** Begin Patch` 和 `*** End Patch`。
- 支持 `Add File`、`Update File`、`Delete File`。
- 建议 dry-run 后再真实应用。

参数保持不变：

```json
{
  "patch": "string",
  "dry_run": "boolean",
  "create_dirs": "boolean"
}
```

`apply_patch_preview` 参数保持只有 `patch`，内部强制 `dry_run=true`。

## 系统提示词更新

`api/automata_api/agent/prompts.py` 中工具说明需要从 unified diff 改为 Codex-style：

```text
Use apply_patch for targeted code edits with Codex-style patches. A patch must
start with *** Begin Patch and end with *** End Patch. Use *** Add File,
*** Update File, and *** Delete File file operations. Update hunks use @@
without line numbers and must include enough surrounding context to match
uniquely. When practical, call apply_patch with dry_run=true before applying
changes with dry_run=false.
```

Plan 模式提示也应说明 `apply_patch_preview` 是 Codex-style dry-run。

## 结果 JSON 设计

成功结果保持现有形态，增加 `syntax` 字段：

```json
{
  "simulated": false,
  "ok": true,
  "tool": "apply_patch",
  "syntax": "codex_patch",
  "dry_run": false,
  "files": [
    {
      "path": "foo.py",
      "status": "modified",
      "hunks": 1,
      "old_lines": 10,
      "new_lines": 10
    }
  ],
  "summary": {
    "added": 0,
    "modified": 1,
    "deleted": 0,
    "moved": 0,
    "hunks": 1
  }
}
```

失败结果保持兼容：

```json
{
  "simulated": false,
  "ok": false,
  "tool": "apply_patch",
  "syntax": "codex_patch",
  "dry_run": true,
  "path": "foo.py",
  "error": "Hunk context is not unique for foo.py. Add more surrounding context."
}
```

## 分步骤开发计划

### S1. 补充当前行为基线测试

- 阅读并确认 `api/tests/test_tools.py` 中现有 `apply_patch` 覆盖范围。
- 增加一组测试固定当前 Plan 模式行为：Plan 模式只暴露 `apply_patch_preview`，阻止 `apply_patch`。
- 增加一组测试固定工具结果 JSON 的关键字段，避免迁移时破坏前端展示。

验收标准：

- 现有测试全部通过。
- 新增测试在当前实现下通过或以明确方式标记为迁移前基线。

### S2. 新增 Codex-style parser

新增 `api/automata_api/agent/tools/patch_codex.py`：

- 实现 `parse_codex_patch(patch: str) -> tuple[CodexPatch | None, str | None]`。
- 支持 `Begin Patch` / `End Patch`。
- 支持 `Add File` / `Update File` / `Delete File`。
- 第一版解析但拒绝 `Move to`。
- 校验路径必须是 workspace-relative 语义，禁止绝对路径、盘符路径、`.`、`..` 和空路径。
- parser 可比 grammar 略宽容，例如允许 patch 首尾空白、错误信息指向具体行号；但实际写入前仍必须完成严格语义校验。

测试用例：

- 解析单文件 update。
- 解析多文件 patch。
- 解析 add/delete。
- 拒绝缺失 Begin/End。
- 拒绝未知指令。
- 拒绝路径逃逸。
- 拒绝 malformed hunk 行。

### S3. 实现纯文本 hunk 应用

在 `patch_codex.py` 中实现：

- `apply_codex_hunks(original_content, hunks, relative_path) -> tuple[str, str | None]`。
- 对每个 hunk 构造 `old_text` 和 `new_text`。
- 要求 `old_text` 非空。
- 精确查找所有匹配位置。
- 0 次匹配失败。
- 多次匹配失败。
- 唯一匹配后替换。
- 多 hunk 按顺序作用于更新后的内容。

测试用例：

- 修改唯一上下文成功。
- 插入带上下文成功。
- 删除行成功。
- 多 hunk 顺序应用成功。
- 上下文不存在失败且不写文件。
- 上下文重复失败且不写文件。
- 空 old_text 失败。

### S4. 接入文件规划与 dry-run

新增或改造 planner：

- `plan_codex_patch_file(operation, workspace_path)`。
- 复用现有 `resolve_file_path()` 做 workspace 边界校验。
- `Add File`：目标不存在，生成新内容。
- `Update File`：目标存在且是 UTF-8 文件，应用 hunk。
- `Delete File`：目标存在且是文件，规划删除。
- dry-run 只产出 planned changes 和 summary，不写文件。

验收标准：

- `apply_patch_preview` 对 Codex-style patch 可返回 summary。
- dry-run 不改变文件内容。
- 所有错误路径都不会产生部分写入。

### S5. 切换 `apply_patch` 主入口

改造 `_core.py::run_apply_patch()`：

- 默认要求 Codex-style patch。
- 解析失败返回 Codex-style 错误。
- 可选过渡兼容：检测 unified diff 时调用旧 parser，并返回 `syntax: "unified_diff"`。
- 非 dry-run 时按 planned changes 写入文件。
- 保持 `create_dirs` 行为。

建议过渡策略：

1. 第一版自动识别 unified diff，避免打断已有测试。
2. 测试和文档都迁移到 Codex-style。
3. 后续单独 PR 把 unified diff 迁到 `apply_unified_diff`。

### S6. 更新工具描述和系统提示词

- 更新 `patch.py` 的 `ApplyPatchTool.spec()`。
- 更新 `ApplyPatchPreviewTool.spec()`。
- 更新 `prompts.py` 的执行模式提示。
- 更新 Plan 模式提示，说明 preview 使用 Codex-style。
- 更新 `api/README.md` 工具说明。
- 如果后续 provider 支持 custom/freeform grammar tool，新增 `FreeformAgentTool` 或 provider-aware spec 分支，把 Lark grammar 作为 `format` 字段发送，而不是只作为普通 prompt 文本。

验收标准：

- LLM 看到的工具 spec 不再要求 unified diff。
- README 明确列出 Codex-style patch 示例。

### S7. 迁移测试

迁移 `api/tests/test_tools.py` 中 patch 测试：

- `test_apply_patch_dry_run_modify_leaves_file_unchanged`
- `test_apply_patch_apply_modify_changes_expected_content`
- `test_apply_patch_add_file_creates_text_file`
- `test_apply_patch_delete_file_removes_target`
- `test_apply_patch_multiple_files_apply_atomically`
- `test_apply_patch_context_mismatch_fails_without_writing`
- `test_apply_patch_rejects_path_escape`
- `test_apply_patch_rejects_malformed_patch`
- `test_apply_patch_preview_is_real_dry_run_alias`

新增测试：

- `test_apply_patch_rejects_ambiguous_hunk_context`
- `test_apply_patch_rejects_insert_only_hunk_without_context`
- `test_apply_patch_accepts_multiple_update_hunks`
- `test_apply_patch_rejects_move_to_initially`

验收标准：

- `uv run --directory api --group dev --locked pytest api/tests/test_tools.py`
- `uv run --directory api --group dev --locked pytest api/tests/test_agent_plan_mode_unit.py`
- 全量 `pytest` 通过。

### S8. 可选拆分 unified diff 兼容工具

如果确认仍需要 Git diff 兼容：

- 新增 `ApplyUnifiedDiffTool`，名称 `apply_unified_diff`。
- 新增 `ApplyUnifiedDiffPreviewTool`，名称 `apply_unified_diff_preview`。
- 将现有 unified diff parser 从 `_core.py` 迁到 `patch_unified.py`。
- 在 registry 中注册两个兼容工具。
- Plan 模式是否允许 `apply_unified_diff_preview` 由产品决策决定；如果允许，需要加入 `PLAN_TOOL_NAMES`。

如果不需要兼容：

- 删除 unified diff parser。
- 删除相关旧测试。
- README 中说明不支持 unified diff，用户应使用 Codex-style patch。

## 推荐实现顺序

1. `patch_codex.py` 纯解析与纯文本应用。
2. 单元测试覆盖 parser 和 hunk matching。
3. `_core.py::run_apply_patch()` 接入 Codex-style planner。
4. 工具 spec 和 prompt 更新。
5. 迁移现有 patch 测试。
6. 决定是否保留 unified diff 兼容工具。

## 风险与应对

### 风险：上下文错配

应对：唯一匹配强约束。匹配 0 次或多次都失败，不自动选择第一处。

### 风险：LLM 生成过少上下文

应对：错误信息明确要求增加 surrounding context。Plan/Act prompt 中强调 hunk 必须包含足够上下文。

### 风险：多 hunk 顺序冲突

应对：按顺序应用到更新后的内容；后续 hunk 若匹配失败则整个 patch 失败。第一版在写入前完成所有文件 planning，保证失败不产生部分写入。

### 风险：迁移破坏外部 unified diff 使用

应对：过渡期自动识别 unified diff，或新增 `apply_unified_diff` 兼容工具。

### 风险：文件行尾变化

应对：第一版遵循现有写入方式；增加覆盖 CRLF 文件的测试。如果发现行尾保持很重要，再引入按文件原行尾风格写回的策略。

## 最终形态建议

长期建议工具集为：

- `read_file`
- `write_file`
- `apply_patch`：Codex-style，LLM 默认编辑工具。
- `apply_patch_preview`：Codex-style dry-run，Plan 模式允许。
- `apply_unified_diff`：可选，人类粘贴 Git diff 时使用。
- `apply_unified_diff_preview`：可选，Plan 模式按需允许。

这样既能让 LLM 使用最稳定的编辑 DSL，也保留与标准 diff 生态互通的能力。
