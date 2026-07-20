# Automata 本地执行安全与可取消运行 MVP 设计

- 文档状态：Implemented（2026-07-15）
- 适用仓库：`D:\workspace\projects\automata`
- 基线日期：2026-07-15
- 实施范围：权限审批、API 隔离、停止任务、进程树清理

## 1. 背景

Automata 已经具备一条完整的本地 Agent 主链路：

- Tauri 启动 FastAPI sidecar；
- React 通过 HTTP 管理 session，通过 WebSocket 发送 prompt；
- `services/chat.py` 创建 backend、MCP runtime、skills context；
- `agent/runtime.py` 调用模型并把 tool call 交给 `ToolRouter`；
- `ToolRouter` 最终执行本地文件、patch、shell 或 MCP 工具；
- UI 展示 token、plan 和 tool result。

这条链路可以完成真实编码任务，但目前有四个相互关联的安全缺口：

1. Act 模式下的写文件、patch 和 shell 命令没有统一的用户审批点；
2. FastAPI 和 WebSocket 没有应用级身份校验；
3. WebSocket 正在执行一次回复时不能继续接收“批准”或“停止”消息；
4. timeout 和连接中断没有统一保证整个子进程树被清理。

这四项必须作为同一个 MVP 交付。只增加审批弹窗而不重构 WebSocket 接收模型，后端会因为正在等待 agent reply 而收不到审批结果；只增加停止按钮而不集中管理子进程，按钮只能取消 UI 状态，不能保证 shell 子进程退出。

## 2. 目标

本方案完成后应满足：

1. 所有从 Agent runtime 发起的工具执行都经过同一个 execution orchestrator。
2. Plan mode 的只读限制仍是不可被审批覆盖的硬边界。
3. 读工具默认直接执行；写、命令、破坏性和外部副作用工具按策略 allow、prompt 或 deny。
4. 用户可以在同一条 WebSocket 连接中批准、拒绝或停止当前 run。
5. 每次 prompt 或 approved plan execution 都获得一个临时 `run_id`。
6. 同一个 session 同时最多有一个活动 run。
7. 取消、超时、WebSocket 断开和应用退出都会清理本地进程树。
8. HTTP 与 WebSocket 都要求 sidecar 启动时生成的临时 token。
9. 默认只允许 loopback API；workspace 内容不能修改授权状态。
10. 审批等待、取消和拒绝都有稳定的模型结果与 UI 事件。

## 3. 非目标

以下能力不在本次实现：

- 不实现完整 OS 文件系统沙箱或容器隔离；
- 不实现持久化 RunTrace、断线事件重放或跨进程恢复；
- 不实现通用交互式终端、PTY、`write_stdin` 或后台 terminal session；
- 不实现远程 API 服务模式；
- 不实现命令语义证明，不依赖 denylist 判断一个 shell 脚本绝对安全；
- 不允许 workspace 文件、skill 或 MCP definition 自行授予权限；
- 不把 plan approval 当成工具权限批准；
- 不在本阶段持久化“永久允许某类命令”的宽泛规则。

本方案会保留 `run_id`、execution policy 和 process supervisor 扩展点，但不会提前实现后续 Run 持久化系统。

## 4. 核心设计决策

### 4.1 审批位于工具路由与 executor 之间

审批不能写进 `ExecCommandTool`、`ApplyPatchTool` 或 `McpAgentTool`。否则：

- 每个工具会重复实现等待、取消和错误映射；
- 新增动态工具时容易绕过审批；
- Plan mode、MCP grant 和本地权限会形成多套不一致的策略。

新增 `ToolExecutionOrchestrator`，Agent runtime 不再直接调用 `router.dispatch()`，而是调用：

    orchestrator.execute(
        router=router,
        tool_name=name,
        raw_arguments=arguments,
        context=execution_context,
    )

`ToolRouter` 负责名称、exposure、deferred activation 和 Plan mode；orchestrator 负责风险分类、审批和取消；具体 `AgentTool` 只负责执行。

### 4.2 临时 run 与持久化 run 分离

本次需要 `run_id` 来寻址审批和取消，但不创建数据库表。

- `run_id`：单次 prompt/plan execution 的 UUID；
- 生命周期：仅当前 sidecar 进程内；
- 活动状态：`ActiveRunRegistry` 内存存储；
- 连接断开或 sidecar 重启后：run 结束并标记为 interrupted/cancelled 的 UI 语义；
- 后续持久化 Run 系统复用相同 `run_id` 和事件字段。

### 4.3 取消是协作式请求加最终强制终止

收到 `cancel_run` 后：

1. 设置 cancellation event；
2. 结束所有 pending approval future；
3. 取消 agent asyncio task；
4. 关闭 provider stream 和 MCP runtime；
5. 对活动本地 process 先 graceful terminate；
6. 超过短 grace period 后强制杀死整个 process tree；
7. 发出唯一终态 `run_cancelled`。

