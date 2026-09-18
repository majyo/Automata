# Automata 项目结构优化与模块化重构方案

> 状态：待实施的设计方案；本次仅新增文档，未执行业务代码重构。
>
> 核验日期：2026-09-18。
>
> 代码基线：`codex/sandbox-system-upgrade` 分支，提交 `10c8b90`（`Refactor ui`）；分析开始时工作区干净。
>
> 目标：让业务模块、应用编排和技术实现形成清晰边界，降低修改传播范围，并保留现有产品行为。

## 1. 总体决策与范围

采用**模块化单体 + 显式依赖注入 + 端口与适配器**。继续保持 `api/`、`ui/`、`native/` 三个顶层工程，先在各自内部建立职责和依赖约束，再逐步迁移实现。

模块化回答“这段代码由谁负责”，分层回答“它可以依赖谁”。目录移动只是实施手段，最终要达到以下结果：

- 增加工具时，改动集中在工具实现与注册位置，不需要修改 Agent 主循环或工作区后端。
- 更换工作区、模型或存储实现时，通过已有能力接口装配，不要求上层理解具体实现。
- 调整 WebSocket 协议映射时，不同时修改 Run 状态机、数据库事务和 React 消息状态。
- Agent 与应用用例可以使用内存替身测试；SQLite、模型 HTTP、操作系统进程分别进行适配器测试。
- 核心依赖方向由自动检查保护，新增代码不能重新回到跨层调用与全局单例。

本轮保持 FastAPI/Python sidecar、React/Tauri、REST/WebSocket、SQLite 和现有 Windows 沙盒宿主。Rust 后端迁移继续作为[独立路线](rust-backend-migration-roadmap.md)，本方案不同时引入微服务、消息中间件、新 ORM 或全局前端状态库，也不改变现有 UI 视觉设计。

## 2. 当前结构与具体问题

### 2.1 已具备的基础

当前项目已经有 `routers`、`services`、`repositories`、`db` 的初步分层，Agent 内部也包含 tools、MCP、Skills、execution 等子包。可以复用的边界包括：

- `AgentContextStore` 协议与 `SessionAgentContextStore` 适配器。
- `Backend`、`ToolProvider`、`ToolDescriptor`、`ToolRouter` 等能力入口。
- `RunCoordinator`、`DurableRunEventSink`、`ApprovalBroker` 的独立职责雏形。
- Agent 禁止依赖 FastAPI、services、routers 的现有架构测试。
- 前端已经拆出的 API 客户端、hooks、reducer 和展示组件。

因此采用逐步提取、委托和替换的方式，保留这些已有接口的有效部分。

### 2.2 当前主要调用路径

一次普通任务的主要链路如下；`AgentConnection` 构建的执行闭包交给 coordinator 管理，实际 Run 不依附于 WebSocket 连接存活：

```text
React App / useAgentSocket
  → /ws/chat → AgentConnection
  → RunCoordinator.start_prompt
  → services.chat.stream_agent_reply
  → Backend + MCP runtime + Skills + ContextStore 装配
  → Agent model loop → LLM / ToolExecutionOrchestrator / ToolRouter
  → 工作区文件、进程、Sandbox、MCP
  → 运行事件持久化 → 广播 → 前端事件投影与 reducer
```

会话 HTTP 路由目前直接调用 repository；Run 完成则由 coordinator 调用 `finish_run()`，原子提交结果及终态。重构必须分别保留这些链路的行为。

### 2.3 重构切入点

下表行数来自当前工作区，仅用于定位职责集中的位置，不作为拆文件的硬性阈值。

