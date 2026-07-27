# Automata Sandbox 系统实现设计

> - 文档状态：`PLANNED`
> - 实现状态：尚未开始
> - 目标平台顺序：Windows → Linux → macOS
> - 参考实现：`D:\workspace\projects\codex`，源码快照 `f2b725102b`
> - Automata 核对基线：当前工作区源码，Git HEAD `257b5ceb68`
> - 最后核对日期：2026-07-27

## 1. 结论

Automata 可以实现与 Codex 对等的本地命令沙箱，但不能通过给现有
`asyncio.create_subprocess_exec()` 增加一个参数完成。

Windows 上的生产级方案需要原生执行层：

1. 权限模型将“是否审批”和“是否应用沙箱”拆成两条独立轴；
2. Python 后端统一通过 `SandboxManager` 和 `ProcessLauncher` 启动子进程；
3. Rust 原生组件负责本地沙箱账户、ACL、restricted token、Job Object、网络规则和
   stdin/stdout/stderr 转发；
4. `Default` 在 managed sandbox 中执行，`Full Access` 明确不进入 sandbox；
5. 沙箱不可用、策略无法表达或 setup 不完整时必须 fail closed，不能静默回退到普通用户权限；
6. 只有识别为沙箱拒绝后，才允许通过新的显式审批发起一次 unsandboxed retry。

Automata 现有的审批、Run 权限快照、进程会话、取消、Job Object 和 Tauri sidecar 打包
可以继续复用，但它们当前都不构成 OS 级文件或网络隔离。

## 2. 文档目的

本文记录：

- Codex 当前 sandbox 的真实源码实现；
- Automata 当前已有能力和缺口；
- Automata 的目标权限模型、执行链路和平台边界；
- Windows 原生 sandbox 的组件设计；
- 文件工具、命令工具、长进程和 MCP 的接入方式；
- 数据迁移、错误协议、可观测性、实施顺序和验收矩阵。

本文是实施设计，不表示功能已经存在。功能完成并与代码、测试和发布包核对一致后，文档才可迁入
`Docs/Archived`。

## 3. 当前源码基线

### 3.1 Automata 已实现

| 能力 | 当前实现 |
|---|---|
| 权限预设 | `default`、`full_access` |
| 审批策略 | `on_request`、`never` |
| Plan 安全边界 | 非只读工具在 Plan mode 中直接拒绝 |
| Run 权限快照 | Run 创建时复制 Session 的 `permission_preset` |
| 命令执行 | `bash`、`powershell`、`exec_command` |
| 进程治理 | 超时、输出限制、取消、Windows Job Object、进程树终止 |
| 长进程 | `yield_time`、process session、`write_stdin` |
| 文件工具 | 应用层 workspace 路径检查 |
| MCP | server grant、tool call policy、stdio/HTTP transport |
| 打包 | Python API 作为 Tauri external binary |

主要代码入口：

- [`permissions.py`](../../api/automata_api/agent/execution/permissions.py)
- [`orchestrator.py`](../../api/automata_api/agent/execution/orchestrator.py)
- [`process.py`](../../api/automata_api/agent/execution/process.py)
- [`process_sessions.py`](../../api/automata_api/agent/execution/process_sessions.py)
- [`windows_job.py`](../../api/automata_api/agent/execution/windows_job.py)
- [`_core.py`](../../api/automata_api/agent/tools/_core.py)
- [`local.py`](../../api/automata_api/agent/backends/local.py)
- [`mcp/client.py`](../../api/automata_api/agent/mcp/client.py)

### 3.2 当前没有实现

当前 `RuntimePermissions` 只包含 `approval_policy`。`Full Access` 的效果是把符合条件的
`prompt` 决策变成 `allow`，并不切换任何 OS 安全上下文。

命令最终仍由 API 进程直接调用 `asyncio.create_subprocess_exec()`。`cwd` 被限制在 workspace
中，只能保证进程的起始目录，不能阻止命令使用绝对路径、`..`、子进程或系统 API 访问其他位置。

现有 Windows Job Object 只解决进程树生命周期：

- 主进程取消时终止子孙进程；
- Job handle 关闭时终止剩余进程；
- 不限制文件读取；
- 不限制文件写入；
- 不限制网络；
- 不改变访问令牌。

