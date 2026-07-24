# Automata 项目结构报告

> 版本：v0.1.0 | 生成日期：2025-07-14

---

## 1. 项目概览

**Automata** 是一个 LLM 驱动的桌面编码代理。它由 **Python FastAPI 后端**（通过 PyInstaller 打包为侧车进程）和 **Tauri + React 前端**组成。代理人可以在本地工作区内循环执行文件读写、代码搜索、Shell 命令和补丁应用等操作。

| 维度 | 内容 |
|------|------|
| 版本 | v0.1.0 |
| 后端 | Python 3.11+ / FastAPI 0.136 / uvicorn 0.46 / uv 包管理 |
| 前端 | TypeScript / React 19 / Vite 7 / Tauri 2 (Rust) |
| 数据存储 | SQLite (`automata.db`)，按 session 组织 |
| 架构模式 | WS 通信 + Agent 循环 + Plan/Execute 双模式 |

---

## 2. 目录结构树

```
automata/
├── run.ps1                    # PowerShell 入口脚本（主）
├── run.bat                    # Windows cmd 入口（转发到 run.ps1）
│
├── api/                       # Python FastAPI 后端
│   ├── main.py                # uvicorn 入口 / PyInstaller 入口
│   ├── pyproject.toml         # Python 项目配置 + 依赖
│   ├── uv.lock                # uv 锁定文件
│   ├── README.md              # API 说明文档
│   ├── automata_api/          # 核心 Python 包
│   │   ├── main.py            # FastAPI app 工厂、CORS、lifespan
│   │   ├── config.py          # 配置管理（环境变量读取）
│   │   ├── schemas.py         # Pydantic 数据模型
│   │   ├── utils.py           # 工具函数（ID生成、时间戳）
│   │   ├── routers/           # HTTP 和 WebSocket 路由
│   │   │   └── ...
│   │   ├── services/          # API 传输层 + 应用编排
│   │   │   └── ...
│   │   ├── agent/             # Agent 核心运行时
│   │   │   ├── tools/         # 内置工具集
│   │   │   │   └── _core.py   # read_file, write_file, rg, grep,
│   │   │   │                 # exec_command, run_bash, apply_patch,
│   │   │   │                 # apply_patch_preview
│   │   │   ├── execution/     # 执行模型 (CamelToken, RunOutcome)
│   │   │   │   ├── model.py   # 数据类型、错误、token 定义
│   │   │   │   └── event_hub.py  # 广播事件到多连接
│   │   │   ├── mcp/           # MCP (Model Context Protocol) 集成
│   │   │   │   ├── config.py  # MCP 配置解析
│   │   │   │   ├── manager.py # 连接管理
│   │   │   │   ├── policy.py  # 策略引擎
│   │   │   │   └── trust.py   # 信任/授权存储
│   │   │   ├── llm/           # LLM 提供商集成
│   │   │   └── context/       # 上下文压缩
│   │   ├── db/                # SQLite 连接 + schema 初始化
│   │   │   └── migrations/    # SQL schema 迁移
│   │   ├── repositories/      # 数据持久化
│   │   │   ├── sessions.py    # 会话 CRUD
│   │   │   └── agent_store.py # Agent 上下文存储接口
│   │   └── ...
│   └── tests/                 # 后端测试（pytest + TestClient）
│       ├── conftest.py        # 测试夹具
│       ├── test_tools.py      # 工具单元测试
│       ├── test_agent_context_unit.py
│       ├── test_agent_boundaries.py
│       └── ...
│
├── ui/                        # Tauri + React 前端
│   ├── package.json           # npm 配置 + 依赖
│   ├── vite.config.ts         # Vite 构建配置
│   ├── tsconfig.json          # TypeScript 配置
│   ├── index.html             # 入口 HTML
│   ├── src/                   # React 应用源码
│   │   ├── App.tsx            # 主应用组件
│   │   ├── main.tsx           # React 入口
│   │   ├── components/        # UI 组件
│   │   │   ├── app-shell/     # 应用壳 (布局、侧边栏)
│   │   │   └── conversation/  # 对话相关组件
│   │   ├── hooks/             # 自定义 React Hooks
│   │   │   ├── useAgentSocket.ts   # WebSocket 管理
│   │   │   ├── useSessions.ts      # 会话管理
│   │   │   ├── useSkills.ts        # MCP 技能
│   │   │   ├── useApiConfig.ts     # API 配置
│   │   │   ├── useTauriBridge.ts   # Tauri IPC
│   │   │   └── useAutoScroll.ts    # 自动滚动
│   │   ├── state/             # 状态管理
│   │   │   └── chatReducer.ts # Reducer 模式状态管理
│   │   ├── types/             # TypeScript 类型定义
│   │   │   └── chat.ts        # 消息/运行/计划类型
│   │   └── styles/            # CSS 样式
│   │       ├── base.css
│   │       ├── layout.css
│   │       └── components.css
│   └── src-tauri/             # Tauri Rust 壳
│       ├── Cargo.toml         # Rust 依赖
│       ├── Cargo.lock         # Cargo 锁定
│       ├── tauri.conf.json    # Tauri 配置 (窗口、权限、sidecar)
│       ├── build.rs           # Rust 构建脚本
│       ├── src/
│       │   ├── main.rs        # Rust 入口
│       │   └── lib.rs         # Tauri 启动、sidecar 管理、IPC
│       ├── capabilities/
│       │   └── default.json   # 权限声明
│       └── binaries/          # PyInstaller sidecar 二进制
│           └── automata-api-*.exe
│
├── examples/                  # 项目文档和示例
│   └── PROJECT_OVERVIEW.md    # 本报告
│
├── .build/                    # PyInstaller 构建工件
├── .uv-cache/                 # uv 包缓存
└── .data/                     # 本地开发数据目录
```