| 位置 | 当前事实 | 结构问题及处理方向 |
| --- | --- | --- |
| `api/automata_api/services/connection.py`，471 行 | 认证、命令解析、Run 启动、Plan 执行、审批、取消、回放和直接 repository 访问集中在连接对象 | 分为协议适配、应用用例和回放服务；保留发送串行化 |
| `api/automata_api/services/chat.py`，473 行 | 每次运行装配 Backend、MCP、Skills、ContextStore、工具审批，并处理事件和消息保存 | 提取运行资源工厂、回合执行器和消息投影，统一 act/plan 的公共装配 |
| `api/automata_api/agent/runtime.py`，670 行 | 同时负责模型循环、上下文、工具执行与 provider 消息转换；直接导入 `db.context_search` 的来源常量 | 分离 engine、context、provider adapter，通过领域类型替代数据库常量依赖 |
| `api/automata_api/agent/tools/_core.py`，2347 行 | 混合 ToolResult、参数处理、路径、补丁、进程输出、命令执行等 | 按职责将纯算法、工作区能力、进程能力和工具协议分离 |
| `api/automata_api/agent/backends/local.py`，1141 行 | 导入 `tools._core`，复用路径、进程和工具执行逻辑；`Backend.tools()` 又构造工具，WindowsBackend 增加 PowerShell 工具 | 工具依赖工作区能力，工作区不再反向依赖工具；注册移到工具工厂 |
| `api/automata_api/repositories/runs.py`，1007 行 | 状态转换、Plan 幂等、终态事务、事件游标和 SQLite 实现混合 | 抽出规则与存储端口，保留用例级事务，不拆成独立提交的 CRUD |
| `api/automata_api/repositories/sessions.py`，849 行 | 会话、消息、模型上下文、摘要与检索逻辑集中 | 明确各类数据的模型与接口，内部按职责拆分 SQL |
| `ui/src/hooks/useAgentSocket.ts`，720 行；`ui/src/state/chatReducer.ts`，604 行 | 连接重试、Run 游标、回放、命令发送、展示投影和状态更新交织 | 拆连接客户端、Run 流控制器、纯事件投影与状态切片 |
| `ui/src/App.tsx`、`components/app-shell/AppShell.tsx` | App 组合多个 hooks，并向 AppShell 传递大量业务字段和回调 | 应用层组合 feature controller，页面接收有边界的模型和操作组 |
| `api/automata_api/observability/sampler.py` | 采样函数内部导入进程全局管理器；对 ObservabilityManager 的反向引用位于 `TYPE_CHECKING` | 注入资源计数回调；区分运行时反向依赖与仅类型依赖，不将类型引用描述为启动失败 |

另一个共性问题是模块级实例：coordinator、event hub、process supervisor 等由多个模块直接导入，配置也分散读取。这使实例隔离、生命周期管理和替换测试较困难。

## 3. 模块划分与分层规则

### 3.1 分层含义

| 层次 | 职责 | 可依赖内容 |
| --- | --- | --- |
| 传输/展示层 | HTTP、WebSocket、React 页面；输入解析、协议映射、交互展示 | 应用用例入口、外部协议 DTO |
| 应用层 | 组织一次用例，协调资源、事务、取消与审批 | 本模块规则、所需端口、其他模块明确公开的 API |
| 领域/规则层 | Run 状态约束、Plan 重试规则、工具风险与模式策略、结构化值对象 | 标准库和少量无框架的公共类型 |
| 适配器层 | SQLite、HTTP/SSE、文件系统、进程、MCP、Tauri、平台沙盒 | 对应端口与领域类型、技术库 |
| 装配层 | 选择实现、注入依赖、管理启动关闭和资源作用域 | 各模块公开入口与具体适配器 |

应用层依赖接口，适配器实现接口。跨模块实现选择集中在 `bootstrap`；适配器仍可创建自己的内部辅助对象。无需为每个函数创建 interface，也无需为每个小模块预建四层空目录。

### 3.2 模块责任与接口归属

| 模块 | 拥有的职责 | 主要公开边界 |
| --- | --- | --- |
| `sessions` | 会话元信息、权限预设配置、对话消息、上下文记录、摘要与会话内检索 | SessionService、SessionStore、ConversationStore、ContextStore |
| `runs` | Run 生命周期、Plan/执行尝试、审批状态、取消、恢复、事件回放 | RunService、RunStore、RunEventStore、ReplayService、ApprovalBroker |
| `agent` | 回合循环、上下文装配/压缩、提示词、act/plan 策略、模型消息语义 | TurnEngine、ModelProvider、ContextAccess、ToolExecutor |
| `tools` | 工具描述与注册、发现、参数校验、风险判定、审批协调、工具结果 | ToolCatalog、ToolExecutor 实现、ToolResult、ApprovalGateway |
| `workspace` | 文件读写、路径约束、搜索、文件枚举、补丁应用能力 | FileAccess、SearchAccess、WorkspaceDescriptor |
| `execution` | 进程启动、输出收集、长进程会话、超时、取消、Sandbox 与平台实现 | ProcessRunner、ProcessSessionService、SandboxLauncher |
| `extensions/mcp` | MCP 配置、传输、信任校验、发现、结果转换与生命周期 | MCP 工具提供器、MCP 管理服务 |
| `extensions/skills` | Skills 发现、加载、设置、选择和提示词材料生成 | SkillService、回合上下文贡献 |
| `storage/sqlite` | 上述存储端口的 SQLite 实现、连接、schema 和事务 | 由装配层注入的具体 store；不对 transport 暴露 SQL |
| `observability` | trace、日志、采样、脱敏、保留策略 | Observer API；资源计数从外部注入 |
| `contracts` | REST/WS 外部协议及少量跨模块基础类型 | 版本化事件信封、协议 DTO、稳定标识与取消信号等 |
| `bootstrap`、`transport` | 分别负责依赖装配与网络协议接入 | create_app、容器工厂、路由适配器 |

