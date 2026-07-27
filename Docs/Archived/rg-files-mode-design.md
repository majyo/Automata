# Automata `rg mode="files"` 文件枚举设计

## 文档状态

- 状态：Implemented
- 设计范围：后端 Agent 工具协议与本地 Backend
- 不包含：前端 UI、数据库迁移、任意 ripgrep 参数透传
- 目标实现位置：`api/automata_api/agent/`

## 实现结果

- 实现日期：2026-07-27
- `rg` 已支持兼容的 `mode="files"`，旧文本搜索调用保持不变。
- 已实现 `rg -> Git -> filesystem` 回退、工作区路径限制、不跟随符号链接、
  glob/depth/limit/字符预算和紧凑返回协议。
- files mode 保持 read-only，在 Act 和 Plan mode 中免审批。
- 已加入 `rg.files`、`operation_mode`、engine、file count、truncated 和
  degraded 等低基数 observability 属性。
- 自动化验证结果：后端完整测试 `294 passed, 1 skipped`；跳过项是当前
  Windows 环境不允许创建测试符号链接。
- 当前仓库 smoke：原生 rg engine、25 条结果、1,584 字符返回体、正确标记
  `file_limit`，且不包含重复 `stdout`/`output`。

## 1. 背景

Automata 当前向模型暴露了名为 `rg` 的只读工具，但它的真实语义是“按
pattern 搜索文件内容”，不是完整的 ripgrep 命令接口：

- `RgTool` 和 `GrepTool` 共用一份要求 `pattern` 的参数结构。
- `Backend.search()` 必须接收 `pattern`。
- 本地 Backend 将调用固定构造成
  `rg --line-number --color never -- <pattern> <path>`。
- `rg` 不可用时，现有实现会退化到 `grep` 或 Bash 中的文本搜索。

因此，模型需要查看项目文件树时，不能通过 `rg` 工具表达 `rg --files`，
只能退回 `exec_command` 执行 `find`、`ls` 或原始 `rg --files`。这些命令
会进入 command 风险策略并等待用户审批。

2026-07-27 的一次项目检查任务验证了该问题：

- Agent 在 6 个模型步骤中调用了 19 次工具，但没有进入写文档和最终回复。
- 11 次 `exec_command` 全部等待审批，累计等待约 19.24 秒。
- 多次宽泛的 `find` 输出共向模型上下文加入约 13.4 万字符。
- 两个 20,000 字符的 stdout 同时以 `stdout` 和 `output` 返回，单次工具
  结果膨胀到约 41,000 字符。
- 第一次和第六次模型请求分别包含约 2,435 和 61,345 prompt tokens。

这说明提高 Agent step 上限只能避免过早失败，不能解决文件枚举缺口、审批
等待和上下文膨胀。

## 2. 设计结论

保留现有工具名 `rg`，新增受控的 `mode="files"`：

```json
{
  "mode": "files",
  "path": ".",
  "include_globs": ["*.py"],
  "exclude_globs": ["api/.venv/**"],
  "hidden": false,
  "max_depth": 6,
  "limit": 500
}
```

后端只根据经过校验的结构化字段构造命令，不接受 `raw_args`、原始命令
字符串或额外位置参数。

`rg --files` 本身保持只读；禁止任意参数透传，是为了阻止 `--pre=COMMAND`、
`--follow`、额外工作区外路径以及无约束的 `--hidden --no-ignore` 等能力突破
当前只读工具边界。

## 3. 目标

1. 让模型使用现有 `rg` 工具完成受控、免审批的工作区文件枚举。
2. 保持所有现有 `rg {"pattern": ...}` 调用兼容。
3. 保持 `RgTool.read_only=True`，使文件枚举在 Act 和 Plan mode 中均可用。
4. 所有搜索根目录必须位于当前 workspace 内。
5. 不执行 shell，不允许模型提供 ripgrep 原始参数。
6. rg/Git 主路径默认遵守 ignore 规则；所有路径均不跟随符号链接，且默认不
   枚举隐藏文件。纯文件系统回退必须显式报告 ignore 语义降级。
