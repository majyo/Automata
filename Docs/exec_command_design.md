# exec_command 设计文档

本文档说明 `exec_command` 工具及其配套 `write_stdin` 工具的设计。它面向实现者，适合用于在其他运行时中复刻该工具，或在当前实现中做较大改动时作为参考。

## 目标

`exec_command` 允许模型在选定的执行环境中运行命令。它既支持短生命周期的一次性命令，也支持长期运行的交互式进程。

该工具围绕以下需求设计：

- 运行本地或远程命令。
- 支持普通 pipe 执行和 PTY 执行。
- 将输出流式发送给客户端，同时向模型返回有界的响应。
- 对仍在运行的命令返回 session id。
- 允许后续通过 `write_stdin` 与运行中的进程继续交互。
- 将审批、沙箱和网络策略集中在 handler 之外处理。
- 将进程生命周期管理与面向模型的工具协议解耦。
- 对所有保留的输出设置硬上限。

最重要的设计分工是：

- Handler 负责工具形状和请求规范化。
- Orchestrator 负责策略决策。
- Runtime 负责准备可在沙箱中执行的请求。
- Process manager 负责进程生命周期。
- Event layer 负责 UI 可见的进度事件。
- Tool output 负责模型可见的响应格式。

## 对外工具形状

模型可见的工具名是：

```text
exec_command
```

核心输入字段如下：

```json
{
  "cmd": "string",
  "workdir": "string",
  "shell": "string",
  "login": true,
  "tty": false,
  "yield_time_ms": 10000,
  "max_output_tokens": 10000,
  "environment_id": "string",
  "sandbox_permissions": "use_default",
  "additional_permissions": {
    "network": { "enabled": true },
    "file_system": {
      "read": ["absolute path"],
      "write": ["absolute path"]
    }
  },
  "justification": "string",
  "prefix_rule": ["git", "pull"]
}
```

只有 `cmd` 是必填字段。其他字段要么按条件暴露，要么作为可选字段处理。

字段说明：

- `cmd`：要执行的 shell 脚本。
- `workdir`：工作目录。相对路径基于所选执行环境的 cwd 解析，而不是基于宿主进程 cwd。
- `shell`：可选 shell binary。在本地 zsh-fork 模式下会隐藏。
- `login`：可选 login shell 开关。只有配置允许 login shell 时才会暴露。
- `tty`：为 true 时分配 PTY；为 false 时使用 pipe。
- `yield_time_ms`：初次向模型返回输出前等待的时间。
- `max_output_tokens`：本次模型可见响应的输出预算。
- `environment_id`：多环境场景下选择非主环境。
- `sandbox_permissions`：单条命令的沙箱覆盖选项。
- `additional_permissions`：为该命令请求的沙箱内文件系统或网络权限。
- `justification`：展示给用户的审批理由。
- `prefix_rule`：可复用的审批前缀，例如 `["git", "pull"]`。

在 code mode 中，响应是结构化对象；在普通模型上下文中，响应是格式化后的 function-call output。概念上它包含：

```json
{
  "chunk_id": "string",
  "wall_time_seconds": 0.25,
  "exit_code": 0,
  "session_id": 1234,
  "original_token_count": 1200,
  "output": "..."
}
```

当进程已经结束时会出现 `exit_code`。当进程仍在运行、可通过 `write_stdin` 继续交互时会出现 `session_id`。

## 配套工具：write_stdin

`write_stdin` 用于继续一个已有的 `exec_command` session。

输入：

```json
{
  "session_id": 1234,
  "chars": "input to write",
  "yield_time_ms": 250,
  "max_output_tokens": 10000
}
```

行为：

- `chars` 非空时，写入进程 stdin。
- `chars` 为空时，表示轮询更多输出。
- 非空写入要求原进程以 `tty: true` 启动。
- 响应使用与 `exec_command` 相同的输出形状。
- 如果一次轮询或写入观察到进程结束，响应会包含 `exit_code`，并省略 `session_id`。