接口按使用方需要定义：例如 `agent.ports.ModelProvider` 与 `runs.ports.RunStore` 属于使用方。文件和进程能力已经是多个模块共享的稳定能力，可由 `workspace.api`、`execution.api` 定义，再由各自适配器实现。

Plan 属于 runs；“计划模式下哪些工具可用”属于 agent/tools 的执行策略。模型上下文压缩算法属于 agent，压缩结果与历史的存取属于 sessions/storage。业务运行事件属于 runs，性能采样与日志属于 observability，两种数据管道不相互替代。

当前 `agent/execution/model.py` 的混合类型按归属拆开：RunOutcome 和审批状态归 runs，ToolRisk、ToolPolicyDecision、ToolExecutionContext 归 tools，跨模块使用的取消信号协议归公共基础类型，其可变实例由 Run 创建。`permissions.py` 中平台无关的权限配置及编译结果可归 execution 的公开权限模型，由 sessions 保存预设、runs 保存快照、tools 读取执行约束；权限类型不反向导入工具注册或数据库实现。

### 3.3 必须执行的依赖约束

1. `transport` 只调用应用用例；不导入 SQLite store、具体 Backend 或全局 coordinator。FastAPI 的依赖入口从 `app.state` 取得所需服务，容器本身不传入业务层。
2. `agent` 核心不导入 FastAPI、SQLite、具体工作区、MCP/Skills 实现或全局配置读取器。模型 HTTP 实现放在其 adapter 中，通过 bootstrap 注入核心。
3. `tools` 使用 workspace/execution 的公开能力；workspace/execution 不导入 tools，返回文件/进程结果，不返回 ToolResult。
4. 工具审批通过 tools 定义的 `ApprovalGateway` 端口请求；runs 提供实现。runs 不反向导入具体工具，Run 执行通过注入的回合执行接口完成。
5. 扩展模块可以实现 agent/tools 的接口；agent/tools 核心不导入具体扩展。Skills 输出上下文材料，MCP 输出工具描述与执行器。
6. SQLite 实现可以理解一次业务事务涉及的多张表，但上层不能取得裸连接。其他模块不导入 `storage/sqlite` 内部实现。
7. `observability` 公共入口不反向导入业务模块；sampler 接受计数提供器。业务代码使用轻量 Observer API，测试可替换为无操作实现。
8. 每个模块通过 `api.py` 或 `public.py` 公开有限入口。`__init__.py` 不创建资源，不汇总导入所有具体实现。禁止借 re-export 或函数内延迟导入绕过边界。
9. `contracts` 不成为通用杂物目录：运行领域模型归 runs，工具结果归 tools，存储记录映射归适配器；只有真实跨边界契约才进入公共包。

## 4. 建议目标目录

以下为迁移完成后的方向；按阶段逐项出现，不一次性创建全部空目录。

```text
api/
  main.py                         # 保留 uvicorn / PyInstaller 兼容入口
  automata_api/
    main.py                       # 薄 create_app 兼容入口
    bootstrap/
      app.py                      # FastAPI 工厂与传输层组装
      container.py                # 跨模块依赖装配
      lifecycle.py                # 启停、关闭顺序、恢复入口
      settings.py                 # 配置读取并转换为模块配置对象
      turn_resources.py           # 每个 Run 的资源作用域
    contracts/
      wire/                       # HTTP、WS 命令、事件信封、错误映射
      primitives.py               # 极少量无框架公共类型
    transport/
      http/                       # sessions、runs、skills、mcp、sandbox、health
      websocket/                  # connection、command_decoder、sender
    sessions/
      api.py
      models.py
      application.py
      ports.py
    runs/
      api.py
      domain/                     # 状态、Plan 尝试、领域错误
      application/                # coordinator、approval、replay、event_sink
      ports.py
    agent/
      api.py
      engine.py
      context.py
      prompts.py
      policies.py
      models.py
      ports.py
      adapters/chat_completions.py
    tools/
      api.py
      models.py
      catalog.py
      dispatch.py
      policy.py
      ports.py
      builtin/                    # files、search、patch、exec、thread_context
    workspace/
      api.py
      paths.py
      patches/                    # 纯解析与内容变换
      adapters/                   # local 文件系统、搜索实现
    execution/
      api.py
      permissions.py              # 权限配置、编译结果与公开值对象
      processes.py
      sessions.py
      output.py
      sandbox/                    # launcher、protocol、平台适配器
    extensions/
      mcp/
      skills/
    storage/sqlite/
      connection.py
      schema.py
      baseline.py
      migrations/
      sessions.py
      contexts.py
      runs.py
      plans.py
      events.py
    observability/
      api.py
      runtime.py
      sampler.py
      ...                         # 保留已有 writer、redaction 等职责
  tests/
    architecture/
    unit/
    integration/
    contracts/
ui/
  src/
    app/                          # 应用装配、页面和布局
    features/
      sessions/                   # api、model、hooks、components
      conversation/               # 消息、Plan、工具卡片与 composer
      runs/                       # 命令、流控制、事件投影、Run 状态
      skills/
      sandbox/
    platform/
      api/                        # HTTP、WS 客户端、认证与配置
      desktop/                    # Tauri bridge
    contracts/                    # 后端外部协议的前端类型
    shared/                       # 真正跨 feature 的 UI、样式、纯工具
  src-tauri/                      # 保留现有工程位置
native/
  windows-sandbox/                # 保留独立二进制工程
Docs/
  project-structure-refactoring-plan.md
```