---

## 3. 后端 (`api/`)

### 3.1 入口文件

- **`api/main.py`** — 兼容层入口。直接 `import uvicorn` 启动服务器，也是 PyInstaller 打包的入口点。绑定 `127.0.0.1:8765`，使用 h11 HTTP 和 websockets WS 协议。

### 3.2 核心包 (`automata_api/`)

| 子模块 | 文件 | 说明 |
|--------|------|------|
| **App 工厂** | `main.py` | 创建 FastAPI app，配置 CORS、middleware、lifespan 事件 |
| **配置** | `config.py` | 从环境变量读取配置（host、port、LLM、压缩等） |
| **数据模型** | `schemas.py` | Pydantic 模型：Session、Message、Run、Plan、MCP Grant |
| **工具** | `utils.py` | `new_id()`、`now_iso()`、`normalize_title()` |
| **路由** | `routers/` | HTTP 端点（health, sessions, messages）+ WebSocket `/ws/chat` |
| **服务** | `services/` | API 传输层，编排 agent 调用 |
| **数据库** | `db/` | SQLite 连接管理 + schema 迁移 |
| **持久化** | `repositories/` | Session / Message / AgentContext 存储接口 |

### 3.3 Agent 运行时 (`agent/`)

核心 AI 代理引擎，独立于 API 传输层（禁止依赖 FastAPI 和 routers/services）。

| 子模块 | 文件 | 说明 |
|--------|------|------|
| **工具** | `tools/_core.py` (~2200 行) | 8 个内置工具：`read_file`、`write_file`、`rg`、`grep`、`exec_command`、`run_bash`、`apply_patch`、`apply_patch_preview` |
| **执行** | `execution/model.py` | `CamelToken`、`ToolRisk`、`RunOutcome`、`ApprovalRequest`、`ToolExecutionContext` |
| **执行** | `execution/event_hub.py` | `RunEventHub` — 广播事件到多 WebSocket 连接 |
| **MCP** | `mcp/config.py` | MCP 服务器配置解析（stdio / streamable_http） |
| **MCP** | `mcp/manager.py` | `McpConnectionManager` — MCP 连接生命周期 |
| **MCP** | `mcp/policy.py` | `McpPolicyEngine` — 工具调用策略决策 |
| **MCP** | `mcp/trust.py` | `McpTrustStore` — 服务器信任/授权持久化 (JSON) |
| **LLM** | `llm/` | LLM 提供商集成（兼容 DeepSeek API） |
| **上下文** | `context/` | 上下文压缩：当超过阈值时调用 LLM 生成摘要，注入后续请求 |

#### 内置工具速览