不能只调用 `task.cancel()`，也不能只更新 React 状态。

### 4.4 API token 是 sidecar capability，不是用户账号

Automata 当前是单用户本地应用，本次不设计登录系统。

- Tauri 每次启动生成 32 字节随机 token；
- token 通过环境变量传给 sidecar；
- token 通过 Tauri command 只返回给当前 WebView；
- HTTP 使用 `Authorization: Bearer <token>`；
- WebSocket 连接后第一条消息必须是 `authenticate`；
- token 不写入 SQLite、日志、session message 或 workspace；
- sidecar 退出后 token 失效。

### 4.5 Plan approval 与 tool approval 不等价

`approve_plan` 表示“允许按此方案开始执行”，不表示：

- 允许所有 shell 命令；
- 允许删除任意文件；
- 允许网络或外部 MCP 副作用；
- 允许突破 Plan mode 或 workspace 边界。

Approved plan execution 仍按照普通 Act run 的 tool policy 逐项审批。

## 5. 总体架构

    Tauri
      ├─ 生成 runtime API token
      ├─ 启动 FastAPI sidecar
      └─ 关闭时终止 sidecar
             │
             ▼
    FastAPI auth/origin boundary
             │
             ▼
    AgentConnection
      ├─ 持续 receive WebSocket messages
      ├─ serialized sender
      ├─ active run task
      └─ approval response / cancel routing
             │
             ▼
    ActiveRunRegistry
      ├─ run_id
      ├─ session lease
      ├─ cancellation event
      └─ ApprovalBroker
             │
             ▼
    ToolExecutionOrchestrator
      ├─ ToolRouter resolve and Plan gate
      ├─ ToolPolicyEngine
      ├─ ApprovalBroker
      └─ authorized executor call
             │
             ▼
    ProcessSupervisor / MCP manager / file tools

必须保持以下不变式：

- 没有 descriptor 的工具不能从 Agent runtime 直接执行；
- approval 不能把 Plan mode deny 变成 allow；
- approval 只对生成它的 run、workspace 和 tool scope 生效；
- run 终态发出后不能再产生 token 或工具事件；
- process 注册成功后才允许把控制权返回上层；
- process 从 registry 移除前必须确认退出或记录强制清理失败。

## 6. 数据模型

建议新增 `api/automata_api/agent/execution/model.py`。

### 6.1 风险类型

    ToolRisk = Literal[
        "read",
        "write",
        "command",
        "destructive",
        "external",
    ]

含义：

| 风险 | 含义 | 默认动作 |
| --- | --- | --- |
| `read` | 只读 workspace 或只读元数据 | allow |
| `write` | 创建、修改 workspace 文件 | prompt |
| `command` | 启动任意 shell/process | prompt |
| `destructive` | 删除、覆盖高风险状态、不可逆操作 | prompt，不能宽泛复用 |
| `external` | MCP、网络或工作区外部系统副作用 | 结合 MCP grant 后 prompt/deny |

### 6.2 策略结果

    PolicyAction = Literal["allow", "prompt", "deny"]

    @dataclass(frozen=True)
    class ToolPolicyDecision:
        action: PolicyAction
        risk: ToolRisk
        reason: str
        approval_scope: str | None = None
        allow_for_run: bool = False

稳定 reason code 至少包括：

- `read_only_tool`
- `workspace_write_requires_approval`
- `command_requires_approval`
- `destructive_action_requires_approval`
- `mcp_call_requires_approval`
- `blocked_by_plan_mode`
- `tool_not_available`
- `policy_denied`

### 6.3 Execution context

    @dataclass(frozen=True)
    class ToolExecutionContext:
        run_id: str
        session_id: str
        tool_call_id: str
        workspace: str
        mode: Literal["act", "plan"]
        cancellation: CancellationToken

### 6.4 Approval request

    @dataclass(frozen=True)
    class ApprovalRequest:
        approval_id: str
        run_id: str
        session_id: str
        tool_call_id: str
        tool: str
        tool_identity: str
        risk: ToolRisk
        reason: str
        summary: str
        preview: dict[str, Any]
        scope: str | None
        options: tuple[str, ...]
        created_at: str

MVP decision：

    ApprovalDecision = Literal["allow_once", "allow_for_run", "deny"]

`allow_for_run` 不是“当前 run 内所有命令都允许”。它只能应用到后端给出的稳定 scope：

- 非删除文件修改：`workspace_write`；
- MCP：精确 `server_fingerprint + original_tool_name`；
- destructive：默认不提供 `allow_for_run`；
- shell command：第一版只提供 `allow_once`，不尝试从 shell 字符串推断安全 prefix。