文件工具的 `Path.resolve() + relative_to(workspace)` 是必要的应用层校验，但不是完整安全边界。
它不能单独解决 reparse point、junction、hardlink、检查后替换和父进程真实用户权限问题。

## 4. Codex Sandbox 的真实实现

### 4.1 审批与沙箱相互独立

Codex 的运行时权限模型使用三种 enforcement：

```text
Managed
  Codex 负责构造和应用 sandbox。

Disabled
  不应用外层 sandbox。

External
  文件系统隔离由外部宿主提供。
```

`Full Access` 对应 `PermissionProfile::Disabled`，运行时文件系统策略为 unrestricted，网络为
enabled，因此 `SandboxManager` 选择 `SandboxType::None`。它不是“免审批但仍在沙箱内”。

### 4.2 Workspace-write 策略

Codex 内建 workspace-write 的默认策略为：

```text
:root            read
:project_roots   write
:tmp             write
/.git            protected
/.agents         protected
/.codex          protected
network          restricted
```

一个容易误解的事实是：该默认策略允许读取文件系统根，而不是只允许读取 workspace；它主要限制写入
范围和网络。更严格的 restricted-read、deny-read 和 glob deny 由扩展策略表达。

Automata 第一阶段若要求与 Codex 默认行为兼容，应先实现“全盘读、workspace/temp 写、元数据保护、
网络受限”。敏感文件 deny-read 属于生产发布门槛，但在能力分层上是下一层策略。

### 4.3 平台选择与命令变换

Codex 先把基础 profile 和单次工具调用的 additional permissions 合并，再选择平台 sandbox：

| 平台 | 后端 |
|---|---|
| Linux | Bubblewrap 文件系统/namespace + 内层 seccomp/no-new-privileges |
| macOS | `/usr/bin/sandbox-exec` + 动态 SBPL |
| Windows | restricted token；完整能力使用 elevated backend |
| Disabled | 原命令直接执行 |

### 4.4 Windows legacy backend

legacy backend 从当前用户 token 创建带 capability SID 的 `WRITE_RESTRICTED` token，并通过 ACL
限制可写根。

它只能作为过渡能力：

- 可以限制写入；
- 不能让 capability SID deny-read 对读取访问具有权威性；
- restricted network 主要依赖环境改写，不能提供完整的按进程网络隔离；
- 遇到无法表达的 restricted-read 策略时，Codex 会拒绝运行，不会静默 unsandboxed。

因此 Automata 不应把 legacy restricted-token 原型标记为“完整 sandbox”。

### 4.5 Windows elevated backend

Codex 的完整 Windows 后端执行以下流程：

1. setup helper 通过 UAC 获取管理员权限；
2. 创建 sandbox users 本地组；
3. 创建 online 和 offline 两个本地账户；
4. 为账户生成随机密码，并用 DPAPI 保存；
5. 为 workspace/write root 生成稳定的 capability SID；
6. 给 runtime/read roots、write roots 和 deny carveouts 更新 ACL；
7. 为 offline 账户安装按用户生效的 Windows 防火墙规则；
8. 使用 WFP 补充 DNS、SMB、ICMP 等过滤；
9. 普通 API 进程用 `CreateProcessWithLogonW` 启动 sandbox account runner；
10. runner 再创建 restricted token；
11. 真实命令通过 `CreateProcessAsUserW` 或 ConPTY 启动；
12. 父子双方通过 named pipe 传输控制消息和输出。

按 write root 生成 capability SID 是必要设计。只给一个固定 sandbox user SID 累积 ACL 权限，会导致
它以后能够访问曾经授权过的所有 workspace。restricted token 每次只携带当前 Run 需要的 capability，
可以避免跨 workspace 权限累积。

### 4.6 沙箱拒绝与重试

Codex 的顺序是：

```text
策略与审批
    ↓
选择 sandbox
    ↓
第一次 sandboxed attempt
    ↓
成功 ─────────────→ 返回
    ↓
识别 SandboxDenied
    ↓
判断是否允许脱离 sandbox
    ↓
新的显式审批
    ↓
一次 unsandboxed retry
```

当策略包含 deny-read 时，脱离 sandbox 会丢失读取限制，因此 Codex 禁止 unsandboxed retry。

## 5. 目标与非目标

### 5.1 目标