小模块先用 `models.py`、`ports.py`、`application.py`，只有出现独立职责才展开子目录。初期不调整 Python 包名、npm 工程边界或 Rust workspace；测试目录最后按模块迁移，避免同时改动所有测试路径。

## 5. 关键抽象如何落地

### 5.1 装配与生命周期

增加 `AppContainer`，显式持有 stores、RunService、EventHub、进程管理器、扩展管理服务和 Observer。`create_app(settings=None, container=None)` 支持测试注入；默认路径保留现有入口行为。

区分资源作用域：

| 作用域 | 资源及规则 |
| --- | --- |
| 应用 | EventHub、Run 协调器、进程管理器、配置快照、观测写入器；每个 app 实例独立 |
| Run | 取消信号、ApprovalBroker、事件 sink、权限快照、工具目录及延迟激活状态、Skills 材料、MCP runtime；跨 Run 不共享可变授权状态 |
| WebSocket | 发送锁、回放缓冲、订阅关系；关闭仅取消订阅和清理连接资源 |

初期保持 MCP runtime 当前按回合创建的语义，连接池复用另行评估。配置加载逐步从执行深处移到入口，权限继续在 Run 创建时快照；UI 修改会话配置不隐式改写正在执行的 Run。

关闭顺序保持“停止接收新任务 → 中断/收束 Run → 清理进程会话及子进程 → 清理订阅与事件资源 → 关闭观测写入”。资源工厂使用异步上下文管理器，在部分初始化失败时也能释放已经取得的资源。

### 5.2 解开 Tool 与 Backend 的反向依赖

第一步将 `ToolResult` 移至 tools 的模型文件，将路径和进程辅助函数分别迁至 workspace/execution；旧 `_core.py` 暂时单向转发。随后工具只拿所需能力，不接收无边界的 Backend 对象。

具体例子：

- `read_file` 工具负责参数校验与 ToolResult 格式；FileAccess 负责文件读写和路径约束。
- `exec_command` 工具负责模型可见参数及输出格式；ProcessRunner 返回退出码、输出、超时、沙盒元数据等进程结果。
- `apply_patch` 的纯解析/内容变换与文件提交分开；保留 dry_run、匹配规则和错误结果。隔离操作继续经过现有 sandbox 路径。
- 文件检索的 fallback、输出截断、枚举边界保留在能力实现中；工具注册不负责启动进程。

用 `build_builtin_tools(capabilities, context_access)` 替代 `Backend.tools()`。Windows 可用工具根据 capabilities 注册；提示词工具说明由描述信息生成，不由文件系统对象决定。`ToolRouter.from_backend()` 等便利工厂在迁移期转发，最后删除。

禁止通过新的万能 `utils.py` 接收全部 `_core.py` 内容。提取顺序为结果类型 → 纯函数 → 进程/文件能力 → 工具适配 → 移除反向引用。

### 5.3 Run、Session 与事务边界

保留 `create_prompt_run`、`begin_plan_execution`、`finish_run` 这类表达完整业务操作的存储接口。应用层提供有类型的请求与结果，SQLite 适配器内部可拆 SQL 辅助文件，但一次操作只有一个事务入口。

特别保留：

- `create_prompt_run` 原子创建用户消息与 Run，并保留数据库“一会话至多一个非终态 Run”的约束。
- `begin_plan_execution` 保留 Plan 校验、执行尝试、`request_id` 幂等及新 Run 的一致性。
- `finish_run` 在同一事务内提交需要保存的最终消息、Plan 内容/状态、Run 状态与终态事件。重复结束遵循现有终态处理，不重复生成最终消息。
- 删除会话和检查活动 Run 保持原子约束，不能先查再通过另一个连接删除。
- 对话消息、模型上下文、Run 事件明确区分；上下文检索仍绑定调用方 Session，不开放任意会话范围。