| 工具 | 风险等级 | 功能 |
|------|----------|------|
| `read_file` | read | 读取工作区内的 UTF-8 文本文件 |
| `write_file` | write | 写入/创建/追加 UTF-8 文件 |
| `rg` | read | 文件搜索（优先用 ripgrep，回退到 grep） |
| `grep` | read | 标准 grep 搜索 |
| `exec_command` | command | 执行 shell 命令（支持 PTY 模式 yield_time_ms） |
| `run_bash` | command | bash 命令兼容工具 |
| `apply_patch` | write | 应用 Codex 风格补丁（支持 dry_run） |
| `apply_patch_preview` | read | 预览补丁效果（强制 dry_run=true） |

### 3.4 测试 (`tests/`)

| 文件 | 覆盖范围 |
|------|----------|
| `conftest.py` | TestClient 夹具 + env monkeypatching |
| `test_tools.py` (~1050 行) | 8 个内置工具的完整单元测试（包含边界情况） |
| `test_agent_context_unit.py` | 上下文压缩逻辑单元测试 |
| `test_agent_boundaries.py` | Agent 包边界测试：禁止 import FastAPI/services/routers |

---

## 4. 前端 (`ui/`)

### 4.1 Tauri 桌面壳 (`src-tauri/`)

- **Rust 主程序** (`lib.rs`) — 启动时生成随机 API token（≥32 字符），通过 `tauri-plugin-shell` 启动 Python sidecar (`automata-api`)，暴露 IPC 命令 `agent_status` 和 `api_config` 给 React 前端。
- **sidecar 管理** — 监听 `automata-api-{target_triple}.exe`，进程生命周期绑定窗口 (close → kill sidecar)。
- **配置** (`tauri.conf.json`) — 窗口 1180×760，CSP 允许 127.0.0.1 WS 连接，声明 `externalBin: ["binaries/automata-api"]`。

### 4.2 React 应用 (`src/`)

| 层级 | 路径 | 说明 |
|------|------|------|
| **入口** | `main.tsx` | React DOM 挂载 |
| **主组件** | `App.tsx` | 所有状态和 hooks 的顶层编排 |
| **组件** | `components/app-shell/` | 布局壳：侧边栏、会话列表、工作目录选择 |
| **组件** | `components/conversation/` | 对话视图、消息渲染、ToolCard 等 |
| **Hooks** | `useAgentSocket.ts` (~720 行) | WebSocket 连接、认证、prompt 发送、plan 审批、cancel、重连 |
| **Hooks** | `useSessions.ts` | 会话 CRUD、active session 管理、工作目录 |
| **Hooks** | `useSkills.ts` | MCP 技能发现和选择 |
| **Hooks** | `useApiConfig.ts` | 从 Tauri IPC 或 localhost 获取 API 配置 |
| **Hooks** | `useTauriBridge.ts` | Tauri 桥接状态检测 |
| **Hooks** | `useAutoScroll.ts` | 消息列表自动滚动 |
| **State** | `chatReducer.ts` | Reducer 驱动：runs、messages、approvals 集中管理 |
| **Types** | `chat.ts` | `ChatMessage`、`RunStatus`、`SendMode`、`ApprovalDecision` 等类型 |
| **Styles** | `base.css / layout.css / components.css` | 三文件分层 CSS |

### 4.3 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 框架 | React | ^19.1.0 |
| 构建 | Vite | ^7.0.4 |
| 语言 | TypeScript | ~5.8.3 |
| 桌面 | Tauri (Rust) | 2.x |
| 测试 | Vitest + Testing Library | ^3.2.7 / ^16.3.2 |
| 图标 | Lucide React | ^1.14.0 |

---

## 5. 构建和运行

### 5.1 入口脚本

| 文件 | 平台 | 说明 |
|------|------|------|
| `run.ps1` | Windows PowerShell | 主入口，支持 4 种模式 |
| `run.bat` | Windows cmd | 转发到 `pwsh -File run.ps1` |

### 5.2 运行模式

```powershell
.\run.ps1           # 默认 = run：启动 Tauri 桌面应用
.\run.ps1 dev       # 开发模式：前端热重载 + sidecar sync
.\run.ps1 build     # 生产构建
.\run.ps1 headless  # 仅启动 API 后端（无 UI，需 AUTOMATA_API_TOKEN）
```

### 5.3 Sidecar 构建流程