1. `Default` 的本地命令在 managed sandbox 中运行。
2. `Full Access` 明确使用 disabled sandbox，并继续保留 Plan deny、MCP grant 等独立边界。
3. workspace 之外的写入由 OS 权限拒绝，不依赖命令是否自觉遵守 cwd。
4. restricted network 对子进程及其子孙进程生效。
5. 子孙进程继承同一权限边界。
6. 长生命周期 process session 在创建时固定 profile。
7. Run 保存不可变、带版本的编译后权限快照。
8. sandbox setup、spawn、deny、retry、cancel 都有稳定错误码和可观测事件。
9. 开发模式、headless 模式和打包后的 Tauri 应用使用同一策略语义。
10. sandbox 不可用时 fail closed。

### 5.2 非目标

第一阶段不包含：

- 虚拟机级隔离；
- Windows Sandbox/Hyper-V 容器；
- 任意容器编排；
- 对远程 HTTP MCP server 的 OS 级隔离；
- 在同一个长生命周期进程内动态升降权限；
- 自动、无提示的 unsandboxed retry；
- 将 Full Access 伪装成 managed sandbox；
- 用字符串命令黑名单代替 OS 权限。

## 6. 威胁模型

Sandbox 需要约束的执行主体包括：

- 模型生成的 shell 命令；
- shell 启动的任意子孙进程；
- PATH 中被解析到的工具；
- workspace 内恶意脚本；
- 已获连接权限的 stdio MCP server；
- 结构化文件工具收到的恶意或竞争性路径。

至少考虑以下逃逸方式：

- 绝对路径访问；
- `..` 路径穿越；
- symlink、junction、mount point、Windows reparse point；
- hardlink；
- NTFS alternate data stream；
- UNC、device path、`\\?\` 路径；
- 8.3 short path 和大小写差异；
- workspace 内创建指向外部的链接后再写入；
- 子进程、孙进程或 detached process；
- IPv4、IPv6、DNS、ICMP、SMB；
- loopback 服务探测；
- 代理旁路；
- 从继承环境读取 API key、token 和内部路径；
- setup 未完成或策略无法表达时的 fail-open。

Sandbox 不负责阻止用户在 `Full Access` 中明确授予的主机访问，也不负责约束已经发生在远程服务端的
MCP/API 操作。

## 7. 权限模型

### 7.1 运行时模型

建议将 [`permissions.py`](../../api/automata_api/agent/execution/permissions.py) 扩展为：

```python
SandboxEnforcement = Literal["managed", "disabled", "external"]
NetworkSandboxPolicy = Literal["restricted", "enabled"]
FileAccess = Literal["deny", "read", "write"]

@dataclass(frozen=True)
class FileSystemRule:
    path: str
    access: FileAccess

@dataclass(frozen=True)
class FileSystemPolicy:
    kind: Literal["restricted", "unrestricted", "external"]
    entries: tuple[FileSystemRule, ...]
    glob_scan_max_depth: int | None = None

@dataclass(frozen=True)
class RuntimePermissions:
    preset: PermissionPreset
    approval_policy: ApprovalPolicy
    sandbox_enforcement: SandboxEnforcement
    file_system: FileSystemPolicy
    network: NetworkSandboxPolicy
    environment_policy_version: int
```

审批策略只回答“是否向用户询问”，sandbox profile 只回答“获准后在哪个权限边界内执行”。

### 7.2 预设映射

| Preset | Approval | Enforcement | Filesystem | Network |
|---|---|---|---|---|
| `default` | `on_request` | `managed` | workspace-write | restricted |
| `full_access` | `never` | `disabled` | unrestricted | enabled |

`Full Access` 只将 `prompt` 改为 `allow`，不能覆盖：

- Plan mode 的硬拒绝；
- MCP server 未授权；
- MCP tool call policy 的 `deny`；
- 非法参数和不存在的工具；
- 内部服务 secret 不向工具环境泄露的规则。

### 7.3 Default 编译策略

第一阶段兼容策略：

```text
filesystem root      read
workspace roots      write
temporary roots      write
.git                  read, deny write
.automata             read, deny write
.agents               read, deny write
network               restricted
```

生产发布前增加：

```text
AUTOMATA_DATA_DIR          deny read/write
resolved secret .env      deny read
API token storage         deny read
observability content     deny read
workspace secret globs    configurable deny read
```

如果 restricted-read 与常用构建工具兼容性不足，可以暂时保留 root-read，但敏感路径 deny-read 不应省略。

### 7.4 Run 快照

只保存 `permission_preset` 不足以长期重放真实权限。建议新增：

```text
agent_runs.permission_profile_version INTEGER
agent_runs.permission_profile_json    TEXT
agent_runs.sandbox_backend             TEXT
```

`permission_profile_json` 保存：

- enforcement；
- 已物化的 workspace roots；
- 文件规则；
- 网络策略；
- 环境策略版本；
- protected metadata；
- profile hash。

Session 切换 preset 只影响之后创建的 Run，不改变正在执行或恢复展示的 Run。

## 8. 总体架构

```text
ToolExecutionOrchestrator
    │
    ├─ policy deny ───────────────────────────────→ 返回拒绝
    │
    ├─ approval
    │
    ▼