## 高层流程

正常启动流程如下：

```text
模型调用 exec_command
  -> ExecCommandHandler 解析工具 payload
  -> 解析选定 environment 和 cwd
  -> 将 cmd 转成 argv
  -> 规范化权限
  -> 运行特殊命令拦截，例如 apply_patch
  -> 将 ExecCommandRequest 发送给 UnifiedExecProcessManager
  -> ToolOrchestrator 处理审批、沙箱和网络策略
  -> UnifiedExecRuntime 准备一个沙箱感知的 ExecRequest
  -> UnifiedExecProcessManager 启动本地或远程进程
  -> 发出 ExecCommandBegin
  -> 开始流式输出
  -> 收集初始输出窗口
  -> 进程要么作为 live session 存储，要么完成
  -> 向模型返回 ExecCommandToolOutput
```

## 主要组件

### 工具规格

工具 schema 由 `shell_spec.rs` 构造。

spec 层决定哪些参数对模型可见：

- `environment_id` 只在多环境模式下包含。
- 当本地 zsh-fork 模式不支持 `shell` 参数时隐藏 `shell`。
- `login` 只在允许 login shell 时包含。
- 开启 exec permission approvals 时包含 `additional_permissions`。

spec 还声明输出 schema。实现必须保持 schema 和实际工具响应字段一致。

### ExecCommandHandler

handler 是模型工具入口。

职责：

- 匹配 `exec_command` 工具名。
- 解析 function-call arguments。
- 选择请求的执行环境。
- 解析 cwd。
- 在相关场景下发出隐式 skill invocation 元数据。
- 分配 process id。
- 将 shell 命令解析为 argv。
- 规范化单命令权限请求。
- 尽早拒绝不可能满足的权限请求。
- 拦截应由其他内部路径处理的命令。
- 构造 `ExecCommandRequest`。
- 调用 `UnifiedExecProcessManager::exec_command`。
- 将执行错误转换为模型可见的 function-call error。

handler 应保持轻量。它不应该直接启动进程，也不应该实现审批策略。

### 命令解析

handler 接收的 `cmd` 是字符串，但执行层使用 argv。

解析后的命令应包含：

```rust
struct ResolvedCommand {
    command: Vec<String>,
    shell_type: ShellType,
}
```

Direct shell 模式：

```text
selected shell + cmd -> [shell_path, "-c" or "-lc", cmd]
```

Zsh-fork shell 模式：

```text
zsh path + cmd -> [zsh, "-c" or "-lc", cmd]
```

规则：

- 除非配置允许 login shell，否则拒绝 `login: true`。
- 远程环境使用 direct shell mode。
- 本地 zsh-fork 模式拒绝 `shell` 参数。
- 展示用字符串应从 argv 通过 shell-style joining 得到。

### 权限和审批输入

handler 通过以下字段接收用户或模型意图：

- `sandbox_permissions`
- `additional_permissions`
- `justification`
- `prefix_rule`

实现应当：

- 应用当前 turn 中已经授予的权限。
- 区分已预批准的 additional permissions 和新的审批请求。
- 在当前 approval policy 无法批准 sandbox override 时尽早拒绝。
- 规范化并校验请求的 additional permissions。
- 即使命令 cwd 受模型控制，也要保留一个锚定在所选 environment 上的可信 sandbox cwd。

### ToolOrchestrator

orchestrator 为 shell-like 工具集中处理策略。

职责：

- 判断是否需要审批。
- 运行 permission request hooks。
- 将审批路由到 guardian 或直接用户审批。
- 对等价请求缓存审批结果。
- 选择初始 sandbox。
- 开始和完成网络审批。
- 在策略允许时，于 sandbox denial 后重试。
- 当重试可以复用早先决定时，避免重复审批提示。

orchestrator 接收 runtime 和 request。它不应该知道如何启动进程。