调用 SQL 的同步阻塞工作由适配器统一封装 `asyncio.to_thread`。整个事务在同一工作单元内完成，不将多步 SQL 拆成多次异步线程调用，也不在持有事务时等待模型或工具。

当前 README 明确说明历史 schema 不提供通用升级支持。因此本轮结构重构保持当前 schema、数据库位置和数据含义不变，不要求用户删库；schema 变更如确有必要，应成为独立迁移任务。

### 5.4 Agent 与扩展

将现有 `stream_model_loop` 收敛为 TurnEngine；act/plan 共用模型循环、事件处理与上下文压缩，只通过显式策略区分提示词、可用工具和完成结果。

核心输入为已解析配置、上下文访问端口、ModelProvider、ToolExecutor、取消信号和上下文材料。MCP/Skills 装配在外部完成，Agent 不识别具体协议客户端或 Skills 配置文件。

模型 HTTP/SSE 解析、provider 消息规范化和网络错误转换归 model adapter；核心使用内部模型消息与增量类型。当前公开错误码通过传输映射保留，技术异常细节只进入日志。

同时逐步收窄 `AgentLoopEvent | dict[str, Any]` 等兜底类型：先覆盖现有核心事件，再为扩展预留有名称、可校验的 payload。外部网络边界与不确定扩展数据允许显式校验，避免用全局 `Any` 消除类型问题。

### 5.5 持久化事件、回放与传输

区分三类对象：Agent 内部事件、带 `run_id/session_id/seq/schema_version` 的持久化 Run 事件、直接发给连接的控制消息（如 ready、回放开始/完成）。不强迫连接控制消息进入业务事件表。

外部协议的规范定义集中在 `contracts/wire`；前端类型通过共享契约样例进行一致性检查，后续再决定是否自动生成。内部类型重命名不改变已发布 JSON 字段、默认值、错误码与 `schema_version`；数据库中已有事件也必须能够被读取和回放。

事件 sink 继续先持久化再广播，并保持 token 聚合、输出大小限制、脱敏及终态前 flush。广播失败后依靠已持久化的序号恢复，不重新执行有副作用的工具。

ReplayService 负责游标校验、watermark、分页读取及裁剪后的恢复错误；WebSocket sender 负责发送锁与回放期间的实时缓冲。交接须覆盖回放前、回放中和结束瞬间到达的事件，保持按 Run 排序和去重；不能把回放改成“查历史后直接订阅”而引入缺口。

现有 `_persist_and_broadcast_locked` 还承担审批事件引起的状态转换。迁移时先整体保留其顺序，再把状态转换归还 Run 应用层；不得在移动文件时无意改变提交顺序。

### 5.6 前端按功能组织，并分离连接与投影

将 `useAgentSocket` 逐步改为薄 React 适配层，提取：

1. `AgentSocketClient`：认证、连接、重连、关闭与消息编解码，不持有聊天 reducer。
2. `RunStreamController`：维护每个 Run 的 sequence、重放状态、缺口补取、终态以及命令请求标识；通过回调发布事件和副作用请求。
3. `projectRunEvent(event, state)`：纯函数，将已接受事件转换为消息、Plan、审批和 Run 状态动作，不调用网络、计时器或 React。
4. 组合 reducer：按 messages、runs、plans、approvals 划分纯更新逻辑，仍可使用现有 `useReducer`，保留一次事件的协调更新。

React 中只保留订阅与 UI 状态；controller 拥有传输游标，reducer 中的 sequence 只是展示投影，不允许两处独立推进。会话切换不丢失其他 Session 的后台 Run 状态。

`app` 负责 sessions、runs、skills 的组合。features 通过公开类型与回调协作，不互相导入内部 hooks/reducer。AppShell 接收 `sessionView`、`conversationView`、`composerActions` 等明确模型与操作组，避免继续扩展扁平 props 列表，也不传入整个全局 store。

现有组件外观与样式先原样迁移。`shared` 仅接收多个 feature 已实际复用的内容；对话消息卡片留在 conversation。

### 5.7 原生工程与启动脚本

核心 Python/前端边界稳定后，再将 `ui/src-tauri/src/lib.rs` 按配置、bridge 命令、sidecar 生命周期拆分；保留 Tauri 命令、externalBin 名称、环境传递和退出清理。

`native/windows-sandbox/src/main.rs` 可按请求协议、AppContainer、进程创建、句柄管理拆分，但不同时改变请求 schema 或权限语义。`run.ps1` 只在新模块路径影响打包时调整资源收集，保留现有 `run/dev/build/headless` 入口。