7. 对文件数量、结果字符数、遍历深度和执行时间进行硬限制。
8. 返回适合模型消费的紧凑结构，不重复保存相同输出。
9. `rg` 缺失时仍有安全、行为可解释的只读回退。
10. 提供足以验证审批消除、结果大小和枚举耗时的 observability 数据。

## 4. 非目标

本方案不实现：

- 完整 ripgrep CLI 的结构化映射。
- `raw_args` 或 shell 字符串透传。
- `--pre`、`--follow`、`--no-ignore`、`-uuu` 或二进制文件扫描。
- 文件内容搜索结果的重构；`mode="search"` 保持现有输出协议。
- 文件内容、大小、时间戳、Git 状态或 hash 的批量读取。
- 目录节点列表；`files` mode 只返回文件路径。
- 稳定快照或游标分页。MVP 截断后要求模型缩小 `path` 或 glob。
- 远程 Backend、MCP resource 枚举或通用虚拟文件系统。
- 前端文件浏览器。

## 5. 工具协议

### 5.1 模式

`rg` 支持两个模式：

| mode | 含义 | `pattern` |
|---|---|---|
| `search` | 现有文本内容搜索 | 必填 |
| `files` | 新增文件路径枚举 | 禁止 |

兼容规则：

- 未提供 `mode` 时按 `search` 处理。
- 现有 `{"pattern": "needle", "path": "."}` 行为完全不变。
- 显式传入未知 mode 返回稳定参数错误。
- `mode="search"` 缺少 pattern 时返回 `missing_pattern`。
- `mode="files"` 携带非空 pattern 时返回
  `pattern_not_allowed_in_files_mode`，不静默忽略。
- `grep` 仍然只有文本搜索模式，不增加 `mode="files"`。

当前 `RgTool` 和 `GrepTool` 共用 `search_parameters()`。实现时必须拆成
`rg_parameters()` 和 `text_search_parameters()`，避免把 files mode 错误地
暴露给 `grep`。

### 5.2 参数结构

为了兼容不同 Chat Completions provider 对 JSON Schema 组合关键字的支持
程度，工具 spec 使用平铺属性，不依赖 `oneOf`、`if/then` 来表达条件必填。
条件约束由 `RgTool.run()` 进行第二层运行时校验。

建议 spec：

```json
{
  "type": "object",
  "properties": {
    "mode": {
      "type": "string",
      "enum": ["search", "files"],
      "description": "Defaults to search. Use files to list workspace files."
    },
    "pattern": {
      "type": "string",
      "description": "Required in search mode and forbidden in files mode."
    },
    "path": {
      "type": "string",
      "description": "Workspace-relative search root. Defaults to '.'."
    },
    "cwd": {
      "type": "string",
      "description": "Workspace-relative working directory. Defaults to workspace root."
    },
    "timeout_seconds": {
      "type": "number",
      "description": "Defaults to 30 and is capped at 120."
    },
    "include_globs": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 32,
      "description": "Files mode only. Include files matching any glob."
    },
    "exclude_globs": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 32,
      "description": "Files mode only. Exclude files matching any glob."
    },
    "hidden": {
      "type": "boolean",
      "description": "Files mode only. Defaults to false."
    },
    "max_depth": {
      "type": "integer",
      "minimum": 0,
      "maximum": 64,
      "description": "Files mode only. Optional traversal depth relative to path."
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 2000,
      "description": "Files mode only. Defaults to 500."
    }
  }
}
```

`pattern` 不再放入顶层 `required`，否则 `mode="files"` 无法通过工具协议。
运行时校验承担 search mode 的 pattern 必填约束。

### 5.3 参数限制