### UnifiedExecRuntime

runtime 将策略决策桥接到进程启动。

职责：

- 将高层 request 转换为 sandbox command。
- 应用依赖 sandbox 的环境变量改动。
- 应用 managed network 环境变量改动。
- 在需要时应用 shell snapshot 包装。
- 在可用时应用 zsh-fork 准备逻辑。
- 在 Windows elevated sandbox 要求的场景下禁用 PowerShell profile。
- 调用 process manager 的 `open_session_with_exec_env`。

runtime 是真正创建进程前最后一个感知策略的层。

### UnifiedExecProcessManager

process manager 拥有 process id、live session 存储、输出收集和 `write_stdin`。

它维护类似如下的 store：

```rust
struct ProcessStore {
    processes: HashMap<i32, ProcessEntry>,
    reserved_process_ids: HashSet<i32>,
}

struct ProcessEntry {
    process: Arc<UnifiedExecProcess>,
    call_id: String,
    process_id: i32,
    hook_command: String,
    tty: bool,
    network_approval: Option<DeferredNetworkApproval>,
    last_used: Instant,
}
```

必需行为：

- 启动前分配 process id。
- 启动失败或短命令完成时释放 id。
- 在等待初始输出前存储 live process，避免 turn 被中断时丢掉最后一个 process 引用。
- 返回响应前刷新进程状态。
- 从 store 中移除已退出进程。
- 当 live process 达到上限时裁剪旧进程。
- 关闭时终止所有存储的进程。

当前实现的最大进程数为 64。

### UnifiedExecProcess

`UnifiedExecProcess` 封装不同 transport 的进程句柄：

- 本地 PTY 进程。
- 本地 pipe 进程。
- 远程 exec-server 进程。

它暴露：

- `write(bytes)`
- `terminate()`
- `has_exited()`
- `exit_code()`
- `failure_message()`
- 用于收集输出的 output handles
- 用于流式输出的 output receiver
- 用于退出通知的 cancellation token

该封装还会在启动和早期退出后检查疑似 sandbox denial 的失败。

## 进程启动模式

process manager 最终会启动以下类型之一：

- Windows restricted token/elevated sandbox process。
- 远程 exec-server process。
- `tty` 为 true 时的本地 PTY process。
- `tty` 为 false 时的本地 pipe process。

PTY 模式是交互式 stdin 所必需的。pipe 模式更适合简单的非交互命令，因为 stdin 可以保持关闭。

## 输出模型

输出有两个消费者：

1. UI/client events。
2. 模型可见 tool response。

这两条路径是有意分离的。

### UI 事件

event layer 发出：

- `ExecCommandBegin`
- `ExecCommandOutputDelta`
- `TerminalInteraction`
- `ExecCommandEnd`

`ExecCommandBegin` 包含：

- tool call id
- 可用时的 process id
- command argv
- cwd
- parsed command
- source

`ExecCommandOutputDelta` 包含：

- call id
- stream
- raw bytes

`ExecCommandEnd` 包含：

- command argv
- cwd
- stdout
- stderr
- aggregated output
- exit code
- duration
- formatted output
- status

### 流式输出

进程启动后，后台 task 会订阅进程输出。

它应当：

- 将字节追加到共享 transcript。
- 尽量在合法 UTF-8 边界切分并发出 delta。
- 限制单个 delta 的大小。
- 进程退出后短暂继续读取，以捕获尾部输出。
- 输出 drain 后通知 exit watcher。

当前实现将单个 delta 限制为 8192 bytes。

### 模型响应

对于模型可见响应，process manager 会根据 `yield_time_ms` 对应的 deadline 收集输出。

收集循环会：

- drain 当前可用的输出 chunks。
- 等待新输出、进程退出、输出关闭或 deadline。
- 当 out-of-band elicitation 暂停时扩展 deadline。
- 进程退出后，额外等待一个很小的输出关闭窗口。

