# exec_command 当前实现与后续设计

## Status

- 文档状态：MVP 已实现；live session / PTY / `write_stdin` 等高级能力尚未实现
- 最近核对：2026-07-20
- 代码基线：`main` / `d0f2eea`
- 已归档的 MVP 方案：[exec_command MVP 落地设计](./Archived/exec_command_mvp_design.md)

## 结论

Automata 当前的 `exec_command` 是一个真实、一次性、非交互的本地命令工具：

- 支持 `bash` 和 `powershell`；
- cwd 必须位于 session workspace 内；
- 捕获 stdout/stderr，按字符数限制模型可见内容；
- 支持 timeout；
- 命令执行前需要用户单次审批；
- Run 取消、timeout 或 API shutdown 会终止已注册的进程树；
- 使用普通 `tool_call` / `tool_result` 事件，不流式发送命令输出。

当前实现不是完整的 terminal session 系统。仓库中没有 `write_stdin`、PTY、live process store、`session_id`、`yield_time_ms`、远程 environment、sandbox 或 command hooks。

## 当前代码路径

```text
agent runtime
  -> ToolRouter.execution_descriptor("exec_command")
  -> ToolExecutionOrchestrator
     -> ToolPolicyEngine                  # command risk
     -> ApprovalBroker                    # allow_once / deny
     -> process_execution_scope(run_id, tool_call_id)
  -> ToolRouter.dispatch_authorized()
  -> ExecCommandTool.run()
  -> LocalBackend.run_exec_command()
  -> tools/_core.py::run_exec_command()
  -> asyncio.create_subprocess_exec()
  -> capture_process_output()
  -> ProcessSupervisor
```

对应文件：

| 职责 | 文件 |
| --- | --- |
| 模型可见 schema | `api/automata_api/agent/tools/exec_command.py` |
| Backend 注入 | `api/automata_api/agent/backends/local.py` |
| 参数解析与进程执行 | `api/automata_api/agent/tools/_core.py` |
| 风险与审批策略 | `api/automata_api/agent/execution/policy.py` |
| 审批事件与结果 | `api/automata_api/agent/execution/approval.py` |
| 执行编排 | `api/automata_api/agent/execution/orchestrator.py` |
| 进程树注册和终止 | `api/automata_api/agent/execution/process.py` |
| Windows Job Object | `api/automata_api/agent/execution/windows_job.py` |
| Run 生命周期 | `api/automata_api/agent/execution/coordinator.py` |
| 前端工具展示 | `ui/src/components/conversation/ToolCard.tsx` |

## 当前工具协议

### 输入

模型实际看到的 schema：

```json
{
  "cmd": "string",
  "shell": "bash | powershell",
  "workdir": "workspace-relative path",
  "timeout_seconds": 30,
  "max_output_chars": 20000
}
```

只有 `cmd` 必填。

| 字段 | 当前语义 |
| --- | --- |
| `cmd` | 交给所选 shell 的脚本文本 |
| `shell` | 默认 `bash`；只接受 `bash` / `powershell` |
| `workdir` | 默认 `.`；可传绝对或相对路径，但解析结果必须位于 workspace 内且是已存在目录 |
| `timeout_seconds` | 默认 30 秒；非正数或非法值回退默认值；最大 120 秒 |
| `max_output_chars` | 默认 20000；非正数或非法值回退默认值；最大 60000 |

当前 schema 不包含：

- `login`
- `tty`
- `yield_time_ms`
- `max_output_tokens`
- `environment_id`
- `sandbox_permissions`
- `additional_permissions`
- `justification`
- `prefix_rule`

这些字段不能由调用方传入后期待生效。

### Shell 解析

`bash` 使用：

```text
bash -lc <cmd>
```

Windows 上会查找可用 Bash，包括 Git Bash；找不到时返回结构化失败结果。

`powershell` 使用：

```text
pwsh|powershell -NoProfile -NonInteractive -Command <cmd>
```

优先使用 `pwsh`，Windows 上也会回退到 Windows PowerShell。

### 输出

成功或命令失败都返回同一 JSON 形状：

```json
{
  "simulated": false,
  "ok": true,
  "tool": "exec_command",
  "cmd": "git status --short",
  "shell": "bash",
  "workdir": ".",
  "cwd": "D:\\workspace\\projects\\automata",
  "shell_path": "C:\\Program Files\\Git\\bin\\bash.exe",
  "timeout_seconds": 30.0,
  "duration_seconds": 0.123,
  "exit_code": 0,
  "timed_out": false,
  "stdout": "...",
  "stderr": "",
  "output": "...",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "output_truncated": false
}
```

规则：