SandboxManager
    │
    ├─ Disabled ──→ DirectProcessLauncher
    │
    └─ Managed ───→ PlatformSandboxBackend
                         │
                         ├─ Windows native host/runner
                         ├─ Linux bwrap/seccomp helper
                         └─ macOS sandbox-exec
```

### 8.1 Python 控制面

Python 负责：

- profile 编译与 Run 快照；
- tool policy 和用户审批；
- sandbox backend 选择；
- 环境变量过滤；
- 进程请求序列化；
- 输出限制、事件持久化和取消；
- SandboxDenied 后的审批与 retry 编排。

### 8.2 原生执行面

原生组件负责：

- OS 身份和 token；
- ACL 和 capability；
- 防火墙/WFP；
- 真实 child spawn；
- Job Object；
- stdin/stdout/stderr 转发；
- 父进程消失后的子树清理；
- 将 setup/spawn/enforcement 错误结构化返回。

## 9. Windows 原生组件

建议新增独立 Rust crate：

```text
native/windows-sandbox/
├─ Cargo.toml
└─ src/
   ├─ bin/
   │  ├─ automata-sandbox-setup.rs
   │  ├─ automata-sandbox-host.rs
   │  └─ automata-command-runner.rs
   ├─ policy.rs
   ├─ identity.rs
   ├─ token.rs
   ├─ acl.rs
   ├─ capability.rs
   ├─ firewall.rs
   ├─ wfp.rs
   ├─ ipc.rs
   ├─ process.rs
   ├─ job.rs
   └─ setup_state.rs