| 参数 | 默认值 | 硬上限 | 规则 |
|---|---:|---:|---|
| `path` | `.` | 1 个根目录 | 必须 resolve 到 workspace 内 |
| `cwd` | workspace 根 | 1 个目录 | 必须位于 workspace 内 |
| `timeout_seconds` | 30 | 120 | 沿用现有限制 |
| `include_globs` | `[]` | 32 项 | 每项最多 256 字符 |
| `exclude_globs` | `[]` | 32 项 | 每项最多 256 字符 |
| `hidden` | `false` | 不适用 | 不提供 `no-ignore` |
| `max_depth` | 不限制 | 64 | `0` 只检查根路径本身 |
| `limit` | 500 | 2,000 | 同时受字符预算约束 |
| 结果 JSON | 不适用 | 20,000 字符 | 超限设置 `truncated=true` |

glob 作为 `--glob` 的独立参数值传给进程，不拼接 shell 字符串。拒绝：

- NUL 字符。
- 空字符串。
- 单项超过 256 字符。
- 总项数超过 32。

`exclude_globs` 由后端转换为 ripgrep 的否定 glob；模型不需要自行拼接 `!`。

## 6. 返回协议

### 6.1 成功

```json
{
  "simulated": false,
  "ok": true,
  "tool": "rg",
  "mode": "files",
  "engine": "rg",
  "path": ".",
  "cwd": "D:/workspace/projects/automata",
  "files": [
    "api/automata_api/main.py",
    "api/automata_api/config.py"
  ],
  "count": 2,
  "truncated": false,
  "truncation_reason": null,
  "limit": 500,
  "max_result_chars": 20000,
  "ignore_semantics": "ripgrep",
  "degraded": false,
  "attempts": [
    {"engine": "rg", "ok": true}
  ]
}
```

设计要求：

- `files` 是相对 `cwd` 的 POSIX 风格路径字符串数组。
- 不返回对象数组，避免为每个路径重复 `{"path": ...}`。
- 路径按规范化后的字典序排序，使测试和模型行为稳定。
- files mode 不返回重复的 `stdout`、`output` 或完整原始进程响应。
- 空目录是成功：`ok=true`、`files=[]`、`count=0`。
- `count` 是实际返回数量，不是完整工作区文件总数。

### 6.2 截断

当结果达到文件数量或字符预算时：

```json
{
  "ok": true,
  "mode": "files",
  "files": ["..."],
  "count": 500,
  "truncated": true,
  "truncation_reason": "file_limit",
  "hint": "Narrow path or include_globs and call rg again."
}
```

`truncation_reason` 取值：

- `file_limit`
- `character_limit`
- `timeout`

MVP 不提供 offset 或 cursor。文件树在两次调用之间可能变化，伪稳定分页容易
让模型误以为拿到了快照。Agent 应优先按子目录或 glob 缩小范围。

### 6.3 失败

错误响应保留现有 `simulated=false` 和 `success=False`，并新增稳定 `error`
字段：

```json
{
  "simulated": false,
  "ok": false,
  "tool": "rg",
  "mode": "files",
  "error": "path_outside_workspace",
  "message": "path must stay inside workspace",
  "files": [],
  "count": 0,
  "truncated": false
}
```

稳定错误码：

| error | 场景 |
|---|---|
| `invalid_mode` | mode 不是 `search` 或 `files` |
| `missing_pattern` | search mode 缺少 pattern |
| `pattern_not_allowed_in_files_mode` | files mode 传入 pattern |
| `invalid_glob` | glob 类型、长度或内容非法 |
| `invalid_limit` | limit 不可转换或超出范围 |
| `path_outside_workspace` | path/cwd 越出 workspace |
| `path_not_found` | 枚举根不存在 |
| `path_not_directory` | files mode 的根路径不是目录 |
| `enumeration_timed_out` | 达到 timeout |
| `enumeration_failed` | 所有 engine 均失败 |

为了兼容现有日志和调用方，错误结果仍可保留 `stderr=message`，但不返回重复
的空 stdout/output。

## 7. 后端设计

### 7.1 Backend 原语

工具名保持 `rg`，但 Backend 层新增与具体 CLI 名称无关的文件枚举原语：

