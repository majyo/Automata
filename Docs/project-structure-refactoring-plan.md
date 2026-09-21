# Automata API 分层与模块化结构

> 实施日期：2026-09-20。本文记录本轮 API 重构后的结构与维护规则。
> 应用编排与领域规则合并为业务核心 `core`，不再建立两套职责重叠的目录。

## 1. 层与模块

模块决定业务归属，层决定依赖方向。API 根目录只保留入口、配置与四个主要分组。

| 分组 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| `transport` | HTTP/WS 认证、参数与 DTO、命令解析、错误映射、连接发送 | 模型循环、Run 执行流程、SQL 和操作系统操作 |
| `core` | 会话规则、Run/Plan 用例、Agent 回合、工具权限与路由；定义所需能力接口 | 选择 SQLite、HTTP 客户端、MCP SDK、平台沙盒实现 |
| `infrastructure` | 实现存储、模型请求、工作区、进程、沙盒、扩展与日志能力 | 导入路由或容器，反向驱动业务服务 |
| `bootstrap` | 组装依赖、应用生命周期、每回合资源的创建和释放 | 重新实现业务规则 |

`bootstrap` 是装配入口，不增加一层业务抽象。`config.py` 暂作为共享配置值与环境读取入口；Agent 回合通过注入的 `TurnSettings` 获得模型和压缩设置。

```text
api/automata_api/
├── main.py
├── config.py
├── bootstrap/        container、lifecycle、turn_resources、tools、status
├── transport/
│   ├── http/         会话、Run、MCP、Skills、沙盒、健康检查
│   ├── websocket/    route、connection、commands、sender
│   ├── dependencies.py
│   ├── schemas.py
│   └── security.py
├── core/
│   ├── sessions/     rules、ports、context_sources、search
│   ├── runs/         service、turns、coordinator、approval、events、replay、ports
│   ├── agent/        runtime、turn、context、messages、ports、resources、settings
│   ├── tools/        registry、router、orchestrator、policy、workspace、工具实现
│   ├── telemetry.py
│   └── utils.py
└── infrastructure/
    ├── persistence/  stores、runs、sessions、db/{schema,migrations,...}
    ├── llm/          client、chat_completions
    ├── workspace/    backends、路径操作
    ├── processes/    进程管理、交互会话、输出、平台清理
    ├── sandbox/      平台后端、file_worker、权限准备、安装
    ├── extensions/   MCP、Skills、管理目录
    └── observability/
```

四个业务模块按实际职责划分，内部不重复建立 application/domain/infrastructure 子目录。小模块可以只有几个文件，复杂模块按功能拆分；不为了结构对称创建空目录或转发类。

## 2. 依赖约束

- `core` 不导入 `transport`、`bootstrap`、`infrastructure`，也不导入 FastAPI、HTTPX、SQLite、subprocess 或 MCP SDK。
- `infrastructure` 可以导入 core 的接口、数据类型和纯算法，不能导入 transport 或 bootstrap。
- `transport` 使用 core 服务和 core 定义的事务接口，不能导入具体 infrastructure 实现。
- `transport/dependencies.py` 从 `app.state.container` 取得实例，是协议入口与装配入口的显式连接点。
- `bootstrap` 选择具体实现并注入。业务服务不接收容器，不在内部寻找默认 SQLite store 或模型客户端。
- 工具由 `core/tools/registry.py` 注册；工作区后端只实现能力，不能反向导入具体工具或注册表。

`tests/architecture` 扫描普通、相对、函数内部和 `TYPE_CHECKING` 导入。检查不依赖历史违规白名单，也不会在测试运行时更新基线；新进程导入测试同时验证业务核心不会暗中加载技术适配器。

## 3. 主要调用链

```text
/ws/chat → AgentConnection
  → RunService.start_prompt / start_plan_execution
    → RunCoordinator：创建 Run、审批、取消、持久化终态
      → TurnService：读取会话、选择 act/plan、投影消息、处理回合错误
        → TurnResourcesFactory：打开本回合资源
          → agent runtime / turn：模型循环、压缩、工具调用
            → ModelProvider → infrastructure.llm
            → ToolRouter → 工具 → Workspace Backend / MCP 工具
        → 关闭本回合资源
      → RunStore.finish_run：原子保存结果与终态事件
  ← 持久化事件、广播、断线重连回放
```

连接负责协议，Run 的任务生命周期独立于连接。`TurnService` 合并 act/plan 的共同装配调用与消息处理，具体资源由 `DefaultTurnResourcesFactory` 提供；测试可以替换整个资源工厂，使用内存模型、工具和上下文完成回合。