- `ok=true` 仅在 `exit_code == 0` 且未 timeout 时成立；
- 非零退出码是正常的工具失败结果，不抛未捕获异常；
- timeout 时 `timed_out=true`、`exit_code=null`；
- 启动失败、缺少 cmd、非法 cwd 或 shell 也返回相同基础字段；
- 非法 shell 额外返回 `supported_shells`；
- `stdout` 和 `stderr` 分别最多保留 `max_output_chars` 个字符；
- `output` 是 stdout 与标记后的 stderr 合并结果，再次受同一字符上限限制；
- 当前截断保留前缀，不是 head-tail buffer；
- 读取使用增量 UTF-8 decoder，非法字节替换而不是使工具崩溃。

## 当前执行与生命周期语义

### 一次性执行

当前实现会等待进程结束或 timeout 后才向模型返回 `tool_result`。即使子进程正在持续输出，前端也不会收到 output delta。

不存在可继续交互的 process session：

- 返回值没有 `session_id`；
- 进程不会在首个工具响应后继续存活；
- 没有轮询接口；
- 没有 stdin 写入接口。

### 审批

`BackendToolProvider` 将 `exec_command` 标记为：

- `read_only=false`
- `risk="command"`
- 默认 direct exposure

`ToolPolicyEngine` 对 act mode 的 command risk 返回 `prompt`。当前命令审批没有可复用 scope，因此审批选项是：

- `allow_once`
- `deny`

不是 `allow_for_run`。

Plan mode 不向模型暴露 `exec_command`；即使绕过可见工具列表直接调用，也会被 `blocked_by_plan_mode` 拒绝，不进入审批。

### Run 取消和进程树

`ToolExecutionOrchestrator` 在授权执行时设置 `process_execution_scope(run_id, tool_call_id)`。`capture_process_output()` 随后把子进程注册到 `ProcessSupervisor`。

当前终止路径：

- timeout；
- asyncio task 被取消；
- 用户发送 `cancel_run`；
- Run 失败后的清理；
- API shutdown。

Windows 优先用 Job Object 终止进程树，分配失败时回退 `taskkill /T /F`。POSIX 使用新 session/process group，先 `SIGTERM`，超时后 `SIGKILL`。

这已经解决“一次性命令在 Run 取消后遗留子进程”的问题，但它不是 live session manager：注册项只用于终止和清理，不支持再次查找、写 stdin 或读取增量输出。

## 当前 WebSocket 和 UI 行为

命令使用通用事件：

```text
tool_call
tool_approval_required
tool_approval_resolved
tool_result
```

- `tool_call` 和最终 `tool_result` 会持久化到 Run event；
- tool run message 保存参数和最终结果；
- `ToolApprovalCard` 显示命令摘要与预览；
- `ToolCard` 展示完成状态和结构化结果；
- 没有 `ExecCommandBegin`、`ExecCommandOutputDelta`、`TerminalInteraction` 或 `ExecCommandEnd` 专用事件。

## 安全边界

### 已有保护

- 工具参数中的 cwd 不能逃出 workspace；
- command tool 默认必须人工审批；
- Plan mode 禁止命令；
- timeout 和显式取消会终止进程树；
- 模型可见输出有字符上限；
- Tool event 经过现有 Run event redaction。

### 当前不提供的保护

cwd 限制不是 sandbox。

一旦用户批准，shell 脚本仍可：

- `cd` 到 workspace 外；
- 使用绝对路径读写其他文件；
- 访问网络；
- 启动系统程序；
- 继承 API 进程可见的环境变量和操作系统权限。

当前没有：

- 文件系统 allowlist enforcement；
- 网络隔离；
- restricted token / container sandbox；
- unsandboxed escalation policy；
- prefix-rule 持久批准；
- shell command 静态安全分析。

现有 Run event redaction 会清理 secret-like 字段名，但不会解析 `stdout` / `stderr` 字符串；命令主动打印出的 secret 仍可能被持久化并显示。

文档和 UI 不应把“workdir 位于 workspace 内”描述成“命令只能影响 workspace”。

## 已实现测试覆盖

`api/tests/test_tools.py` 已覆盖：

- Bash 执行；
- PowerShell 执行（可用平台）；
- workdir 越界；
- 不支持的 shell；
- timeout；
- timeout 前缀输出保留；
- stdout/stderr 限制；
- 大输出并发读取。

`api/tests/test_execution_safety.py` 已覆盖：

- command/write 审批；
- Plan mode 拒绝；
- 取消命令时终止子进程树；
- timeout 时终止子进程树。

`api/tests/test_chat.py` 已覆盖真实 WebSocket agent loop 中的 `exec_command` tool call/result。

## 后续目标

只有产品确实需要“长任务 / 交互终端”时，才继续扩展以下能力：

1. 命令输出在运行中增量显示；
2. 长命令在初始等待后返回 `session_id`；
3. `write_stdin` 轮询和交互；
4. PTY；
5. live process 数量、空闲时间和关闭清理；
6. 可配置 sandbox / permission；
7. 多 environment 或远程执行；
8. pre/post tool hooks。

这些是后续设计，不是当前接口承诺。