`allow_for_workspace` 字段可以为未来协议预留，但当前 UI 不展示、后端不接受，避免在缺少规则版本化和撤销 UI 时形成永久授权。

## 7. 工具风险与审批策略

### 7.1 ToolDescriptor 扩展

在 `agent/tools/model.py` 的 `ToolDescriptor` 增加：

    risk: ToolRisk
    policy: ToolCallPolicy | None = None

`descriptor_for_tool()` 根据内建工具提供默认 risk，也允许工具显式声明。

初始映射：

| 工具 | 基础风险 | 动态规则 |
| --- | --- | --- |
| `read_file` | read | 无 |
| `rg` / `grep` | read | 无 |
| `apply_patch_preview` | read | 只能 dry-run，保持只读 |
| `tool_search` | read | 只激活 descriptor，不执行目标工具 |
| `write_file` | write | delete 不适用；overwrite 仍需审批 |
| `apply_patch` | write | 含 Delete File 时升级为 destructive |
| `exec_command` | command | 始终 prompt |
| `run_bash` | command | 始终 prompt |
| `run_powershell` | command | 始终 prompt |
| MCP read-only tool | external/read | 必须同时满足 server grant 和 tool policy |
| MCP mutating/unknown tool | external | 默认 prompt 或 deny |

命令关键词只能用于 UI 提示“可能包含删除/发布/提权”，不能把未命中关键词的命令自动判定为安全。

### 7.2 策略顺序

每次 tool call 按固定顺序处理：

1. 解析 JSON arguments；
2. ToolRouter 检查工具是否存在、是否 hidden/deferred；
3. Plan mode 检查 read-only；
4. 工具/MCP policy 计算 allow/prompt/deny；
5. 检查当前 run 已有的 scoped approval；
6. 如需 prompt，向 UI 发出 approval request 并等待；
7. 再次检查 cancellation；
8. 执行工具；
9. 返回 `ToolResult`。

步骤 2 和 3 的 deny 不进入 approval；用户不能批准一个 Plan mode 下的写工具。

### 7.3 MCP 策略接入

当前 `McpAgentTool.run_in_mode()` 内部调用 `McpPolicyEngine`，`prompt` 会直接返回 `mcp_approval_required`。实现交互审批后应调整为：

- `McpToolProvider` 为 descriptor 附加 MCP policy adapter；
- orchestrator 在调用 executor 前执行 MCP policy；
- `deny` 仍直接拒绝；
- `prompt` 进入通用 `ApprovalBroker`；
- 用户批准后执行 `McpAgentTool`；
- `McpAgentTool` 保留参数/结果 schema 验证，但不再独立完成交互审批。

MCP server connection grant 与单次 tool call approval 仍是两层：

1. 没有 server grant：不得连接，不产生 tool call approval；
2. server 已 grant，但 tool policy 为 prompt：产生交互审批；
3. tool policy 为 deny：不得用交互审批覆盖。

### 7.4 拒绝语义

用户点击 deny 不取消整个 run。orchestrator 返回失败的 `ToolResult`：

    {
      "simulated": false,
      "ok": false,
      "tool": "exec_command",
      "error": "tool_approval_denied",
      "approval_id": "...",
      "reason": "User denied this tool call."
    }

该结果继续发送给模型，模型可以选择只读替代方案或向用户说明无法继续。

取消 run 则不构造普通拒绝结果，而是终止整个 agent loop。

## 8. ApprovalBroker

新增 `api/automata_api/agent/execution/approval.py`。

职责：

- 创建不可预测的 `approval_id`；
- 保存 `approval_id -> Future`；
- 发出 `tool_approval_required`；
- 校验 response 属于当前 connection/run；
- 只允许第一次 response 生效；
- run cancel/disconnect 时结束所有 pending future；
- 实现 approval timeout；
- 维护仅当前 run 有效的 scoped grants。

建议默认 timeout 为 5 分钟，可通过 `AUTOMATA_APPROVAL_TIMEOUT_SECONDS` 配置并设合理上限。

状态机：

    pending -> allowed_once
            -> allowed_for_run
            -> denied
            -> cancelled
            -> expired

重复、过期或跨 run response 返回 `approval_error`，但不影响当前 run。

审批正文不写入 session message、SQLite 或普通日志。日志只记录：

- run id 短 hash；
- tool identity；
- risk；
- decision；
- duration；
- reason code。

## 9. WebSocket 协议

### 9.1 为什么必须改连接模型

当前 `routers/chat.py` 在收到 prompt 后直接 `await stream_agent_reply()`。直到整个 reply 完成，同一 coroutine 才会再次执行 `receive_text()`。

因此新增审批或停止消息前，必须把连接改成：

- 一个持续运行的 receive loop；
- 一个独立 active run task；
- 一个串行化 sender；
- 一个 connection-scoped ApprovalBroker。