```python
@dataclass(frozen=True)
class FileListResult:
    ok: bool
    engine: str
    path: str
    cwd: str
    files: tuple[str, ...]
    truncated: bool
    truncation_reason: str | None
    ignore_semantics: str
    degraded: bool
    timed_out: bool
    attempts: list[dict[str, Any]]


class Backend(ABC):
    @abstractmethod
    async def list_files(
        self,
        *,
        path: str | None,
        cwd: str | None,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        hidden: bool,
        max_depth: int | None,
        limit: int,
        max_result_chars: int,
        timeout_seconds: float,
    ) -> FileListResult:
        ...
```

这样做的原因：

- `RgTool` 负责协议分派和参数校验。
- `Backend` 负责工作区路径边界和平台执行。
- `LocalBackend` 负责 `rg`、Git 和文件系统回退。
- `WindowsBackend` 继承同一实现，不需要单独拼 PowerShell。
- 未来 Backend 可以实现自己的只读枚举，而不必伪装成 shell。

### 7.2 主路径：ripgrep

后端自行构造 argv：

```text
rg
  --files
  --sort path
  [--hidden]
  [--max-depth N]
  [--glob INCLUDE]...
  [--glob !EXCLUDE]...
  --
  <validated-relative-path>
```

约束：

- 使用 `asyncio.create_subprocess_exec()`，不使用 shell。
- `--` 之后只能出现一个由后端生成且验证过的相对根路径。
- 不接受任何来自模型的 option 名称。
- 不加入 `--follow`，因此不遍历符号链接目标。
- 不加入 `--no-ignore`，默认遵守 ripgrep ignore 语义。
- 输出统一转换为 `/` 分隔符。
- 对输出读取实施行数和字符双重预算。
- 读取到 `limit + 1` 项或字符预算后即可确认截断，并终止进程树，避免继续
  扫描巨大目录。
- 进程退出与超时复用现有 process supervisor 和进程树终止机制。

### 7.3 回退顺序

files mode 不使用 grep，因为 grep 不能表达文件枚举。回退顺序为：

1. `rg --files`
2. Git 工作区内使用 `git ls-files --cached --others --exclude-standard`
3. Backend 原生 `os.scandir()` 遍历

Git 回退：

- 命令参数由后端固定构造，不使用 shell。
- 只接受已经验证的 workspace 内根路径。
- Python 层应用 include/exclude glob、hidden、max_depth 和输出预算。
- `engine="git"`、`ignore_semantics="git"`、`degraded=false`。

文件系统回退：

- 使用 `os.scandir()`，不调用外部命令。
- `follow_symlinks=False`，跳过所有符号链接目录和文件。
- `hidden=false` 时跳过任一路径段以 `.` 开头的文件或目录。
- Python 层应用 include/exclude glob、max_depth 和输出预算。
- 无法完整复现 `.gitignore`/`.ignore`/`.rgignore`，因此返回
  `engine="filesystem"`、`ignore_semantics="basic"`、
  `degraded=true`。

回退差异必须显式进入结果，不允许把 basic walker 冒充完整 ripgrep 语义。

### 7.4 结果收集

统一的结果规范化步骤：

