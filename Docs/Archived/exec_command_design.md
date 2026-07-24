# exec_command 与 live process session 实现说明

## Status

- 文档状态：`DONE`
- 最近核对：2026-07-23
- 代码基线：`main` / `220db76` 加本文对应工作区改动
- 已完成范围：一次性命令、持久化工具输出、非 PTY live session、pipe stdin、进程生命周期清理
- Deferred：PTY、真实 sandbox、远程 Backend

## 结论

Automata 的本地命令执行现已包含两种兼容模式：

1. 默认的一次性 `exec_command`：等待命令结束或 timeout，再返回最终结果。
2. 显式传入 `yield_time_ms` 的 live session：初始等待后若命令仍在运行，返回 `session_id`，后续用 `write_stdin` 写入 stdin 或空轮询。

这两种模式都使用真实本地子进程、现有 command 审批、workspace cwd 校验、`ProcessSupervisor` 进程树终止和有界输出。live session 是普通 stdin/stdout/stderr pipe，不是 PTY，不承诺终端行编辑、交互式密码提示、resize 或全屏 TUI。

## 实现路径

```text
agent runtime
  -> ToolExecutionOrchestrator
     -> ToolPolicyEngine / ApprovalBroker
     -> process_execution_scope(run, session, workspace, tool_call)
     -> tool_output_execution_scope()
  -> ExecCommandTool / WriteStdinTool
  -> LocalBackend
  -> tools/_core.py
  -> ProcessSessionManager       # live session、stdin、poll、transcript
  -> ProcessSupervisor           # 平台相关进程树终止
```

主要文件：

| 职责 | 文件 |
| --- | --- |
| `exec_command` schema | `api/automata_api/agent/tools/exec_command.py` |
| `write_stdin` schema | `api/automata_api/agent/tools/write_stdin.py` |
| 命令和结果协议 | `api/automata_api/agent/tools/_core.py` |
| live session store | `api/automata_api/agent/execution/process_sessions.py` |
| 进程树清理 | `api/automata_api/agent/execution/process.py` |
| 通用输出事件 scope | `api/automata_api/agent/execution/tool_output.py` |
| 事件持久化与 Run 预算 | `api/automata_api/agent/execution/events.py` |
| Run 生命周期清理 | `api/automata_api/agent/execution/coordinator.py` |
| UI 增量归并 | `ui/src/state/chatReducer.ts` |
| UI 工具卡 | `ui/src/components/conversation/ToolCard.tsx` |

## exec_command 协议

### 输入

```json
{
  "cmd": "string",
  "shell": "bash | powershell",
  "workdir": "workspace-relative path",
  "timeout_seconds": 30,
  "max_output_chars": 20000,
  "yield_time_ms": 10000
}
```

- 只有 `cmd` 必填。
- `shell` 默认 `bash`，也支持 `powershell`。
- `workdir` 默认 `.`，解析结果必须是 workspace 内已存在目录。
- `timeout_seconds` 默认 30 秒，最大 120 秒。
- `max_output_chars` 默认 20000，最大 60000。
- `yield_time_ms` 不传时保持原有一次性语义；传入后最小为 0，最大 30000 毫秒。

cwd 校验不是 sandbox。获批脚本仍继承 API 进程权限，并可能访问 workspace 外文件、网络和环境变量。

### 一次性结果

未传 `yield_time_ms` 时，结果包含：