## 6. 源码迁移映射

| 当前入口 | 目标职责位置 | 迁移方式 |
| --- | --- | --- |
| `main.py`、`automata_api/main.py` | bootstrap + 薄兼容入口 | 保留 create_app 与启动契约，先注入旧实现 |
| `routers/*`、`schemas.py` | transport/http、transport/websocket、contracts/wire | 协议 DTO 与领域对象分开映射 |
| `services/connection.py` | WS connection/sender + RunService/ReplayService | 先委托用例，后移动协议代码 |
| `services/chat.py` | bootstrap/turn_resources + agent 回合入口 + sessions 消息投影 | 资源装配与执行分开，共用 act/plan 路径 |
| `agent/execution/coordinator.py`、`approval.py`、`events.py` | runs/application | 注入 stores、发布器和执行器 |
| `agent/execution/orchestrator.py`、`policy.py` | tools 执行协调与策略 | 通过 ApprovalGateway 请求审批 |
| `agent/execution/process*`、`sandbox/*` | execution | 保留进程与隔离能力，去掉业务层反向依赖 |
| `agent/backends/*`、`tools/_core.py` | workspace、execution、tools/models、builtin | 按能力提取；旧 Backend 暂时作为适配 facade |
| `agent/runtime.py`、`llm.py` | agent/engine、ports、adapters | 注入模型与工具接口，移除具体存储依赖 |
| `repositories/sessions.py`、`agent_store.py` | sessions 接口 + storage/sqlite | 保留上下文访问能力，收窄记录类型 |
| `repositories/runs.py`、`db/*` | runs 规则/端口 + storage/sqlite | SQL 内部拆分，用例事务保持完整 |
| `agent/mcp/*`、`agent/skills/*` | extensions | 移动前解除核心对扩展具体类型的依赖 |
| `hooks/useAgentSocket.ts`、`state/chatReducer.ts` | platform/api + features/runs、conversation | 先提取纯投影，再提取 controller 和连接管理 |
| `hooks/useSessions.ts`、`useSkills.ts` 与对应 API/组件 | features/sessions、skills | 同步迁移测试与公开入口 |

## 7. 分阶段实施计划

每个阶段可拆为多个小 PR；一个 PR 只承担“提取接口”“切换调用”“删除兼容层”中的一个主要目的。文件移动、行为修复和性能优化分别提交。

| 阶段 | 工作及交付物 | 前置与验收门槛 | 回退点 |
| --- | --- | --- | --- |
| M0：契约与边界基线 | 整理 REST/WS、工具结果、错误码和启动配置样例；补足关键行为测试；记录既有违规依赖 | 现有检查通过；正常执行、plan、审批取消、断线回放均有脱敏固定样例 | 只新增测试和契约资产，可独立撤回 |
| M1：集中装配 | 引入容器、配置对象、lifespan 与 Run 资源工厂；先包装现有实现 | M0；两个 app 实例资源隔离；部分启动失败可清理；现有测试不再依赖共享全局状态 | create_app 恢复旧装配；单进程仅启用一个资源图 |
| M2：工作区/工具/进程边界 | 提取 ToolResult、路径/补丁纯函数和能力端口；移走进程能力；替换 Backend.tools | M1；workspace/execution → tools 导入为零；文件/命令/搜索/沙盒结果兼容 | facade 委托回原能力实现，不能绕过授权或隔离 |
| M3：Session/Run 与存储 | 提取用例、状态规则、store 端口，迁移 SQLite 实现；注入 event sink | M1；原子事务、单活动 Run、Plan 幂等、终态和上下文检索测试通过 | 撤回调用切换；schema 不变，无双写 |
| M4：Agent 与扩展 | TurnEngine、ModelProvider、ToolExecutor；统一 act/plan；MCP/Skills 外部装配 | M2、M3；核心可用内存端口测试；无 db/具体后端/扩展实现依赖 | 同一入口切回旧回合实现，不镜像执行真实工具 |
| M5：传输与回放 | WS 只做协议处理；RunService 承接命令；ReplayService 承接恢复 | M3、M4；断线不中止 Run；实时/回放交接无丢失或重复投影；错误协议兼容 | 路由委托旧服务；持久化事件格式保持一致 |
| M6：前端功能模块 | 连接、流控制、投影、reducer 切片；AppShell 接口与 feature 目录整理 | M0 后可先做纯投影，完整联调依赖 M5；行为测试和 build 通过 | 旧 hook 委托新模块，按切片撤回，不同时切换 UI 设计 |
| M7：收尾与原生整理 | 删除转发层/全局实例、收紧依赖检查；更新打包/文档；按需拆原生文件 | 前述阶段通过；全量检查与桌面打包冒烟完成；无旧路径引用 | 保留上一个通过打包验证的提交/产物，按 PR 撤回 |