1. 将 engine 输出转成相对 `cwd` 的路径。
2. 将 `\` 规范化为 `/`。
3. 拒绝绝对路径和包含 `..` 的归一化结果。
4. 去重。
5. 应用 hidden、max_depth 和 glob 过滤。
6. 按路径字典序排序。
7. 在添加每个路径前计算 JSON 字符预算。
8. 返回不超过 limit 和 20,000 字符的结果。

## 8. 安全与权限

### 8.1 只读性

`RgTool.read_only=True` 保持不变，因此：

- `ToolDescriptor.risk` 继续为 `read`。
- `ToolPolicyEngine` 返回 `allow/read_only_tool`。
- Act mode 不弹审批。
- Plan mode 继续允许调用。

这项结论成立的前提是 files mode 只能使用本设计中的结构化字段。若未来加入
raw args，必须重新评估 `read_only` 标记。

### 8.2 明确禁止的能力

以下内容不进入工具 schema，也不能通过字段间接表达：

- `--pre=COMMAND`
- `--pre-glob`
- `--follow` / `-L`
- `--no-ignore` / `-u` / `-uuu`
- `--search-zip`
- 任意额外搜索根
- 绝对路径或 `..` 逃逸
- shell 运算符、管道、重定向
- 环境变量覆盖
- ripgrep 配置文件路径覆盖

### 8.3 符号链接

- 用户传入的根路径先 `resolve()`，若最终位置在 workspace 外则拒绝。
- 遍历期间不跟随符号链接。
- 文件系统回退不返回符号链接文件，确保各 engine 的边界一致。
- 后端在执行前和规范化结果时各检查一次 workspace 边界，降低
  TOCTOU 和平台路径差异造成的意外越界。

### 8.4 资源边界

- 复用 120 秒 timeout 硬上限。
- 默认只返回 500 个文件。
- 最大只返回 2,000 个文件。
- 返回 JSON 最大 20,000 字符。
- glob 数量和长度有限。
- max_depth 最大 64。
- 达到结果预算后主动终止仍在运行的枚举进程。

## 9. Agent 提示词

更新 `DEFAULT_TOOL_NOTES`：

```text
Use rg with mode="files" to enumerate workspace files. Narrow the path or
include_globs when the result is truncated. Do not use exec_command with ls,
find, dir, Get-ChildItem, or rg --files for ordinary workspace enumeration.
Use rg search mode for text search and read_file for selected file contents.
```

提示词只负责引导，不承担安全控制。即使模型忽略提示，后端参数校验和工具策略
仍必须独立成立。

工具 description 同时明确：

- 未指定 mode 时为文本搜索。
- 文件枚举使用 `mode="files"`。
- files mode 不需要 pattern。
- 结果截断后缩小 path 或 glob。

## 10. Observability

现有 `tool.call`、`tool.policy.evaluate` 和 `tool.execute` span 保留。新增安全、
低基数属性：

| Span/事件 | 属性 |
|---|---|
| `tool.call` | `tool="rg"`、`operation_mode="files"` |
| `tool.execute` | `engine`、`file_count`、`truncated`、`degraded` |
| `rg.files` | `include_glob_count`、`exclude_glob_count`、`max_depth`、`limit` |

禁止在普通 diagnostic/profile 中记录：

- 实际文件路径列表。
- glob 正文。
- cwd/path 正文。

`profile + content` 可继续按现有策略保存经过脱敏的工具请求和响应。

验收 profile 应能回答：

- 是否仍为文件枚举调用了 `exec_command`。
- 是否触发工具审批。
- files mode 的 engine 和耗时。
- 返回文件数量及是否截断。
- 工具结果字符数。
- 后续 LLM request 是否因文件树输出异常膨胀。

## 11. 与上下文压缩的关系

files mode 的首要控制点是生成紧凑结果，而不是依赖事后 context compression：

- 文件数组本身受 20,000 字符限制。
- 不返回重复 stdout/output。
- 不把未返回的完整枚举结果持久化进 provider context。
- 截断时要求模型缩小范围，而不是继续扩大单次结果。

现有 loop context compression 保持兜底职责，不修改触发阈值。

## 12. 代码改动范围

### `api/automata_api/agent/tools/search.py`

- 将共享 spec 拆为 `rg_parameters()` 与 `text_search_parameters()`。
- `RgTool.run()` 根据 mode 分派 search/files。
- 增加 files mode 参数校验和稳定错误。
- `GrepTool` 保持现有行为。

### `api/automata_api/agent/backends/base.py`

- 新增 `FileListResult`。
- 新增抽象 `Backend.list_files()`。

### `api/automata_api/agent/backends/local.py`

- 实现 `rg -> git -> filesystem` 文件枚举。
- 复用 workspace/cwd/path 解析。
- 实现行数、字符、超时和进程终止边界。

### `api/automata_api/agent/tools/_core.py`

- 增加 glob、limit、max_depth 参数读取函数。
- 增加 files mode 成功/错误 payload 构造。
- 增加路径规范化和紧凑 JSON 字符预算辅助函数。

### `api/automata_api/agent/prompts.py`

- 明确普通文件枚举优先使用 `rg mode="files"`。
- 阻止模型继续用 `exec_command` 执行 `find/ls/dir/Get-ChildItem`。

### `api/automata_api/agent/execution/orchestrator.py`

- 将经过枚举校验的 `operation_mode` 加入 tool span。
- 不改变审批策略；`rg` 继续通过 read-only 路径自动允许。

### 文档

- `api/README.md` 已更新工具协议说明和示例。
- 本文状态已改为 `Implemented`，并按仓库约定移入 `Docs/Archived/`。

不需要：

- 数据库迁移。
- WebSocket 协议变更。
- 前端状态或组件修改。
- 新工具注册或 tool_search 索引变化。

## 13. 自动化测试矩阵

### 13.1 工具协议

- 旧式 `rg {"pattern": "needle"}` 默认进入 search mode。
- 显式 `mode="search"` 与旧调用结果一致。
- `mode="files"` 不要求 pattern。
- 未知 mode 返回 `invalid_mode`。
- search mode 缺少 pattern 返回 `missing_pattern`。
- files mode 携带 pattern 返回稳定错误。
- `grep` spec 不出现 files mode 参数。

### 13.2 基本枚举

- 返回 workspace 内文件。
- 空目录成功并返回空数组。
- 路径统一使用 `/`。
- 结果顺序稳定。
- 不返回目录。
- 不返回符号链接。
- hidden 默认为 false。
- hidden=true 只影响 workspace 内隐藏路径。

### 13.3 过滤

- 单个 include glob。
- 多个 include glob 使用 OR 语义。
- 单个和多个 exclude glob。
- include 与 exclude 同时存在时 exclude 最终生效。
- max_depth 的 0、1、最大值边界。
- glob 数量、长度、空值和 NUL 校验。

### 13.4 工作区安全

- `path=".."` 被拒绝。
- 绝对工作区外路径被拒绝。
- cwd 越界被拒绝。
- 根路径符号链接指向工作区外时被拒绝。
- 工作区内目录包含外部 symlink 时不跟随、不返回目标。
- glob 值写成 `--pre=...` 只作为 glob 值处理，不能变成 option。
- schema 不接受 raw args 或额外 path 数组。

### 13.5 输出与资源限制

- 达到 limit 时 `truncated=true/file_limit`。
- 达到字符预算时 `truncated=true/character_limit`。
- 返回 JSON 不超过预算允许的固定协议开销。
- 大目录达到预算后终止子进程树。
- timeout 采用统一语义：已有结果时成功返回部分结果并标记
  `truncation_reason="timeout"`；尚无结果时返回稳定
  `enumeration_timed_out` 错误。
- files mode 不同时返回 stdout 和 output。

### 13.6 回退

- 原生 rg 可用时使用 `engine="rg"`。
- rg 缺失、Git workspace 可用时使用 `engine="git"`。
- rg/Git 均不可用时使用 `engine="filesystem"`。
- filesystem 回退设置 `degraded=true` 和
  `ignore_semantics="basic"`。
- 三种 engine 对 path、glob、hidden、max_depth、limit 的核心结果一致。

### 13.7 权限和 Plan mode

- files mode 的 policy decision 为 `allow/read_only_tool`。
- Act mode 不产生 `tool_approval_required`。
- Plan mode 能看到并调用同一个 `rg` 工具。
- files mode 不生成 command 风险 span。
- 现有 exec/write/destructive 审批测试不受影响。

### 13.8 Observability

- span 记录 `operation_mode="files"`。
- 完成 span 记录 engine、count、truncated、degraded。
- 非 content 模式不记录文件路径。
- result size 能由现有 `result_chars` 观测。

### 13.9 回归

- 现有 rg 文本命中、无命中、超时和 Bash fallback 测试全部保持。
- `grep` 现有测试全部保持。
- Windows 和 POSIX 路径测试通过。
- 完整后端 pytest 通过。
- `git diff --check` 通过。

## 14. 实施顺序

1. 在 Backend 增加 `FileListResult` 和 `list_files()`。
2. 完成 LocalBackend 的 rg 主路径及输出预算测试。
3. 增加 Git 和 filesystem 回退。
4. 拆分 rg/grep 参数 spec。
5. 在 `RgTool.run()` 增加 mode 分派和参数校验。
6. 增加紧凑返回协议。
7. 更新提示词，引导模型使用 files mode。
8. 增加权限、Plan mode 和 observability 测试。
9. 运行 targeted pytest 和完整 pytest。
10. 使用原项目检查任务重新采集 profile，比较审批、上下文和总耗时。
11. 验收后将文档状态改为 Implemented 并归档。已完成。

## 15. 验收标准

功能验收：

- 模型可通过 `rg {"mode":"files","path":"."}` 枚举文件。
- 旧 `rg {"pattern":"..."}` 调用无行为变化。
- `grep` 不支持 files mode。
- rg 缺失时可安全回退并明确报告降级语义。

安全验收：

- files mode 不经 shell。
- 不能表达 `--pre`、`--follow`、额外根路径或工作区越界。
- 不跟随符号链接。
- Act 和 Plan mode 均按 read-only 工具免审批。

性能验收：

- 普通项目枚举不产生 `exec_command` 或审批事件。
- 单次 files mode 结果不超过 2,000 个路径和 20,000 字符。
- 不出现 stdout/output 双份内容。
- 在同一项目检查任务中，文件枚举不再产生约 4 万字符的单个工具结果。
- Agent 能在达到 max steps 前进入写文档和最终回复，而不是持续使用 shell
  进行目录发现。

质量验收：

- 本文测试矩阵有对应自动化覆盖。
- 完整后端测试通过。
- 文档和 README 与最终参数、返回协议一致。
- 新 profile 能明确区分 files mode、engine、结果大小和截断状态。

## 16. 被否决的替代方案

### 新增独立 `list_files` 工具

安全和语义都可行，但会增加一个模型可见工具。当前选择在 `rg` 中增加受控
mode，以复用已有的只读搜索入口、Plan mode 白名单和用户心智。

### 向模型开放 ripgrep 原始参数

被否决。`--pre=COMMAND` 可执行外部程序，`--follow` 可穿过符号链接读取
工作区外内容，额外位置参数可绕过单根路径约束，无约束 ignore/hidden 参数也
会放大敏感信息暴露和结果体积。

### 对 `exec_command rg --files` 建立免审批规则

被否决。Shell 字符串的正确安全解析很难，且会把只读判断建立在命令文本
分类上；重定向、管道、命令替换和不同 shell 语义会扩大策略边界。

### 使用空 pattern 模拟文件枚举

被否决。空 pattern 是内容搜索语义，会产生匹配行而不是文件集合，并继续保留
grep fallback 和重复内容问题。

### files mode 继续回退到 grep

被否决。grep 没有等价的纯文件枚举模式。强行构造会造成平台行为不一致，并
再次依赖 shell。

## 17. 最终决策摘要

- 使用现有工具名 `rg`。
- 新增可选 `mode`，默认 `search`。
- 仅 `rg` 支持 `mode="files"`；`grep` 不支持。
- files mode 使用受控字段，不接受原始参数。
- 主路径为 `rg --files`，回退为 Git，再回退到不跟随链接的文件系统遍历。
- rg/Git 路径默认遵守 ignore；文件系统回退显式标记 ignore 语义降级。
- 所有 engine 默认跳过隐藏文件和符号链接。
- 默认 500 个文件，最大 2,000 个文件，结果最大 20,000 字符。
- 返回紧凑 files 数组，不返回重复 stdout/output。
- 保持 read-only、免审批和 Plan mode 可用。
- MVP 不提供分页；截断后由 Agent 缩小 path 或 glob。