响应包含：

- chunk id
- wall time
- optional exit code
- optional session id
- original token count
- output text

模型输出应按以下两者的较小值截断：

- 请求的 `max_output_tokens`
- 当前 turn 的 truncation policy budget

### Head-Tail Buffer

长进程输出必须有界。

head-tail buffer 会保留：

- 输出开头的稳定前缀
- 输出结尾的稳定后缀

超过上限时，中间部分的字节会被丢弃。

这样既能避免无限内存增长，又能保留终端输出中最有价值的两个区域：启动上下文和最近的失败/进展信息。

当前保留输出上限为 1 MiB。

## 短命令与长命令

启动后：

```text
if process is still alive:
  store process
  return session_id
else:
  emit ExecCommandEnd
  release process id
  return exit_code
```

对于一开始被存储、但在初始等待窗口中退出的进程，process manager 会在返回前刷新状态并将其从 store 中移除。

这个区别非常关键，因为模型会根据是否存在 `session_id` 来决定是否调用 `write_stdin`。

## write_stdin 流程

`write_stdin` 流程如下：

```text
模型调用 write_stdin(session_id, chars)
  -> handler 解析 args
  -> process manager 准备 process handles
  -> 如果 chars 非空，写入 stdin
  -> 收集输出直到 deadline
  -> 刷新进程状态
  -> 仍存活则返回 session_id，否则返回 exit_code
  -> 在合适时发出 TerminalInteraction
```

重要规则：

- 空 stdin 被视作轮询。
- 非空 stdin 是可见的 terminal interaction。
- poll 只有在仍有 live process 可等待时才应展示。
- 如果一次 `write_stdin` 调用观察到最终完成，它应与最初的 `exec_command` call id 关联，以便 post-tool hooks 正确运行。

## Hooks 机制

`exec_command` 参与 Bash 风格的 pre/post tool hooks。

Pre-tool hook：

- 使用原始 `cmd` 字符串。
- 发出 Bash `PreToolUse` payload。
- 允许 hook input rewrite 更新 `cmd` 参数。

Post-tool hook：

- 只在命令完成且有最终输出时发出。
- 对仍在运行的 session 跳过。
- 后续 `write_stdin` poll 如果观察到原命令完成，可以发出该原命令对应的 post-tool hook。

`write_stdin` 不发 pre-tool hook，因为它只是已有命令的 transport。

## 错误处理

错误应转换为有用的模型可见消息和一致的 UI events。

常见类别：

- 参数错误：返回给模型，让模型用修正后的参数重试。
- 不支持的 payload：返回清晰的 handler error。
- 审批拒绝：报告 declined/rejected，并发出终止状态。
- Sandbox denial：保留输出；只有策略允许时才重试。
- Network denial：终止或标记进程失败，并展示网络拒绝消息。
- 进程启动失败：释放预留的 process id。
- Stdin 失败：先刷新进程状态，再判断进程是否实际已经退出。

失败路径应避免留下没有 end event 的 active UI command。

## 安全和策略要点

实现应保持以下不变式：

- 受模型控制的 cwd 不能作为 sandbox policy anchor。
- approval policy 决定是否可以进行 sandbox escalation。
- 已授予的权限可以自动应用，但新的权限请求仍必须校验。
- 禁止无沙箱执行的策略下，不得进行 unsandboxed retry。
- 网络审批可以 deferred，因为进程可能先启动，之后才真正尝试访问网络。
- 绕过 sandbox 或 managed network 时应调整环境变量，避免将 proxy state 泄漏给无沙箱命令。

## 最小复刻计划

可以分阶段构建一个最小克隆。

阶段 1：一次性执行

- 定义包含 `cmd`、`workdir`、`yield_time_ms` 和 `max_output_tokens` 的 `exec_command` schema。
- 解析 cwd。
- 运行进程。
- 捕获输出。
- 返回 exit code 和 output。