### 9.2 Client -> Server

认证：

    {
      "type": "authenticate",
      "token": "<runtime token>"
    }

审批：

    {
      "type": "tool_approval_response",
      "run_id": "...",
      "approval_id": "...",
      "decision": "allow_once"
    }

停止：

    {
      "type": "cancel_run",
      "session_id": "...",
      "run_id": "..."
    }

现有 `prompt` 和 `approve_plan` 保持兼容，但服务端会为实际执行创建 `run_id`。

### 9.3 Server -> Client

`started` 增加：

    {
      "type": "started",
      "session_id": "...",
      "run_id": "...",
      "prompt": "...",
      "mode": "execute"
    }

审批请求：

    {
      "type": "tool_approval_required",
      "session_id": "...",
      "run_id": "...",
      "approval_id": "...",
      "tool_call_id": "...",
      "tool": "exec_command",
      "risk": "command",
      "reason": "command_requires_approval",
      "summary": "Run a PowerShell command",
      "preview": {
        "shell": "powershell",
        "cwd": "D:/workspace/projects/automata",
        "command": "uv run --directory api pytest"
      },
      "options": ["allow_once", "deny"]
    }

审批已处理：

    {
      "type": "tool_approval_resolved",
      "run_id": "...",
      "approval_id": "...",
      "decision": "allow_once"
    }

取消：

    { "type": "run_cancel_requested", "run_id": "..." }

    {
      "type": "run_cancelled",
      "session_id": "...",
      "run_id": "...",
      "message": "Run cancelled by user."
    }

忙碌：

    {
      "type": "run_error",
      "code": "session_busy",
      "session_id": "...",
      "active_run_id": "..."
    }

所有 run 内事件逐步增加 `run_id`。第一阶段前端仍可兼容没有 `run_id` 的旧测试 fixture，但新后端必须发送。

### 9.4 AgentConnection

建议新增 `services/connection.py`：

    class AgentConnection:
        websocket: WebSocket
        sender: SerializedWebSocketSender
        approval_broker: ApprovalBroker
        active_run: ActiveRun | None

receive loop 只负责协议分发，不直接执行完整 reply：

    while True:
        payload = await receive_payload(websocket)
        if payload.type == "prompt":
            start_run_task(...)
        elif payload.type == "approve_plan":
            start_approved_plan_task(...)
        elif payload.type == "tool_approval_response":
            approval_broker.resolve(...)
        elif payload.type == "cancel_run":
            active_runs.cancel(...)

由于 run task 和 receive loop 都可能发送消息，所有 `send_json` 必须经过一个 `asyncio.Lock`，避免并发写 WebSocket。

### 9.5 Session lease

新增进程内 `ActiveRunRegistry`：

    session_id -> ActiveRun(
        run_id,
        owner_connection_id,
        task,
        cancellation,
        process_handles,
    )

规则：

- 同一 session 只能登记一个 active run；
- 同一 connection 也只允许一个 active run；
- 新 prompt 遇到 active run 返回 `session_busy`；
- cancel 只允许 owner connection；
- disconnect 取消 owner 的 active run；
- unregister 使用 `finally`，并校验 run id，避免旧 task 清掉新 run；
- 取消为幂等操作。

这只是运行期 lease，不写数据库。

## 10. API 隔离

### 10.1 Token 生命周期

修改 `ui/src-tauri/src/lib.rs`：

1. setup 时使用 OS CSPRNG 生成 32 字节 token；
2. token 保存在 `BackendState`；
3. 启动 sidecar 时设置 `AUTOMATA_API_TOKEN`；
4. `api_config` 返回 `apiToken`；
5. stop sidecar 时清除内存引用。

不得：

- 把 token 写进项目 `.env`；
- 在 stdout/stderr 打印 token；
- 把 token作为 URL query 参数；
- 在 FastAPI error 中回显；
- 把 token传给模型或 tool arguments。

### 10.2 FastAPI HTTP

新增 `automata_api/security.py`：

- 读取并校验 `AUTOMATA_API_TOKEN`；
- 使用 `secrets.compare_digest`；
- 提供 HTTP dependency/middleware；
- 提供 WebSocket authenticate helper；
- 提供 Origin allowlist；
- 对错误统一返回 401/403，不区分 token 是否存在。

HTTP client 在 `ui/src/api/client.ts` 自动加：

    Authorization: Bearer <apiToken>

`deleteSession()` 当前直接调用 `fetch`，也必须改为复用统一 client，避免漏 header。

`/health` 可以保留无认证，但只能返回 `status=ok` 和不敏感版本信息；模型地址、workspace、session、MCP 和 skills 信息都必须受保护。

### 10.3 WebSocket

WebSocket 握手按以下顺序处理：