```

不建议直接依赖 Codex 的 `codex-windows-sandbox` crate。它与 `codex-protocol`、PTY、OTEL 和 Codex
workspace 紧密耦合。可以在遵守 Apache-2.0 attribution 要求的前提下抽取原理和经过审查的低层实现。

### 9.1 Setup helper

setup helper 必须：

- 只接受带版本的受限 schema，不接受任意 shell；
- 通过 `runas` 请求 UAC；
- 幂等创建本地 sandbox group；
- 幂等创建 online/offline accounts；
- 生成高强度随机密码；
- 用当前真实用户可解密的 DPAPI blob 保存凭据；
- 锁定 secrets 和 setup marker 的 ACL；
- 安装/刷新按用户防火墙规则；
- 给 sandbox binaries、runtime roots、read roots、write roots 更新 ACL；
- 记录 setup version 和 policy-relevant marker；
- setup 失败时写结构化错误，不留下“看似 ready”的 marker。

### 9.2 Host

host 由 Python API 以普通用户启动：

1. 接收长度前缀 JSON/CBOR request；
2. 校验 profile version、cwd 和 roots；
3. 检查 setup marker；
4. 必要时调用 setup helper；
5. 根据 network policy 选择 online/offline account；
6. 解密对应账户密码；
7. 创建带调用者/runner ACL 的 named pipes；
8. 使用 `CreateProcessWithLogonW` 启动 runner；
9. 进行 PID 绑定的 pipe handshake；
10. 转发控制消息、stdin、stdout、stderr 和退出状态。

### 9.3 Runner

runner 在 sandbox account 下运行：

- 只接受父 host 已认证 pipe；
- 解析并再次校验 request；
- 只加载当前调用所需的 capability SIDs；
- 使用 `CreateRestrictedToken`：
  - `DISABLE_MAX_PRIVILEGE`
  - `LUA_TOKEN`
  - `WRITE_RESTRICTED`
- 只保留必要 privilege；
- 创建 `KILL_ON_JOB_CLOSE` Job Object；
- 使用 `CreateProcessAsUserW` 启动目标程序；
- 限制继承 handle 列表；
- 父 pipe 断开时终止 Job；
- 将退出码、超时、spawn failure 和 enforcement failure 分开报告。

Automata 当前没有 PTY，第一版可继续使用 pipes。ConPTY 和终端 resize 不应成为第一阶段前置条件。

### 9.4 文件 ACL

ACL 规划必须：

- capability SID 按 canonical write root 派生；
- workspace root 授予 capability write；
- read-only carveout 添加 deny-write；
- 不存在的受保护路径在命令启动前创建或占位，避免命令抢先创建；
- 同时处理 lexical path 和已存在的 canonical target；
- 处理 reparse point；
- `.git`、`.automata`、`.agents` 默认保护；
- setup refresh 幂等；
- 不清除用户原有 ACL；
- 不授予 sandbox account SID 对全部历史 workspace 的永久写权限。

### 9.5 网络隔离

restricted network 使用 offline account：

- 非 loopback IPv4/IPv6 全部出站阻止；
- loopback UDP 默认阻止；
- loopback TCP 默认阻止；
- managed proxy 模式只放行指定 loopback TCP 端口；
- 可选 local-binding 模式必须明确配置；
- WFP 补充 DNS、SMB、ICMP；
- firewall policy 被组策略覆盖时 setup 失败；
- 不能仅通过设置无效 proxy 环境变量宣称网络已隔离。

online account 只用于明确 `network=enabled` 的 managed profile。`Full Access` 不需要 sandbox account。

### 9.6 环境变量

所有 preset 都应先应用独立的 environment policy。默认不向工具进程继承：

- `AUTOMATA_LLM_API_KEY`
- `AUTOMATA_API_TOKEN`
- provider authorization headers
- 数据库内部路径和凭据
- observability content capture 配置
- sandbox account secrets

允许的环境按白名单构造，例如：

- `PATH`
- `PATHEXT`
- `SYSTEMROOT`
- `COMSPEC`
- locale/encoding
- 经过重定向的 `TEMP`、`TMP`
- sandbox HOME/USERPROFILE

Full Access 表示主机文件和网络不受 sandbox 限制，不表示 Automata 必须主动把内部控制面 secret 注入工具。

## 10. Python 接入点

建议新增：

```text
api/automata_api/agent/execution/sandbox/
├─ __init__.py
├─ model.py
├─ policy.py
├─ manager.py
├─ errors.py
├─ environment.py
├─ launcher.py
└─ backends/
   ├─ base.py
   ├─ direct.py
   ├─ windows.py
   ├─ linux.py
   └─ macos.py
```

### 10.1 统一启动接口

```python
class ProcessLauncher(Protocol):
    async def spawn(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str],
        stdin: StdioMode,
        stdout: StdioMode,
        stderr: StdioMode,
        profile: CompiledPermissionProfile,
        scope: ProcessExecutionScope,
    ) -> SpawnedProcess: ...