1. **PyInstaller** 将 `api/` 打包为单文件 `automata-api.exe`
2. 输出到 `ui/src-tauri/binaries/automata-api-{target_triple}.exe`
3. Tauri 构建时将 sidecar 嵌入 bundle
4. 运行时 Tauri 通过 `tauri-plugin-shell` 启动 sidecar 子进程

```bash
# 开发时后端独立启动
cd api && uv sync && uv run uvicorn main:app --host 127.0.0.1 --port 8765 --reload

# 运行测试
uv run --directory api --group dev --locked pytest
```

---

## 6. API 端点

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| `GET` | `/health` | 无 | 健康检查 |
| `GET` | `/sessions` | Bearer Token | 列出所有会话 |
| `POST` | `/sessions` | Bearer Token | 创建新会话 |
| `PATCH` | `/sessions/{session_id}` | Bearer Token | 更新会话标题 |
| `DELETE` | `/sessions/{session_id}` | Bearer Token | 删除会话 |
| `GET` | `/sessions/{session_id}/messages` | Bearer Token | 获取会话消息列表 |
| `WS` | `/ws/chat` | 首帧 authenticate | Agent 对话 WebSocket |

WebSocket 事件流：`started` → `agent_step` → `tool_call`/`tool_result` → `token` (delta) → `done`

---

## 7. Plan 模式

Automata 支持 **Plan → Approve → Execute** 工作流：

### 7.1 流程

1. **Plan** — 发送 `{ type: "prompt", mode: "plan" }` → Agent 生成执行计划，**不执行写操作**
2. **Approve** — 前端收到 `plan_ready` 事件，展示计划 → 用户审批
3. **Execute** — 发送 `{ type: "approve_plan" }` → Agent 按计划执行，可调用所有工具

### 7.2 Plan 状态机

```
pending → approving → executed
                   → failed（可 retry）
pending → superseded（新 plan 替代旧 plan）
```

### 7.3 Retry 机制

- 失败的 plan 可通过 `{ type: "retry_plan", confirm_possible_duplicate_side_effects: true }` 重试
- 重试前前端弹出确认对话框（可能有重复副作用）

---

## 8. 关键配置变量

### 8.1 LLM 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTOMATA_LLM_API_KEY` | **必填** | LLM API 密钥 |
| `AUTOMATA_LLM_BASE_URL` | `https://api.deepseek.com` | API 基础 URL |
| `AUTOMATA_LLM_MODEL` | `deepseek-v4-pro` | 模型名称 |
| `AUTOMATA_LLM_TIMEOUT_SECONDS` | `120` | 请求超时 |
| `AUTOMATA_LLM_TEMPERATURE` | `0.2` | 温度参数 |

### 8.2 上下文压缩

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTOMATA_CONTEXT_COMPRESSION_ENABLED` | `true` | 启用压缩 |
| `AUTOMATA_CONTEXT_MAX_TOKENS` | `1000000` | 模型上下文 token 上限 |
| `AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO` | `0.8` | 触发阈值比例 |
| `AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS` | 自动计算 | 精确字符阈值（约 3,200,000） |
| `AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS` | `20000` | 摘要目标字符数 |

### 8.3 API 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTOMATA_API_HOST` | `127.0.0.1` | API 绑定地址（仅 loopback） |
| `AUTOMATA_API_PORT` | `8765` | API 端口 |
| `AUTOMATA_API_TOKEN` | — | 认证令牌（headless 模式必填，≥32 字符） |
| `AUTOMATA_WORKSPACE_DIR` | 项目根目录 | 工作区路径 |
| `AUTOMATA_DATA_DIR` | `api/.data` | SQLite 数据目录 |
| `AUTOMATA_ENV_FILE` | — | 自定义 .env 文件路径 |

---

## 9. 架构决策记录 (ADR)

1. **Agent 层隔离** — `agent/` 包禁止 import FastAPI、routers、services，确保纯逻辑测试和可移植性
2. **Sidecar 架构** — Python 后端作为 Tauri sidecar 进程，避免 Rust↔Python FFI 复杂度
3. **双模式执行** — Plan/Execute 分离：plan 模式只读分析，execute 模式运行含写操作的完整计划
4. **事件驱动 WS** — 所有 agent 步骤通过 WebSocket 实时流式传输（tool_call、tool_result、token delta）
5. **工具策略引擎** — 根据风险等级 (read/write/command/destructive/external) 实施可配置的 allow/prompt/deny 策略