M2 与 M3 在接口确定后可分开实施；其余核心主线为 M0 → M1 → M2/M3 → M4 → M5 → M7。前端纯函数提取不必等待后端全部搬迁，但不同时改动前后端事件语义。

### 首批可直接执行的三个 PR

- **PR 1：建立约束。** 在现有架构测试上增加按模块的禁用依赖与违规基线；增加跨前后端的回放样例，覆盖重复 seq、缺口和终态；不移动生产代码。
- **PR 2：提取最小能力边界。** 迁移 ToolResult、路径解析和输出截断纯函数；旧路径仅转发；验证 LocalBackend 不再为了这些辅助函数导入 tools。进程执行路径在后续 PR 再切换。
- **PR 3：形成装配闭环。** create_app 注入一组 app 级 RunCoordinator/EventHub/进程管理器及现有 stores；路由通过依赖入口取得服务；测试两个 app 的隔离与关闭顺序。

PR 2 只提前执行 M2 中无运行生命周期变化的纯函数提取；完整 M2 仍在 M1 装配边界建立后进行。

## 8. 兼容性与验证要求

### 8.1 必须保留的行为

| 契约 | 核心断言 | 已有测试入口 |
| --- | --- | --- |
| 桌面与认证 | loopback 限制、HTTP Bearer、WS 首帧 authenticate、Tauri 环境注入和子进程清理保持兼容 | `test_security.py`；桌面集成冒烟 |
| Run 与 Session | 同会话拒绝并发运行；不同会话可并发；断线后继续；取消与进程退出一致 | `test_run_lifecycle.py`、`test_runs.py` |
| Plan 与审批 | 模式限制、审批取消、request_id 幂等、显式重试语义、权限快照 | `test_agent_plan_mode_unit.py`、`test_execution_safety.py`、`test_runs.py` |
| 存储与事件 | 完成消息/Plan/终态原子提交；seq 有序；先存后发；裁剪后旧游标报错 | `test_runs.py`、`test_database_schema.py`、`test_chat.py` |
| 工作区与工具 | 路径与输出约束、补丁 dry_run、搜索 fallback、长进程会话及退出清理 | `test_backends.py`、`test_tools.py`、`test_sandbox.py` |
| 上下文/扩展 | 检索绑定 Session、压缩记录不混同展示消息；MCP/Skills 权限和注入行为一致 | `test_context_search.py`、`test_context_compression.py`、MCP/Skills 测试 |
| 前端状态 | 按 Run 去重、缺口补取、会话切换、消息替换与 Plan/审批展示保持一致 | 现有 reducer/组件测试；补 controller/projection 测试 |

已有测试入口不代表每项断言都有完整覆盖；M0 逐项确认并只补缺口。协议样例只归一化随机 ID、时间等非确定值，不归一化顺序、状态和错误码，以免掩盖回归。

重点补充的测试是：资源隔离与失败清理、SQLite 终态事务故障注入、回放/实时交接竞态、controller 重连与游标去重，以及模块导入约束。业务实现移动后的单测使用端口替身；SQL 语义用真实临时 SQLite，MCP 保留现有本地假服务器测试。

### 8.2 结构验收

- 核心模块的禁用依赖为零；架构检查同时识别绝对、相对和函数内导入，并单独报告 TYPE_CHECKING 引用。
- 迁移中的存量例外逐条记录退出阶段；数量只能减少，新增违规直接失败。最终跨业务模块依赖图无环。
- Agent 核心无具体 DB/HTTP/工作区实现依赖，技术库依赖限于适配器；transport 无 repository/SQL 直接依赖；workspace/execution 无 tools 依赖。
- Run 资源由工厂创建，应用资源由容器创建；禁止生产核心自行取得全局实例。
- 前端 feature 不引用其他 feature 内部文件；跨 feature 编排集中于 app。
- 旧模块的转发层有使用方清单与删除阶段；删除前搜索 import、测试 monkeypatch 字符串和打包资源路径。
- 以新增功能所需修改的模块数、接口可替换性与测试隔离性衡量效果；文件行数只作为复查信号。

Python 可延续当前 AST 测试实现边界检查；前端增加轻量 import 检查并接入测试命令。后续若规则复杂再评估专用依赖工具，不为目录重排预先引入大量工具链。

### 8.3 检查命令

在仓库根目录执行：

```powershell
uv run --directory api --group dev --locked pytest -q
uv run --directory api --group dev --locked ruff check automata_api tests
uv run --directory api --group dev --locked pyright
npm --prefix ui test
npm --prefix ui run build
```