1. 在 `accept()` 前读取并校验 Origin；
2. Origin 合法后才接受连接；
3. 在 3 秒内等待 `authenticate`；
4. 常量时间比较 token；
5. 失败使用应用 close code 4401；
6. 成功后才发送 `ready`。

未认证连接不得创建 session、读取消息、发 prompt、批准工具或停止其他 run。

不把 token放在 WebSocket URL 中，避免代理、错误报告或日志记录完整 URL。

### 10.4 Loopback 和 CORS

MVP 默认拒绝非 loopback `AUTOMATA_API_HOST`。如果配置为 `0.0.0.0`、局域网地址或公网地址，sidecar 启动失败并给出明确错误。

CORS 从宽泛配置收紧为：

- 显式 Tauri production origin；
- Vite dev 的 `localhost:1420` 和 `127.0.0.1:1420`；
- 明确 methods；
- headers 只允许 `Authorization`、`Content-Type`；
- 不使用 cookie，`allow_credentials=False`。

Tauri production origin 必须在目标 Windows 构建中实测后写入 allowlist，不能凭假定放行 `*`。

### 10.5 CSP

`tauri.conf.json` 当前 `csp=null`。本阶段设置最小 CSP：

- `default-src 'self'`；
- script 只允许 app 自身；
- connect 只允许 Tauri IPC、当前 loopback HTTP/WS；
- image 允许 `self` 和必要的 `data:`；
- 禁止任意远程 frame/object；
- style 如当前 CSS 构建确有需要，可暂时保留最小 `unsafe-inline` 并记录。

完成后必须在 dev 和 release 模式验证 Tauri IPC、目录选择和 sidecar WebSocket。

### 10.6 Headless 模式

`run.ps1 headless` 不能绕过认证。

建议：

- CI/自动化显式设置 `AUTOMATA_API_TOKEN`；
- 交互式 headless 若未设置 token，由 `run.ps1` 生成临时 token 文件；
- 文件只授予当前用户读取权限；
- runner 只打印文件路径，不打印 token；
- 后端退出时删除临时文件。

如果第一阶段不实现安全临时文件，则 headless 模式直接要求调用方提供 `AUTOMATA_API_TOKEN`，不要静默退回无认证。

## 11. 停止任务

### 11.1 取消覆盖范围

`cancel_run` 必须在以下状态均有效：

- 等待 LLM response；
- 正在接收 provider SSE；
- 等待 tool approval；
- 正在执行文件工具；
- 正在执行本地命令；
- 正在调用 MCP tool；
- 正在上下文压缩；
- 正在发送最终 token。

文件写入或 patch 如果已经完成，取消不能回滚已完成副作用；UI 要显示“已完成的动作不会自动撤销”。后续真实 diff inspector 负责让用户审查结果。

### 11.2 Runtime 传播

`CancellationToken` 至少提供：

    is_cancelled()
    raise_if_cancelled()
    wait()

关键边界都调用 `raise_if_cancelled()`：

- 模型调用前；
- 每个模型 step 开始；
- tool call 审批前后；
- executor 调用前；
- tool result 写入 provider context 前；
- context compression 前；
- 最终 message 保存前。

实际取消仍通过 `asyncio.Task.cancel()` 及时打断网络和 subprocess await；token 用于组件间显式判断和测试。

### 11.3 终态

取消使用专用 `RunCancelledError` 或直接传播 `asyncio.CancelledError`，由 service 最外层统一转换。

规则：

- 不作为普通 `error`；
- 不保存“Backend error” agent message；
- 不发送 `done`；
- 只发送一次 `run_cancelled`；
- 已经持久化的 user/tool message 保留；
- pending tool card 更新为 cancelled；
- approved plan 暂时保持 approved，Plan 重试属于后续开发方向。

## 12. ProcessSupervisor

### 12.1 集中进程启动

当前进程创建分散在：

- `_core.run_exec_command()`；
- `_core.run_bash()`；
- `LocalBackend._run_process()`；
- MCP SDK stdio transport。

新增 `agent/execution/process.py`，统一本地 shell/process 的启动、注册、timeout 和清理：

    class ProcessSupervisor:
        async def run(
            self,
            *,
            run_id: str,
            tool_call_id: str,
            argv: list[str],
            cwd: str,
            timeout_seconds: float,
            stdout_limit: int,
            stderr_limit: int,
        ) -> CapturedProcessOutput

所有 builtin 命令路径必须迁移到 supervisor，`capture_process_output` 不再直接拥有 `process.kill()` 策略。

### 12.2 Process handle

    @dataclass
    class ManagedProcess:
        run_id: str
        tool_call_id: str
        process: asyncio.subprocess.Process
        platform_group: ProcessGroupHandle
        state: Literal["running", "terminating", "exited"]