简单会话 CRUD 与 Run 查询直接调用 core 拥有的事务接口，避免增加只转发一个方法的 service。多步骤 Run 流程集中在 `RunService`、`TurnService` 和 `RunCoordinator`。

## 4. 关键抽象

| 接口/类型 | 归属 | 实现或用途 |
| --- | --- | --- |
| `SessionStore` / `ConversationStore` / `ContextStore` | `core.sessions.ports` | `infrastructure.persistence.stores`；统一上下文存储接口 |
| `RunStore` | `core.runs.ports` | `infrastructure.persistence.runs.SqliteRunStore`；事务级操作 |
| `RunProcesses` | `core.runs.ports` | 进程及交互会话管理器；Run 取消和清理 |
| `ModelProvider` | `core.agent.ports` | Chat Completions 适配器；流式与压缩请求共用入口 |
| `TurnResourcesFactory` | `core.agent.resources` | bootstrap 中的默认实现；异步上下文管理资源生命周期 |
| `Backend` | `core.tools.workspace` | infrastructure 中的工作区实现；文件、搜索、命令与 stdin 能力 |
| `McpCatalog` / `SkillCatalog` / `SandboxAdministration` | `core.tools.management` | 扩展目录和沙盒管理适配器 |
| `Observer` | `core.telemetry` | infrastructure 日志适配器；核心默认无操作观察器 |

`AppContainer` 拥有 coordinator、事件总线、存储和进程管理器。Run 执行时通过上下文绑定它使用的进程管理器，使工具启动与取消清理落到同一组资源。独立底层进程调用仍有默认管理器；日志观察器也保留进程级配置，不能将本轮重构理解为所有配置都已实现多实例隔离。

## 5. 数据和协议兼容

- HTTP 路径、WS 命令、消息字段、错误码和事件回放协议保持原有契约。
- `finish_run` 保留原事务边界，不拆成多个独立提交的 CRUD；Plan 幂等执行与单 Session 活跃 Run 约束继续由事务保障。
- 数据库目录和 schema 不因分层而改变，不清空现有会话、上下文或迁移记录。
- 迁移 1 仅修改了导入路径。校验时将这一处新导入还原到历史拼写，兼容历史 LF/CRLF 校验值；其他源码仍参与校验，修改迁移内容仍失败。
- `api/main.py` 的 file-worker 入口、PyInstaller 迁移源码收集路径、沙盒宿主和工作区路径定位已随新结构更新。
- Python 包内部导入路径已统一迁移；旧目录不保留转发壳。对外兼容指产品协议与数据，不承诺旧内部 Python 导入路径。

## 6. 后续开发的落点

- 新增工具：实现 `core/tools` 中的工具并加入 registry；只有新增工作区能力时才扩展 Backend。
- 新增模型后端：实现 `ModelProvider`，放入 `infrastructure/llm`，在 bootstrap 选择。
- 新增存储后端：实现 core 的存储接口，保持用例级原子性，再在 bootstrap 注入。
- 新增 Run 行为：在 `core/runs` 组织流程和规则；transport 只增加参数或命令映射。
- 修改 MCP、Skills、沙盒或日志实现：进入对应 infrastructure 模块；对上层只暴露 core 所需能力。

## 7. 验证方式与边界

```powershell
uv run --directory api --group dev --locked pytest -q
uv run --directory api --group dev --locked ruff check automata_api tests
uv run --directory api --group dev --locked pyright
npm --prefix ui test
npm --prefix ui run build
```

新增行为验证覆盖：内存依赖完成 act/plan、资源在异常时关闭、模型错误映射、并发 Run 进程资源隔离、历史迁移校验兼容且数据不变。原有生命周期、回放、工具、MCP、会话和协议测试随真实模块迁移，继续验证行为。

本次验证结果：后端 `402 passed, 1 skipped`，前端 `108 passed`；Ruff、Pyright 和前端构建通过。PyInstaller sidecar 构建完成，并通过实际可执行文件的启动、HTTP 认证、会话读写、WS 认证及命令、历史迁移校验值下重启和 file-worker 读取测试。冒烟测试使用独立临时数据库。

跳过项为缺少 Windows sandbox host 时的 AppContainer 集成测试。pytest 退出时仍出现基线已有的临时目录清理权限提示，前端构建仍提示大于 500 KB 的 chunk；两者不影响本次测试和构建结果。

本轮范围为 API 及其打包路径，不改变 UI 设计、引入微服务或启动 Rust 后端迁移。平台沙盒实测、真实供应商网络调用与完整桌面安装运行，需要相应运行环境；不能由单元测试或 sidecar 构建成功替代。