```

所有本地子进程必须经过该接口。发布前至少审计并迁移：

- `_core.run_exec_command`
- `_core.run_process`
- `LocalBackend._run_process`
- search/list 的 `rg`、`grep`、bash fallback
- `ProcessSessionManager`
- stdio MCP client
- setup/diagnostic helper

任何旁路的 `asyncio.create_subprocess_exec()` 都应被静态检查或测试发现。

### 10.2 现有 ProcessSupervisor

`ProcessSupervisor` 继续负责 Run/Session/tool-call 关联和取消，但 managed Windows 进程的真正树形终止
由 native runner Job Object 保证。

Python 不应在 runner 已启动真实 child 后再尝试补做安全属性。host/runner 必须先完成权限和 Job 准备，
再启动目标进程，避免先运行后赋权的竞态窗口。

### 10.3 长进程

process session 在创建时保存：

- run/session/workspace scope；
- compiled profile hash；
- sandbox backend；
- native host session id；
- process/job ownership。

`write_stdin` 只与已存在进程交互，不能改变它的 profile。Session preset 修改也不能改变该进程。

## 11. 工具边界

| 工具类型 | Sandbox 策略 |
|---|---|
| `exec_command`、bash、PowerShell | managed profile 下必须走平台 sandbox |
| search/list native helper | 走 read-only/restricted-network helper profile |
| read file/stat | 应用层 policy；后续使用安全句柄或文件 worker |
| write/delete/apply_patch | 应用层 policy + sandbox file worker/句柄级防逃逸 |
| `write_stdin` | 继承被创建 process session 的固定 profile |
| stdio MCP | server 启动时固定 Run profile |
| HTTP MCP | 不受本地 OS sandbox 保护，依赖 grant/policy/approval |
| LLM provider | 控制面网络，不进入工具 sandbox |

结构化文件工具不能因为“不启动 shell”而跳过权限模型。它们应和命令工具共享同一
`FileSystemPolicy` matcher。

## 12. Sandbox attempt 与错误协议

建议错误分类：

```text
sandbox_unavailable
sandbox_setup_required
sandbox_setup_failed
sandbox_policy_unsupported
sandbox_spawn_failed
sandbox_denied
sandbox_network_denied
sandbox_timed_out
sandbox_protocol_error
```

工具输出增加：

```json
{
  "sandbox": {
    "enforcement": "managed",
    "backend": "windows-elevated",
    "profile_hash": "...",
    "attempt": 1,
    "denied": false
  }
}
```

不能把所有非零退出都标记为 sandbox denial。优先级为：

1. native helper 明确报告；
2. Windows spawn/access error 明确分类；
3. Linux signal/seccomp 信息；
4. 已知 permission-denied 输出启发式；
5. 其他情况保持普通 command failure。

### 12.1 Retry 规则

- `Full Access` 首次就是 direct execution，不存在 sandbox retry。
- `Default` 首次必须 sandboxed。
- setup/spawn/protocol failure 不自动 unsandboxed。
- 普通 command failure 不触发 unsandboxed approval。
- 只有 `sandbox_denied` 才能申请脱离 sandbox。
- deny-read policy 存在时不得脱离 sandbox。
- 审批通过后最多重试一次。
- retry 记录新的 attempt 和审批事件。

## 13. 数据库与事件

新增 migration 时保留当前 `permission_preset`，同时增加编译后快照字段。

建议新增事件：

```text
sandbox_selected
sandbox_setup_required
sandbox_setup_started
sandbox_setup_completed
sandbox_setup_failed
sandbox_attempt_started
sandbox_denied
sandbox_retry_requested
sandbox_retry_started
```

事件只记录：

- backend；
- profile hash/version；
- 状态和错误码；
- root 数量、规则数量；
- 耗时；
- 是否 elevated；
- 是否 retry。

不得记录账户密码、DPAPI blob、完整环境变量、API key 或敏感文件路径正文。

## 14. UI

权限选择至少显示：

```text
Default
  Managed sandbox
  Workspace/temp 可写
  Protected metadata 不可写
  Network restricted
  仍按当前审批规则询问

Full Access
  No sandbox
  No eligible approval prompts
  可访问 workspace 外文件和网络