## 后续协议草案

### 扩展 `exec_command`

在 live session 阶段才加入：

```json
{
  "cmd": "long-running command",
  "workdir": ".",
  "shell": "bash",
  "tty": false,
  "yield_time_ms": 10000,
  "max_output_chars": 20000
}
```

建议继续使用 `max_output_chars`，除非 runtime 真正接入统一 token budget；不能仅改字段名为 `max_output_tokens`。

进行中的响应：

```json
{
  "ok": true,
  "running": true,
  "session_id": "process-id",
  "output": "...",
  "output_truncated": false
}
```

完成的响应继续包含 `exit_code`，不包含 `session_id`。

### `write_stdin`

只有 live session store 和 PTY/pipe 语义完成后才注册：

```json
{
  "session_id": "process-id",
  "chars": "",
  "yield_time_ms": 250,
  "max_output_chars": 20000
}
```

建议规则：

- 空 `chars` 表示轮询；
- 非空写入只允许对声明为可交互的 session；
- 已退出或未知 session 返回稳定错误；
- session 必须绑定创建它的 Run/session/workspace；
- poll 观察到进程结束时，从 store 移除；
- `cancel_run` 和 API shutdown 终止并移除全部关联 session。

## 后续架构

### ProcessSessionManager

当前 `ProcessSupervisor` 只负责终止。不要直接向它堆入 transcript、stdin 和 UI 事件。

建议新增独立的 `ProcessSessionManager`：

```python
ProcessSessionEntry = {
    "session_id": "...",
    "run_id": "...",
    "tool_call_id": "...",
    "workspace": "...",
    "process": "...",
    "tty": False,
    "created_at": "...",
    "last_used_at": "...",
}
```

它负责：

- 分配和查找 session id；
- 有界 transcript；
- stdin / poll；
- 最大 live process 数；
- idle eviction；
- Run cancel 和 shutdown；
- 最终状态幂等。

`ProcessSupervisor` 继续负责平台相关进程树终止。

### 输出事件

后续可新增版本化事件：

- `exec_command_begin`
- `exec_command_output_delta`
- `exec_command_end`
- `terminal_interaction`

这些事件必须进入现有持久化 Run sequence，支持断线 replay。不能只向当前 WebSocket 发送临时 delta，否则刷新后 UI 与实际 Run 不一致。

输出 delta 需要独立预算和批处理；最终 tool result 仍保持有界。

### Sandbox 和权限

Sandbox 不能只靠给 `exec_command` 增加几个未实现字段。正确顺序是：

1. 定义平台无关的执行权限模型；
2. 实现至少一个真正 enforcement 的 runtime；
3. 让 `ToolExecutionOrchestrator` 选择策略；
4. 只有 enforcement 存在时才向模型暴露 `sandbox_permissions`；
5. 增加 denial、retry、cancel 和审计测试。

## 实施阶段

### Phase 0：一次性命令 MVP

状态：`DONE`

- bash / PowerShell；
- cwd 校验；
- timeout；
- 有界 stdout/stderr；
- 通用 tool events。

### Phase 1：审批和进程树清理

状态：`DONE`

- command risk；
- allow-once / deny；
- Plan mode 阻止；
- Run cancel；
- Windows Job / taskkill / POSIX process group。

### Phase 2：持久化 output delta

状态：`TODO`

- begin/delta/end event；
- 批处理和 event payload 限制；
- UI 增量显示；
- 断线重放。

### Phase 3：live sessions 和 `write_stdin`

状态：`TODO`

- session manager；
- 初始 yield；
- poll；
- stdin；
- process limit / idle eviction；
- Run 绑定与关闭清理。

### Phase 4：PTY

状态：`TODO`

- 平台 PTY adapter；
- resize 和交互；
- 控制字符与 transcript 安全。

### Phase 5：sandbox、environment 和 hooks

状态：`TODO`

- 在有真实 enforcement 后再设计对外字段；
- 多环境/远程执行；
- pre/post hooks；
- 网络和文件权限。

## 验收标准

当前 MVP 的验收标准：

1. Schema 与真实参数一致。
2. cwd 校验、timeout 和输出限制有测试。
3. command 必须审批，Plan mode 不执行。
4. cancel/timeout 不遗留子进程树。
5. 所有失败返回稳定 JSON。
6. 全量 API 测试通过。

未来 live session 阶段还必须满足：

1. 运行中才返回 `session_id`，完成时返回 `exit_code`。
2. output delta 可持久化和重放。
3. `write_stdin` 不能访问其他 Run 的 process。
4. 进程、transcript 和 session 数量都有硬上限。
5. cancel、disconnect、API shutdown 和 idle eviction 不遗留进程。
6. UI build 和新增 reducer/session 测试通过。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest tests/test_tools.py tests/test_execution_safety.py tests/test_chat.py
uv run --directory api --group dev --locked pytest
npm --prefix ui run build
```