阶段 2：流式事件

- 发出 begin、output delta 和 end events。
- 添加有界 output buffer。
- 将大输出拆成有上限的 deltas。

阶段 3：live sessions

- 分配 process ids。
- 存储 live processes。
- 返回 `session_id`。
- 添加 `write_stdin`。
- 添加进程裁剪和关闭清理。

阶段 4：审批和沙箱

- 引入 tool orchestrator。
- 添加 approval requirements。
- 添加 sandbox selection。
- 在策略允许时对 sandbox denial 进行 retry。

阶段 5：高级环境

- 添加多 environment。
- 添加远程 exec-server。
- 添加 shell snapshots 或 zsh-fork。
- 添加 managed network approval。
- 添加 hook integration。

## 参考伪代码

```rust
async fn exec_command(invocation: ToolInvocation) -> Result<ToolOutput, ToolError> {
    let env_args = parse_environment_args(&invocation.arguments)?;
    let environment = resolve_environment(env_args.environment_id)?;
    let cwd = resolve_cwd(&environment.cwd, env_args.workdir)?;

    let args = parse_exec_args_with_base_path(&invocation.arguments, &cwd)?;
    let process_id = process_manager.allocate_process_id().await;
    let command = resolve_command(&args, invocation.session.user_shell())?;

    let permissions = normalize_permissions(&args, &cwd, &invocation.turn)?;

    if let Some(output) = intercept_special_command(&command, &cwd).await? {
        process_manager.release_process_id(process_id).await;
        return Ok(output);
    }

    let request = ExecCommandRequest {
        command,
        process_id,
        cwd,
        environment,
        tty: args.tty,
        yield_time_ms: args.yield_time_ms,
        max_output_tokens: args.max_output_tokens,
        sandbox_permissions: permissions.sandbox_permissions,
        additional_permissions: permissions.additional_permissions,
        justification: args.justification,
        prefix_rule: args.prefix_rule,
    };

    process_manager.exec_command(request, &invocation.context()).await
}
```

```rust
async fn process_manager_exec_command(
    request: ExecCommandRequest,
    context: &UnifiedExecContext,
) -> Result<ExecCommandToolOutput, UnifiedExecError> {
    let process = open_session_with_sandbox(&request, context).await?;

    emit_exec_begin(&request, context).await;
    start_streaming_output(&process, context);

    let started_alive = !process.has_exited();
    if started_alive {
        store_process(&process, &request, context).await;
    }

    let output = collect_output_until_deadline(
        &process,
        Duration::from_millis(clamp_yield_time(request.yield_time_ms)),
    )
    .await;

    let status = refresh_process_state(request.process_id).await;
    match status {
        ProcessStatus::Alive { process_id, exit_code, .. } => {
            Ok(tool_output(output, Some(process_id), exit_code))
        }
        ProcessStatus::Exited { exit_code, .. } => {
            emit_exec_end(&request, &output, exit_code, context).await;
            release_process_id(request.process_id).await;
            Ok(tool_output(output, None, exit_code))
        }
        ProcessStatus::Unknown => Err(UnifiedExecError::UnknownProcessId {
            process_id: request.process_id,
        }),
    }
}
```

## 实现检查清单

- Tool schema 与实际 response 一致。
- Handler 只支持 function-call payload。
- 相对 workdir 基于所选 environment cwd 解析。
- 每个启动或校验失败路径都会释放 process id。
- 进程/session 保留的输出有上限。
- 成功和失败都会发出 UI events。
- 长时间运行的进程返回 `session_id`。
- 已完成进程返回 `exit_code`。
- `write_stdin` 会从 store 中移除已退出 session。
- 审批逻辑没有嵌入 handler。
- Sandbox cwd 是策略可信的，而不是模型控制的。
- Network denial 可以在启动后使进程失败。
- Post-tool hooks 只为已完成命令运行。