```json
{
  "simulated": false,
  "ok": true,
  "tool": "exec_command",
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

最终 stdout/stderr 使用有界 head-tail buffer；小于 marker 空间的极小限制直接保留头尾字符，不强插截断标记。`output` 再按同一上限处理。

### live session 结果

传入 `yield_time_ms` 后，如果进程仍在运行：

```json
{
  "ok": true,
  "running": true,
  "session_id": "proc_<opaque-id>",
  "exit_code": null,
  "stdout": "...",
  "stderr": "",
  "output": "..."
}
```

如果进程在初始等待内结束或 timeout，返回终态并不包含 `session_id`。进程 timeout 时 `timed_out=true`、`exit_code=null`、`ok=false`。

## write_stdin 协议

```json
{
  "session_id": "proc_<opaque-id>",
  "chars": "hello\n",
  "yield_time_ms": 250,
  "max_output_chars": 20000
}
```

规则：

- `session_id` 必填。
- `chars` 默认空字符串；空字符串只轮询。
- 非空字符串按 UTF-8 写入 stdin pipe。
- `yield_time_ms` 默认 250，最大 30000。
- 有输出时可以在进程退出前返回 `running=true`；调用方随后用空 `chars` 继续轮询。
- 观察到终态后 session 从 store 移除；再次访问返回 `process_session_not_found`。
- session 严格绑定创建它的 Run、会话和 workspace；不匹配返回 `process_session_scope_mismatch`。

空轮询按 read risk 放行；非空 stdin 写入按 command risk 请求审批。Plan mode 不暴露 `write_stdin`，直接绕过调用也会被非只读策略阻止。

## 持久化 output delta

stdout/stderr 通过通用事件发送：

```json
{
  "type": "tool_output_delta",
  "tool_call_id": "call-id",
  "tool": "exec_command",
  "stream": "stdout",
  "content": "incremental output",
  "truncated": false
}
```

实现特性：

- 事件进入现有 Run sequence，并写入 `agent_run_events`，断线后可经现有 replay 读取。
- 单事件最多 8192 字符。
- 单工具调用累计最多 262144 字符。
- 单 Run 的工具输出默认累计最多 1000000 字符，可用 `AUTOMATA_RUN_TOOL_OUTPUT_MAX_CHARS` 调整。
- Run event 仍受 `AUTOMATA_RUN_EVENT_MAX_BYTES` 的单 payload 限制。
- UI 按 `tool_call_id` 把 stdout/stderr 增量归入运行中的 `ToolCard`，最终 `tool_result` 是权威结果。
- 命令主动打印到 stdout/stderr 的 secret 仍可能被持久化；字段名 redaction 不能解析任意输出正文。

live session 的后台 reader 只维护有界 pending/transcript；本次工具调用返回的输出再通过通用 delta 发送。后续 `write_stdin` 轮询得到的输出归属于该次 `write_stdin` tool call。

## 生命周期和资源上限

`ProcessSessionManager` 当前硬限制：

| 项目 | 值 |
| --- | --- |
| 同时 live session 数 | 8 |
| 空闲回收 | 60 秒 |
| 每个 stdout/stderr transcript | 60000 字符 head-tail |
| reader chunk | 8192 bytes |
| 命令总 timeout | 仍受 `timeout_seconds <= 120` 限制 |

清理路径：

- 命令 timeout；
- session 空闲回收；
- Run 正常完成；
- Run 取消、失败或中断；
- API shutdown。

Windows 优先使用 Job Object，失败时回退 `taskkill /T /F`；POSIX 使用 process group，先 `SIGTERM`，必要时 `SIGKILL`。WebSocket 断开本身不会取消 durable Run；session 仍受 Run、timeout 和 idle cleanup 约束。

## 安全边界

已实现：

- cwd 必须在 workspace 内；
- command risk 审批；
- Plan mode 阻止非只读工具；
- session 跨 Run/session/workspace 隔离；
- 进程数、输出、transcript、idle 和 timeout 上限；
- Run 和进程树联动清理。

未实现：

- 文件系统或网络 sandbox enforcement；
- restricted token/container；
- 环境变量和凭据隔离；
- PTY/ConPTY；
- terminal resize 和控制字符协议；
- RemoteBackend、远程认证与远端取消。

因此公开 schema 不包含 `tty`、`sandbox_permissions`、`environment_id` 等没有 enforcement 的字段。

## 测试覆盖

后端覆盖：

- Bash / PowerShell、cwd、非法 shell、timeout；
- stdout/stderr 并发读取和 head-tail 截断；
- `tool_output_delta` 顺序、持久化和 Run 累计预算；
- live session 初始 yield；
- stdin 写入、输出后继续轮询、自然退出；
- 跨 Run 拒绝；
- timeout 和显式 Run cleanup；
- command 审批、Plan mode、进程树清理。

前端覆盖：

- stdout/stderr delta 归并到匹配工具卡；
- final result 覆盖运行态展示；
- Vitest 和生产构建。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest
npm --prefix ui test
npm --prefix ui run build
```

## Deferred

### PTY

等待跨平台 PTY adapter、resize 协议、控制字符/ANSI transcript 策略、输入输出预算和自动化矩阵。当前 pipe session 不应被描述成 terminal emulator。

### 真实 sandbox

等待威胁模型、OS 支持矩阵、至少一个实际 enforcement runtime，以及文件、网络、环境变量和凭据隔离方案。

### 远程执行

等待 `RemoteBackend`、transport/auth、远端进程 store 和取消/恢复协议；不会通过 tool-level `environment_id` 冒充实现。