实现阶段根据改动先跑对应测试，每个阶段切换前跑全量检查。涉及原生源码时补对应 Cargo 检查；涉及导入路径、沙盒 worker 或打包收集时执行 `run.ps1 -Mode build`，并验证发布包中 sidecar、file_worker、沙盒宿主可运行。桌面冒烟还应覆盖启动、认证、一轮任务、关闭后无残留进程。

### 8.4 本次实际验证结果

2026-09-18 在上述提交运行的当前基线：

| 检查 | 结果与边界 |
| --- | --- |
| 后端 pytest | `316 passed, 1 skipped`，退出码 0；退出清理 `pytest-current` 时出现 Windows 权限异常，不影响测试断言结果，但不能称无告警 |
| Ruff | 全部通过 |
| Pyright | `0 errors, 0 warnings, 0 informations`；另有工具新版本提示 |
| 前端 Vitest | 6 个文件、14 项测试通过 |
| 前端生产构建 | 成功；主 JS 产物约 600.14 kB，触发大于 500 kB 的分包提示 |

以上为重构前的自动化基线。本次未进行真实模型联网调用、Tauri 发布包启动验证或 Windows 沙盒人工端到端验证；不将测试通过解释为这些运行环境均已验证。构建体积告警可作为独立前端优化任务，不以分层重构承诺包体积或性能收益。

## 9. 风险、回退与完成定义

| 风险 | 控制方式 |
| --- | --- |
| 目录变整齐，但所有调用仍穿透到旧实现 | 每阶段至少切换一个真实调用路径；用禁用依赖与存量例外退出检查约束 |
| 过度抽象导致每次修改跨多个转发层 | 仅在业务边界、外部能力和资源生命周期处设接口；不引入通用 BaseService/BaseRepository |
| 事务被拆开，或两个实现同时写入 | 保留完整存储命令；单次请求只走一个实现；禁止对有副作用路径做双写/镜像执行 |
| 回放事件与实时事件乱序、重复或缺失 | 保持序号、watermark、发送锁与缓冲语义，增加确定性的竞态测试 |
| 共享资源导致权限或工具激活状态串 Run | 明确 app/Run/连接作用域；权限快照和可变工具目录按 Run 隔离 |
| 移动 Python 模块后打包漏收集动态资源 | 检查 file_worker 启动路径、动态导入、PyInstaller 收集和 sidecar 资源；实际构建验证 |
| 前端拆分引入两份状态真相 | 明确 controller 与 reducer 的所有权，投影函数只根据已接受的事件更新 |

每个切换点保留短期兼容 facade，facade 仅单向委托，不维护独立状态。回退以撤回小 PR/恢复已验证装配为主，不要求所有模块长期保留双实现或配置开关；切换运行实现前先结束活动 Run，避免一个 Run 跨越两套资源图。

本轮完成需同时满足：

1. 模块职责和依赖规则在代码中成立，自动检查可以阻止回退。
2. 全量基线与新增关键行为测试通过，协议与数据兼容。
3. `_core.py`、services 中的跨层职责完成迁移，旧转发层清理完毕；不以文件更名代替职责迁移。
4. 新增工具、替换模型适配器、独立测试 Run 用例均不需要改动无关模块。
5. 桌面构建与关键操作冒烟通过，项目说明和打包入口与新结构一致。

## 10. 现状核对入口

本方案以当前源码和本次自动化检查为依据；历史文档只用于了解背景，不作为当前实现已完成迁移的证据。

- [API 工程说明](../api/README.md)、[前端工程说明](../ui/README.md)。
- [应用启动与生命周期](../api/automata_api/main.py)、[连接处理](../api/automata_api/services/connection.py)、[回合装配](../api/automata_api/services/chat.py)。
- [Agent 运行时](../api/automata_api/agent/runtime.py)、[Backend 接口](../api/automata_api/agent/backends/base.py)、[本地 Backend](../api/automata_api/agent/backends/local.py)、[工具公共实现](../api/automata_api/agent/tools/_core.py)。
- [Run 协调](../api/automata_api/agent/execution/coordinator.py)、[事件持久化](../api/automata_api/agent/execution/events.py)、[Run 事务](../api/automata_api/repositories/runs.py)、[现有架构测试](../api/tests/test_agent_boundaries.py)。
- [前端连接与运行控制](../ui/src/hooks/useAgentSocket.ts)、[聊天状态](../ui/src/state/chatReducer.ts)、[前端应用装配](../ui/src/App.tsx)。
- [Tauri sidecar 管理](../ui/src-tauri/src/lib.rs)、[Windows 沙盒宿主](../native/windows-sandbox/src/main.rs)、[启动与打包脚本](../run.ps1)。