ProcessSupervisor 维护：

    run_id -> {tool_call_id -> ManagedProcess}

注册时机：

1. 创建子进程；
2. 立即建立平台 process group/job；
3. 注册到 supervisor；
4. 才开始等待输出。

任何异常路径都在 `finally` 中 unregister。

### 12.3 Windows

目标实现使用 Windows Job Object：

- Job 设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`；
- 创建进程后立即把 pid 关联到当前 run 的 Job；
- run cancel 或 sidecar shutdown 时关闭 Job handle；
- 等待短 grace period 后仍未退出则强制 terminate；
- 如果 Job assignment 因宿主 job 限制失败，明确记录并使用受测的 tree-kill fallback；
- 不能只依赖 `CREATE_NEW_PROCESS_GROUP`，它不保证杀死所有后代进程。

建议使用一个小型 `windows_job.py` 封装 ctypes，避免仅为 Job Object 引入完整 pywin32。实现必须只使用公开 Win32 API，不读取 asyncio 私有 transport 字段。

如果无法在 `asyncio.create_subprocess_exec` 后可靠关联 Job，应把 Windows spawn 封装下沉到独立 adapter；不要用不稳定的 `process._transport` 作为正式实现。

### 12.4 POSIX

POSIX 启动使用独立 session/process group：

- spawn 时 `start_new_session=True`；
- cancel 时先 `SIGTERM` 整个 process group；
- grace period 后 `SIGKILL`；
- 对 ProcessLookupError 幂等处理；
- parent 已退出但 child 仍在时仍按 pgid 清理。

### 12.5 Timeout 与 cancellation 共用终止路径

timeout、用户 cancel、WebSocket disconnect 和 sidecar shutdown 都调用：

    terminate_tree(reason, grace_seconds)

不保留四套 kill 逻辑。

`capture_process_output` 在 `CancelledError` 下必须：

1. 调用 process supervisor；
2. 继续 drain/cancel stdout、stderr reader；
3. await process exit；
4. 重新抛出 cancellation。

这样既不留下孤儿进程，也不留下未回收 reader task。

### 12.6 MCP

MCP 分两类：

- Streamable HTTP：取消 task，关闭 HTTP stream/session；请求已发送后的远端副作用可能是 unknown outcome。
- stdio：取消必须进入 `McpConnectionManager.close_all()`，确保 SDK transport 退出。

第一阶段至少用 fake stdio server 证明：

- waiting call 被取消；
- adapter `aclose()` 执行；
- MCP server 进程退出；
- run 终态为 cancelled。

如果官方 SDK 的 stdio launcher 无法纳入 `ProcessSupervisor`，必须把“SDK server 本身已退出”和“server 创建的孙进程是否保证退出”分别记录。不能把只关闭 pipe 描述成已经完成完整 process-tree guarantee。

## 13. 前端设计

### 13.1 API config

`ApiRuntimeConfig` 增加：

    apiToken: string

`requestJson` 自动添加 Bearer header。所有直接 `fetch` 调用收敛到该 helper。

WebSocket `open` 后第一时间发送 authenticate；收到 ready 前 composer 保持 disabled。

### 13.2 Socket payload

`SocketPayload` 增加：

- `tool_approval_required`；
- `tool_approval_resolved`；
- `approval_error`；
- `run_cancel_requested`；
- `run_cancelled`；
- `run_error`；
- 所有现有 run event 的可选/必选 `run_id`。

必须增加 runtime shape validation，不能继续只用 `JSON.parse(...) as SocketPayload` 信任任意对象。第一版可以写轻量 type guard，不必立即引入 schema library。

### 13.3 Approval UI

新增 `ToolApprovalCard`，展示：

- 工具名和风险标签；
- 操作摘要；
- workspace/cwd；
- 文件路径或 command preview；
- MCP server/tool identity；
- Allow once；
- 后端明确允许时才显示 Allow for this run；
- Deny。

交互要求：

- pending 时阻止重复提交；
- response 后立刻显示 resolving；
- 后端 `tool_approval_resolved` 才进入最终状态；
- deny 不停止整个 run；
- 卡片不把完整 arguments 永久保存在浏览器日志；
- command/patch preview 有大小上限和展开按钮。

### 13.4 Stop button

Composer 发送按钮在 streaming 时切换为 Stop：

- 点击发送 `cancel_run`；
- 进入 `Cancelling...`；
- 在 `run_cancelled` 或终态前保持 disabled，避免重复 prompt；
- cancel 超时显示“取消尚未确认”，但不能擅自把 run 标成已停止；
- 新连接不能取消不属于自己的 run。

### 13.5 Reducer

Chat state 增加：

    activeRunId: string | null
    runStatus: "idle" | "running" | "waiting_approval" | "cancelling"
    approvals: Record<string, ApprovalViewState>

run id 必须用于过滤迟到事件。旧 run 的 token/tool result 到达时不能污染新 run UI。

## 14. 后端文件改动建议

新增：

- `api/automata_api/security.py`
- `api/automata_api/agent/execution/__init__.py`
- `api/automata_api/agent/execution/model.py`
- `api/automata_api/agent/execution/policy.py`
- `api/automata_api/agent/execution/approval.py`
- `api/automata_api/agent/execution/orchestrator.py`
- `api/automata_api/agent/execution/runs.py`
- `api/automata_api/agent/execution/process.py`
- `api/automata_api/agent/execution/windows_job.py`
- `api/automata_api/services/connection.py`

修改：

- `config.py`：token、approval timeout、loopback validation；
- `main.py`：auth/CORS；
- `routers/chat.py`：持续 receive loop；
- `services/chat.py`：run task 生命周期和取消终态；
- `agent/runtime.py`：使用 orchestrator 和 cancellation；
- `agent/types.py`：approval/cancel events；
- `agent/tools/model.py`：risk/policy；
- `agent/tools/providers.py`：内建风险 metadata；
- `agent/tools/router.py`：resolve 与 authorized execution 边界；
- `agent/tools/mcp_provider.py`、`mcp_tool.py`：通用审批接入；
- `agent/tools/_core.py`、`backends/local.py`：统一 ProcessSupervisor；
- `ui/src-tauri/src/lib.rs`：token 生成和 sidecar env；
- `ui/src-tauri/Cargo.toml`：CSPRNG 所需最小依赖；
- `ui/src/api/client.ts`、`config.ts`、`websocket.ts`；
- `ui/src/types/api.ts`、`socket.ts`、`chat.ts`；
- `ui/src/hooks/useAgentSocket.ts`；
- composer、tool card 和 reducer；
- `run.ps1`、`api/.env.example`、`api/README.md`。

## 15. 错误码

API/auth：

- `api_token_missing`
- `api_token_invalid`
- `origin_not_allowed`
- `remote_bind_not_allowed`

Run：

- `session_busy`
- `run_not_found`
- `run_owner_mismatch`
- `run_already_finished`
- `run_cancel_timeout`

Approval：

- `approval_not_found`
- `approval_expired`
- `approval_already_resolved`
- `approval_run_mismatch`
- `tool_approval_denied`
- `tool_approval_cancelled`

Process：

- `process_start_failed`
- `process_tree_setup_failed`
- `process_termination_failed`
- `command_timed_out`

模型可见结果只包含必要错误；内部平台错误详情进入 redacted log。

## 16. 测试方案

### 16.1 Policy 单元测试

- read 工具直接 allow；
- write/command/destructive 默认 prompt；
- Plan mode mutating 永远 deny；
- approval 不能覆盖 hidden/deferred/Plan deny；
- apply patch delete 升级 destructive；
- skill/descriptor 文本不能修改 risk；
- MCP deny 不能被 approval 覆盖；
- MCP prompt 在批准后只调用一次 server。

### 16.2 ApprovalBroker 单元测试

- allow_once、allow_for_run、deny；
- 重复 response；
- 错误 run id；
- timeout；
- cancel 结束 pending future；
- scoped grant 不跨 run/workspace；
- destructive 没有宽泛 allow_for_run。

### 16.3 WebSocket 集成测试

- 未认证连接无法收到 session 数据；
- 正确认证后收到 ready；
- 错误 token 使用 4401 关闭；
- 非允许 Origin 被拒绝；
- prompt 触发 command approval；
- 后端等待审批时仍能收到 approval response；
- 等待审批时 cancel；
- LLM streaming 时 cancel；
- session busy；
- disconnect 自动取消；
- 迟到 approval/token 不影响新 run。

### 16.4 Process 测试

Windows fixture 启动：

    parent shell -> child Python -> grandchild long-running Python

验证：

- 正常完成全部退出；
- timeout 后三层进程都不存在；
- cancel 后三层进程都不存在；
- WebSocket disconnect 后三层进程都不存在；
- sidecar shutdown 后三层进程都不存在；
- stdout/stderr 已满时取消不死锁；
- output truncation 原有语义不回归。

POSIX 使用 process group fixture 覆盖相同语义。

### 16.5 API 隔离测试

- HTTP 无 token/错误 token/正确 token；
- token compare 不记录 secret；
- CORS allowlist；
- 非 loopback bind 启动失败；
- health 不泄露 model endpoint、workspace 或 token；
- Tauri config 返回 token，但 sidecar stdout 不含 token；
- CSP 下 HTTP、WS、Tauri IPC 仍工作。

### 16.6 回归

- 现有 backend 全量 pytest；
- UI TypeScript build；
- Cargo check；
- 普通文本 reply；
- read-only tool loop；
- Plan mode；
- approve plan；
- MCP stdio/Streamable HTTP；
- skills 注入；
- context compression；
- bounded command output。

## 17. 分阶段实施

### S0：协议和基线

状态：`DONE`

- 固定事件 schema、error code 和风险映射；
- 为当前 WebSocket、timeout、disconnect 建立基线测试；
- 记录现有 221 个后端测试结果；
- 确认 Windows 目标平台的真实 Tauri Origin。

退出条件：协议 fixture 和风险表评审完成。

### S1：API 隔离

状态：`DONE`

- Tauri token 生成；
- sidecar env 注入；
- HTTP Bearer auth；
- WebSocket authenticate；
- loopback validation；
- CORS/CSP 收紧；
- 前端 client 统一 header。

退出条件：无 token 的 HTTP/WS 均不能操作 Agent，desktop dev/build 可连接。

### S2：并发连接与临时 Run

状态：`DONE`

- AgentConnection receive loop；
- SerializedWebSocketSender；
- ActiveRunRegistry；
- `run_id`；
- session lease；
- run task 终态和 disconnect cleanup。

退出条件：run 执行期间连接仍能处理控制消息，同一 session 不能并发运行。

### S3：通用工具审批

状态：`DONE`

- ToolDescriptor risk；
- policy engine；
- orchestrator；
- ApprovalBroker；
- MCP prompt policy 接入；
- ApprovalCard 和 reducer。

退出条件：所有 Agent runtime 工具路径都无法绕过 orchestrator，deny/allow/cancel 行为稳定。

### S4：取消与进程树

状态：`DONE`

- CancellationToken；
- Stop UI；
- ProcessSupervisor；
- Windows Job Object；
- POSIX process group；
- timeout/disconnect/shutdown 共用终止路径；
- MCP cancel cleanup。

退出条件：受测 parent/child/grandchild 在所有终止路径均退出，没有 orphan process。

### S5：文档与完整回归

状态：`DONE`

- 更新 README、env example 和协议说明；
- 全量 backend tests；
- UI build；
- Cargo check；
- desktop release smoke；
- 手工验证 approval、deny、stop 和 app close。

退出条件：满足下节所有验收标准。

实施验证（2026-07-15）：backend `238 passed`，UI production build、
`cargo fmt --check`、`cargo check` 和 Tauri `--no-bundle` release build 通过；
release smoke 已验证关闭窗口后 desktop、PyInstaller launcher、API worker 和
8765 listener 全部退出。

## 18. 验收标准

1. 未认证客户端不能访问 session、messages、MCP、skills 或 chat。
2. Desktop 默认只监听 loopback。
3. Read tool 无额外点击即可执行。
4. Write、command、destructive 工具按策略弹出审批。
5. Plan mode 写工具即使用户构造 approval response 也不能执行。
6. MCP prompt policy 可以通过统一 UI 批准，deny policy 不可覆盖。
7. 用户可以在等待模型、审批或命令时停止 run。
8. cancel 后不再收到该 run 的 token 或 tool result。
9. timeout、cancel、disconnect、app close 都不会留下受测子进程树。
10. 同一 session 并发 prompt 返回 `session_busy`。
11. token 不出现在 URL、数据库、普通日志和模型上下文。
12. 现有测试全部通过，并新增 auth、approval、cancel、process-tree 覆盖。

## 19. 风险与处理

### WebSocket 并发发送

run task 和 receive loop 都可能回复。统一使用 sender lock，禁止直接散落 `websocket.send_json`。

### 用户批准后参数被替换

ApprovalRequest 保存 canonical arguments hash；执行前重新计算。不一致则废弃 approval 并重新请求，防止 TOCTOU。

### Shell command 无法可靠静态分析

所有 shell command 默认 prompt。关键词分析只影响提示，不产生 allow。

### Process 已产生部分副作用

取消只停止未来执行，不能回滚已完成写入。UI 明确说明，后续真实 diff/run inspector 提供审查。

### MCP 远端结果不确定

请求发送后取消或网络断开时标记 `mcp_call_outcome_unknown`，不得自动重试 mutating tool。

### Job Object 兼容性

用真实 Windows release sidecar 测试，而不只测试 Python 开发进程。Job assignment 失败必须 fail closed 或明确降级，不能静默宣称树清理成功。

### Token 进入 renderer 内存

Renderer 必须有 CSP、禁止远程页面和任意脚本；token 只作为 sidecar capability。未来如引入远程内容或插件 WebView，需要重新评估隔离层。

## 20. 后续开发方向

1. 增加持久化 Run 状态、断线恢复、Plan 失败重试和安全数据库迁移。
2. 补模型设置、MCP/Skills 管理、Markdown、真实 diff/run inspector。
3. 最后补前端测试、CI 和完整桌面打包 smoke test。
