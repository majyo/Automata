# Automata 运行期工具发现与注册设计

## 背景

Automata 已经有 `Backend` 抽象层：`LocalBackend`、`WindowsBackend` 负责文件、命令、搜索等执行原语，并通过 `backend.tools()` 组装当前 backend 的核心工具。这个抽象解决了“不同后台有不同执行能力”的问题，但 agent runtime 仍然把工具当成一次性静态注入：

- `services/chat.py` 按 session 创建 backend 后立即构造一份工具集合。
- `runtime.py` 在 agent loop 开始时把工具 specs 传给模型，后续步骤没有重新发现或调整工具面。
- 外部工具、插件工具或未来 MCP 工具如果数量较多，只能全量暴露或完全不可用。

Codex 的源码提供了更合适的结构参考：`ToolRegistry` 负责执行索引，`ToolRouter` 负责每个 turn 的模型可见工具面，`ToolExposure` 区分 direct/deferred/hidden，`tool_search` 用来发现 deferred 工具。Automata 本方案采用同样的分层思想，但适配当前 OpenAI-compatible Chat Completions 协议，不照搬 Responses API 的 namespace/defer wire shape。

## 目标

1. 保留现有 `Backend` 抽象和核心工具行为，避免影响 local/windows 会话。
2. 新增运行期工具发现层，允许多个 `ToolProvider` 贡献工具。
3. 新增 `ToolRouter`，把“可执行工具全集”和“本轮模型可见工具集”拆开。
4. 支持 deferred 工具：初始不暴露给模型，通过 `tool_search` 命中后在下一轮模型调用中暴露。
5. Plan mode 继续只允许 read-only 工具，并且只允许搜索 read-only deferred 工具。
6. 不新增数据库迁移；工具发现和激活状态是 turn-scoped runtime state。

## 核心模型

新增工具模型位于 `api/automata_api/agent/tools/model.py`：

```python
class ToolExposure(str, Enum):
    DIRECT = "direct"
    DEFERRED = "deferred"
    HIDDEN = "hidden"

@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    spec: dict[str, Any]
    executor: AgentTool
    read_only: bool
    exposure: ToolExposure
    source: str
    search_text: str | None

@dataclass(frozen=True)
class ToolDiscoveryContext:
    session_id: str | None
    workspace: str | None
    backend: Backend | None
    mode: str
    config: Any
```

`ToolProvider.discover(context)` 返回一组 `ToolDescriptor`。MVP 的 `BackendToolProvider` 只是把当前 `backend.tools()` 包装成 direct descriptors，因此现有工具默认行为不变。未来接 MCP、插件或动态工具时，只需要新增 provider，不需要改 runtime 主循环。

## ToolRouter

`ToolRouter` 位于 `api/automata_api/agent/tools/router.py`，职责是：

- 构建执行索引：内部仍使用现有 `ToolRegistry`。
- 生成模型可见工具：`model_visible_specs(mode=...)`。
- 生成 plan mode 允许工具名：`allowed_names(mode="plan")`。
- 执行工具：`dispatch(name, args, mode=...)`。
- 管理 deferred 工具激活状态。

曝光规则：

- `direct`：立即进入模型可见工具列表；plan mode 下还必须 `read_only=True`。
- `deferred`：注册为可执行工具，但初始不进入模型可见工具列表；`tool_search` 命中后加入本次 agent loop 的 activated set，下一次模型调用时才暴露。
- `hidden`：注册后不进入模型可见工具列表，也不通过 `tool_search` 搜索。

未激活 deferred 工具被直接调用时返回稳定错误：

```json
{
  "simulated": false,
  "ok": false,
  "tool": "calendar_lookup",
  "error": "tool_not_loaded",
  "hint": "Use tool_search before calling deferred tool: calendar_lookup"
}
```

Plan mode 中 mutating 工具被调用时继续返回现有语义：

```json
{
  "simulated": false,
  "ok": false,
  "tool": "write_file",
  "mode": "plan",
  "error": "blocked_by_plan_mode",
  "allowed_tools": ["read_file", "rg", "tool_search"]
}
```

## tool_search

`tool_search` 位于 `api/automata_api/agent/tools/tool_search.py`，作为普通 function tool 暴露给 Chat Completions：

```json
{
  "type": "function",
  "function": {
    "name": "tool_search",
    "description": "Search tools that are available at runtime but not loaded...",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string" },
        "limit": { "type": "integer", "minimum": 1, "maximum": 20 }
      },
      "required": ["query"]
    }
  }
}
```

Automata MVP 不引入 BM25 依赖，先使用轻量 token matching：

- 搜索目标包含工具名、描述、参数名、schema description 和 provider 提供的 `search_text`。
- `limit` 默认 8，最大 20。
- 命中的 deferred 工具会被立即激活，但只在下一次模型请求中出现在 tools 列表。
- plan mode 的候选集只包含 read-only deferred 工具。

## Runtime 接入

正式 WebSocket 路径现在按 session 构建 router：

```text
services/chat.py
  -> create_backend(...)
  -> ToolRouter.from_backend(backend, session_id, workspace, mode)
  -> stream_agent_loop(..., router=router)
```

`runtime.stream_model_loop()` 每一步调用模型前重新读取：

```python
current_tools = router.model_visible_specs(mode=mode)
async for delta in llm.stream_chat_completion(messages, tools=current_tools):
    ...
```

因此当模型第一轮调用 `tool_search` 后，第二轮会看到 newly activated 工具。无 router 的旧路径仍保留，便于低层单元测试和兼容旧调用者。

## 与 Skills 的边界

`Docs/skills-system-design.md` 中的 skills 仍是上下文指令层：它们告诉模型如何组织工作、读哪些参考、运行哪些脚本。工具发现层只处理“模型可调用动作”。两者不混合：

- skill 不直接注册 tool。
- tool provider 不注入长指令。
- skill 需要的实际动作仍通过已有 tools/backend 策略执行。

## 测试覆盖

新增或调整的测试覆盖：

- backend 工具默认 direct，模型可见 specs 与既有 backend specs 一致。
- duplicate tool name 拒绝注册。
- deferred 工具初始不可见，直接调用返回 `tool_not_loaded`。
- `tool_search` 命中 deferred 工具后，下一轮 specs 包含该工具。
- hidden 工具不进入模型可见列表。
- plan mode 过滤 mutating direct 工具，只搜索 read-only deferred 工具。
- runtime 在 tool_search 后重新生成 tools，并成功执行新激活工具。

推荐回归命令：

```powershell
uv run --directory api --group dev --locked pytest tests/test_agent_tool_router_unit.py tests/test_agent_runtime_unit.py tests/test_agent_plan_mode_unit.py
uv run --directory api --group dev --locked pytest
```

## 后续扩展

- `McpToolProvider`：连接 MCP server，转换 tool schema 为 `ToolDescriptor`，默认按数量或配置选择 direct/deferred。
- `PluginToolProvider`：插件贡献工具，不改 runtime 主循环。
- Provider warning/event：发现失败、重复工具、跳过工具可通过 WebSocket 调试事件暴露。
- 更强的搜索排序：如工具数量增长明显，可把 `tool_search` 的简单 token matching 替换为 BM25。