```

Windows setup 未完成时：

- 显示需要一次管理员授权；
- 用户明确触发 setup；
- UAC 取消后 Default 命令保持失败，不切换 Full Access；
- setup readiness 可重新检测；
- 不在后台反复弹 UAC。

工具卡可显示 backend 和 `sandbox_denied`，但不展示内部账户名或安全凭据。

## 15. 打包与启动

当前 Tauri 已打包 `automata-api` external binary。新增：

```json
"externalBin": [
  "binaries/automata-api",
  "binaries/automata-sandbox-host",
  "binaries/automata-sandbox-setup",
  "binaries/automata-command-runner"
]
```

[`run.ps1`](../../run.ps1) 增加：

1. 根据 Rust host triple 编译 native sandbox；
2. 把三个二进制复制到 `ui/src-tauri/binaries/*-<target-triple>.exe`；
3. 将源码时间戳纳入增量构建判断；
4. dev 模式同步到 Tauri target 目录；
5. headless 模式支持显式 helper path 或自动发现开发产物；
6. release smoke 检查 helper 可发现、签名/哈希匹配、setup readiness 可查询。

Python API 通过显式环境变量或 sidecar sibling discovery 找到 host，不能从 workspace 或普通 PATH
选择安全 helper。

## 16. 分阶段实施

### S0：权限模型与统一启动入口

状态：`PLANNED`

- 扩展 `RuntimePermissions`；
- 定义编译后 profile；
- Run 保存 profile version/json/hash；
- 新增 `SandboxManager` 和 direct backend；
- 统一环境白名单；
- 迁移所有本地 subprocess 入口；
- 先通过 feature flag 保持现有行为。

退出标准：

- 没有工具直接旁路统一 launcher；
- Full Access 行为不回归；
- Plan/MCP deny 不被 Full Access 覆盖；
- Run profile 不随 Session 修改而漂移。

### S1：Windows workspace-write 原型

状态：`PLANNED`

- Rust host/runner/setup 骨架；
- sandbox account provisioning；
- per-root capability SID；
- restricted token；
- workspace/temp 写；
- protected metadata deny-write；
- Job Object 和 pipe I/O；
- 暂以 root-read 兼容策略为主。

该阶段是内部原型，不得仅凭 restricted write 宣称生产 sandbox 完成。

### S2：Windows 网络与敏感读取保护

状态：`PLANNED`

- offline/online identity；
- 防火墙/WFP；
- managed proxy allowlist；
- deny-read ACL；
- data dir、secret env sources 和敏感 glob 保护；
- reparse point/不存在路径处理；
- setup marker 和非提权 ACL refresh。

退出标准：

- 文件和网络测试均 fail closed；
- setup 被 UAC 取消不会普通权限执行；
- 不存在可复现的 workspace 外写入和直连网络旁路。

### S3：审批回退、文件工具与 stdio MCP

状态：`PLANNED`

- `sandbox_denied` 分类；
- 显式 unsandboxed retry 审批；
- deny-read 下禁止 bypass；
- 写文件、删除、patch 接入统一 policy/worker；
- stdio MCP 按 Run profile 启动；
- process session 固定 profile。

### S4：Linux 与 macOS

状态：`PLANNED`

Linux：

- Bubblewrap；
- user/PID/network namespace；
- read-only root + write bind；
- seccomp/no-new-privileges；
- WSL1 显式不支持或降级失败。

macOS：

- 固定 `/usr/bin/sandbox-exec`；
- 动态 SBPL；
- read/write roots；
- deny-read；
- network/proxy policy。

### S5：发布与安全收尾

状态：`PLANNED`

- 打包 smoke；
- 安装/升级 setup version 迁移；
- helper 完整性验证；
- 可观测性；
- 文档与 UI；
- 全量安全矩阵；
- 人工逃逸测试。

## 17. 自动化测试矩阵

### 17.1 权限模型

- Default 编译为 managed/workspace-write/restricted-network；
- Full Access 编译为 disabled/unrestricted/enabled；
- Full Access 只把 prompt 变 allow，不覆盖 deny；
- Run 快照不可变；
- 未知 profile version fail closed。

### 17.2 文件系统

- workspace 内创建、修改、删除成功；
- workspace 外创建、修改、删除失败；
- temp 写入成功；
- `.git`、`.automata`、`.agents` 写入失败；
- workspace 外读取按 profile 成功或失败；
- deny-read secret 失败；
- symlink/junction/reparse point 逃逸失败；
- hardlink 逃逸失败；
- 不存在 protected path 不能抢先创建；
- 大小写、8.3、UNC、device path、ADS 不能绕过；
- child/grandchild 权限一致。

### 17.3 网络

- IPv4 外连失败；
- IPv6 外连失败；
- DNS 失败；
- ICMP 失败；
- SMB 失败；
- loopback TCP/UDP 默认失败；
- managed proxy 指定端口成功；
- 非指定 loopback 端口失败；
- online managed profile 按策略成功；
- Full Access 网络成功。

### 17.4 环境

- 工具进程看不到 LLM key；
- 看不到 API token；
- 看不到 sandbox credentials；
- PATH、SYSTEMROOT、TEMP 等必要变量可用；
- Full Access 也不自动继承控制面 secrets。

### 17.5 生命周期

- timeout 终止整个 Job；
- Run cancel 终止 child/grandchild；
- API 崩溃后 runner 终止子树；
- process session 不能跨 Run/Session/workspace；
- `write_stdin` 不能改变 profile；
- UAC 取消 fail closed；
- helper handshake 超时不遗留 runner。

### 17.6 Retry

- 普通退出码不触发 sandbox retry；
- sandbox denial 生成独立审批；
- 用户拒绝不重试；
- 用户允许只重试一次；
- deny-read profile 不允许 unsandboxed；
- setup failure 不允许 unsandboxed。

### 17.7 打包

- headless 开发模式可发现 helper；
- Tauri dev 可发现 helper；
- release binary 可发现 helper；
- helper 缺失时 Default fail closed；
- helper 版本不匹配时要求 setup/升级；
- Full Access 不依赖 helper 才能运行。

## 18. 验收标准

Sandbox 第一版只有同时满足以下条件才可对用户标记为已实现：

1. Default 本地命令全部经过 managed platform backend。
2. 不存在已知的普通 subprocess 旁路。
3. workspace 外写入由 OS 权限拒绝。
4. protected metadata 不可写。
5. restricted network 对 child/grandchild 有效。
6. 控制面 secrets 不进入工具环境。
7. setup 不完整或策略不支持时 fail closed。
8. Full Access 明确走 direct backend。
9. Plan deny、MCP grant 和显式 policy deny 保持有效。
10. 长进程从创建到退出保持同一 profile。
11. sandbox denial 不会静默降级。
12. Run 保存可审计的 profile version/hash/backend。
13. backend、API 和 UI 自动化测试通过。
14. Windows release smoke 通过。
15. 完成人工 reparse point、网络和进程树逃逸检查。

## 19. 实施前需要最终确认的产品决策

以下选择不阻塞架构，但在 S0/S1 开始前应定案：

1. Default 是否严格复制 Codex 的 root-read，还是默认只读 workspace + runtime roots。
2. 哪些 workspace secret glob 默认 deny-read。
3. Default 中 sandboxed command 是否仍按当前规则每次审批。
4. managed proxy 是否进入 Windows 第一版。
5. 是否允许用户配置额外 read/write roots。
6. Full Access 切换是否需要额外警告或二次确认。

当前建议：

- S1 使用 Codex 兼容的 root-read/workspace-write 以降低工具兼容成本；
- S2 完成敏感路径 deny-read 后才对外发布；
- 保持当前审批语义，先只增加 sandbox，不在同一改动中重新定义审批产品行为；
- managed proxy 可以晚于完全断网，但 restricted network 不能用环境变量模拟；
- 第一版不开放任意自定义 roots，先稳定内建 profile。

## 20. Codex 参考源码索引

以下路径相对于参考仓库 `D:\workspace\projects\codex`：

| 主题 | 路径 |
|---|---|
| PermissionProfile | `codex-rs/protocol/src/models.rs` |
| 文件/网络策略 | `codex-rs/protocol/src/permissions.rs` |
| 平台选择与命令变换 | `codex-rs/sandboxing/src/manager.rs` |
| additional permission 合并 | `codex-rs/sandboxing/src/policy_transforms.rs` |
| 审批、sandbox attempt、retry | `codex-rs/core/src/tools/orchestrator.rs` |
| sandbox override/bypass 限制 | `codex-rs/core/src/tools/sandboxing.rs` |
| Linux 设计说明 | `codex-rs/linux-sandbox/README.md` |
| Linux bwrap | `codex-rs/linux-sandbox/src/bwrap.rs` |
| Linux seccomp/Landlock | `codex-rs/linux-sandbox/src/landlock.rs` |
| macOS Seatbelt | `codex-rs/sandboxing/src/seatbelt.rs` |
| Windows permission resolution | `codex-rs/windows-sandbox-rs/src/resolved_permissions.rs` |
| Windows setup | `codex-rs/windows-sandbox-rs/src/setup.rs` |
| Windows identity | `codex-rs/windows-sandbox-rs/src/identity.rs` |
| Windows token | `codex-rs/windows-sandbox-rs/src/token.rs` |
| Windows ACL/spawn prep | `codex-rs/windows-sandbox-rs/src/spawn_prep.rs` |
| Windows command runner | `codex-rs/windows-sandbox-rs/src/bin/command_runner/win.rs` |
| Windows setup helper | `codex-rs/windows-sandbox-rs/src/bin/setup_main/win.rs` |
| Windows firewall | `codex-rs/windows-sandbox-rs/src/bin/setup_main/win/firewall.rs` |
| Windows WFP | `codex-rs/windows-sandbox-rs/src/wfp.rs` |
