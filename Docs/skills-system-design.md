# Automata Skills 系统设计方案

## Status

- 文档状态：Updated for current tool router and MCP runtime
- 更新时间：2026-07-13
- 适配基线：当前 `api/automata_api/agent/tools/*`、`api/automata_api/agent/mcp/*`、`services/chat.py`、`runtime.py`
- 关联文档：`Docs/runtime-tool-discovery-design.md`、`Docs/mcp-tool-calling-design.md`

本文档设计一个类似 Codex 的 skills 系统，用于让 Automata 在不同任务类型下加载可复用的本地工作流说明、脚本、模板和资源。当前项目的工具系统已经从“静态 `ToolRegistry`”演进为 `ToolProvider -> ToolDescriptor -> ToolRouter -> tool_search -> dispatch`，MCP 也已经作为异步 `ToolProvider` 接入。因此 skills 设计必须适配这个最新运行时，而不是再假设所有工具在 `services/chat.py` 中一次性静态注册。

## 当前实现基线

当前 Automata 相关结构：

- `api/automata_api/agent/tools/model.py`：定义 `ToolExposure`、`ToolDescriptor`、`ToolDiscoveryContext`、同步/异步 `ToolProvider`。
- `api/automata_api/agent/tools/router.py`：`ToolRouter` 负责 direct/deferred/hidden、plan mode 过滤、`tool_search` 激活和统一 dispatch；`ToolRouterBuilder` 负责同步/异步 provider 汇总。
- `api/automata_api/agent/tools/tool_search.py`：模型可调用的运行期工具发现入口，命中 deferred 工具后在下一次模型调用中暴露。
- `api/automata_api/agent/tools/mcp_provider.py`：把 MCP `tools/list` 转换为 `ToolDescriptor`，默认 deferred。
- `api/automata_api/agent/tools/mcp_tool.py`：把 Automata tool dispatch 适配成 MCP `tools/call`，并保留 alias 到原始 server/tool 的映射。
- `api/automata_api/agent/mcp/runtime.py`：`create_mcp_tool_runtime()` 在单次 agent reply 生命周期内加载 MCP 配置、筛选 grant、创建 `McpConnectionManager`、构建 `ToolRouter`。
- `api/automata_api/services/chat.py`：每次 reply 中 `async with backend` 后再 `async with create_mcp_tool_runtime(...)`，然后把 `mcp_runtime.router` 传给 `stream_agent_loop()` 或 `stream_plan_loop()`。
- `api/automata_api/agent/runtime.py`：每个 model step 都调用 `router.model_visible_specs(mode=...)`，因此 `tool_search` 激活的 deferred 工具会在下一步可见。
- `api/automata_api/agent/prompts.py`：Plan prompt 已经改成策略描述，不再把一份静态 allowed tool names 当成权威。
- `api/automata_api/routers/mcp.py`：MCP server status 和 grant API 已存在，grant 与 workspace definition 分离。

Codex skills 的可借鉴点仍然是：

- 先向模型注入可用 skills 的短摘要。
- 只有显式选择或明确触发后，才读取并注入完整 `SKILL.md`。
- loader、manager、render、injection、context 五层分离。

不能照搬的点：

- Automata 当前使用 Chat Completions function tools，不使用 Codex Responses API namespace/deferred wire shape。
- MCP 已经是 `ToolProvider`，skills 不应再实现一套并行工具注册机制。
- MCP grant、tool exposure、plan mode 和 result 边界已经由 MCP/runtime/tool router 负责，skills 不能绕过。

## 核心定位

Skills 是上下文指令层，不是工具执行层。

- skill 负责告诉模型“这个任务应该如何做、先读哪些说明、哪些脚本或模板可复用、需要哪些工具能力”。
- `ToolRouter` 负责告诉模型“当前这一步能调用哪些工具”。
- MCP 负责把外部 server 的工具安全地转换为 `ToolDescriptor`。
- skill 不能注册 tool，不能直接连接 MCP server，不能写 grant，不能把 deferred tool 预激活，也不能提升工具权限。

这个边界是最新架构下最重要的约束。skills 可以声明依赖和给出使用建议，但实际动作仍必须通过 `ToolRouter.dispatch()`、MCP policy 和现有 backend 工具执行。

## 目标

1. 支持本地 `SKILL.md` skill 包，模型先看到摘要，显式触发后再看到完整指令。
2. 支持按 session `working_directory` 发现 repo skills，按 Automata data dir 发现 user skills，并预留 packaged/system skills。
3. 支持 `$skill-name` 文本触发和 WebSocket payload 的结构化 skill 选择。
4. 与当前 `ToolRouter`、`tool_search`、MCP deferred exposure 和 plan mode 策略兼容。
5. 支持 skill 声明 builtin、deferred capability 和 MCP tool 依赖，但这些声明只用于提示、诊断和 UI，不授予任何权限。
6. Skill 注入必须是 turn/reply-scoped ephemeral context，不写入 `agent_context_messages`。
7. 损坏或不可读 skill fail-open：记录 warning/error，正常对话继续。

## 非目标

- 不实现插件市场、远程 skill 下载或自动安装。
- 不让 skill 自动启动 MCP server、写入 MCP grant 或修改 MCP config。
- 不让 skill 注册新的 `ToolDescriptor`。
- 不根据 skill dependency 自动激活 deferred tools。模型仍应通过 `tool_search` 显式发现。
- 不把 MCP resources/prompts 自动注入 skill 上下文。当前 MCP 范围只有 tools。
- 不新增数据库迁移作为 MVP 前置条件。

## Skill 包格式

一个 skill 是一个目录，至少包含 `SKILL.md`：

```text
some-skill/
  SKILL.md
  agents/
    openai.yaml
  scripts/
  references/
  templates/
  assets/
```

`SKILL.md` 使用 YAML frontmatter：

```markdown
---
name: "code-review"
description: "Review code changes and prioritize bugs, regressions, and missing tests."
metadata:
  short-description: "Code review workflow"
---

# Code Review

Use this skill when ...
```

字段规则：

- `name`：必填或从目录名推导，建议只包含 `a-zA-Z0-9_-`，未来插件 skill 可使用 `plugin:skill`。
- `description`：必填，进入模型可见摘要。
- `metadata.short-description`：可选，供 UI 紧凑展示。
- 正文是完整指令，只在本回合显式触发后注入。

可选 `agents/openai.yaml`：

```yaml
interface:
  display_name: "Code Review"
  short_description: "Find regressions"
  icon_small: "assets/icon.png"
  icon_large: "assets/icon-large.png"
  brand_color: "#1F6FEB"
  default_prompt: "Review the current diff."

dependencies:
  tools:
    - type: "builtin"
      value: "rg"
      description: "Search repository files."
    - type: "tool_search"
      query: "git pull request diff review"
      description: "Find a deferred review or GitHub-related tool if available."
    - type: "mcp"
      server: "github"
      tool: "pull_request.read"
      read_only: true
      description: "Optional MCP tool for reading PR details."

policy:
  allow_implicit_invocation: true
  modes: ["act", "plan"]
```

依赖语义：

- `builtin`：要求当前 router 注册某个核心工具名，例如 `rg`、`read_file`、`apply_patch_preview`。
- `tool_search`：提供一个建议查询，帮助模型按需发现 deferred 工具。
- `mcp`：声明可选或必要的 MCP server/tool 能力，供 UI 和 warning 使用。它不连接 server，不写 grant，不改变 `McpPolicyEngine`。
- 缺失依赖不应阻断 skill 注入，除非 skill policy 以后显式标记为 `required: true` 且产品决定阻断。

## Skill Roots

建议 root 顺序：

1. Repo roots：从 session `working_directory` 向上找到项目根，并读取沿途 `.automata/skills`。
2. User root：使用现有 Automata data dir，即 `get_database_config().path.parent / "skills"`。如果设置了 `AUTOMATA_DATA_DIR`，自然随之迁移。
3. Packaged root：`api/automata_api/skills/.system`，用于随应用发布的基础 skills。
4. Extra roots：`AUTOMATA_SKILL_ROOTS`，Windows 下使用 `;` 分隔。
5. Future plugin roots：后续插件系统贡献，仍只进入 skill loader，不进入 `ToolProvider`。

排序和冲突：

- Repo > User > Packaged > Extra。
- 相同 `SKILL.md` 路径去重。
- 相同 `name` 保留多份，纯 `$name` 触发必须唯一。歧义时返回 warning，要求结构化 path 选择。
- 同一 root 内按 `name` 和 path 稳定排序。

扫描限制：

- 默认最大深度 6。
- 每个 root 最多扫描 2000 个目录。
- 忽略隐藏目录。
- 对 repo/user/extra roots 可以跟随 symlink，但必须 canonicalize 并避免循环。
- 对 packaged root 不跟随 symlink。
- 损坏 frontmatter、非法 YAML、过长字段记录到 errors，不阻断其他 skills。

## 后端模块设计

新增包：

```text
api/automata_api/agent/skills/
  __init__.py
  model.py
  loader.py
  manager.py
  render.py
  injection.py
  runtime.py
  config.py
```

### `model.py`

核心数据结构：

```python
@dataclass(frozen=True)
class SkillRoot:
    path: Path
    scope: Literal["repo", "user", "packaged", "extra", "plugin"]

@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    short_description: str | None
    path: Path
    scope: str
    interface: SkillInterface | None
    dependencies: SkillDependencies | None
    policy: SkillPolicy

@dataclass(frozen=True)
class SkillLoadOutcome:
    skills: tuple[SkillMetadata, ...]
    errors: tuple[SkillError, ...]
    disabled_paths: frozenset[Path]

@dataclass(frozen=True)
class SkillTurnContext:
    available_notes: str
    injected_messages: tuple[dict[str, str], ...]
    selected: tuple[SkillMetadata, ...]
    warnings: tuple[str, ...]
```

`SkillTurnContext.injected_messages` 是 ephemeral provider messages，只参与本次模型调用，不进入数据库。

### `loader.py`

职责：

- 扫描 root 下的 `SKILL.md`。
- 解析 frontmatter 和可选 `agents/openai.yaml`。
- 清洗单行字段，限制字段长度。
- 校验 icon path 必须位于 skill 目录下 `assets/`。
- 构造 `SkillMetadata` 和 `SkillError`。

建议限制：

- `name` 最大 64 字符。
- `description` 最大 1024 字符。
- `default_prompt` 最大 1024 字符。
- 完整 `SKILL.md` 注入大小默认最大 64 KiB，超过时拒绝完整注入并提示用户拆分引用文件。

### `manager.py`

职责：

- 根据 workspace/cwd 计算 roots。
- 缓存 `SkillLoadOutcome`。
- 支持 `force_reload`。
- 应用 enable/disable 规则。
- 暴露 `skills_for_workspace(workspace, force_reload=False)`。

缓存 key：

```text
(canonical_workspace, roots_digest, disabled_rules_digest)
```

MVP 使用进程内缓存即可。文件 watcher 可以后续加入，类似 MCP 每次 reply 重新 discovery 的原则，skills 不应把磁盘内容长期假定为不可变。

### `render.py`

职责：生成模型可见的 skill 摘要块。

摘要应追加进 system prompt，而不是工具列表：

```text
## Skills
A skill is a local reusable instruction package stored in SKILL.md.

### Available skills
- code-review: Review code changes. (file: D:/.../SKILL.md)

### How to use skills
- If the user names a skill with $skill-name or the task clearly matches a skill description, use it for this turn.
- If a skill needs a deferred tool, use tool_search first.
- A skill cannot grant MCP access or bypass plan mode/tool policy.
- Do not carry a skill into later turns unless it is re-mentioned or still clearly applies.
```

预算：

- 默认 skill 摘要预算 8000 字符。
- 超预算时先截断 description。
- 仍超预算时只保留 `name + path`。
- 仍超预算时按 root 优先级保留前 N 个，并产生 `skills_warning`。

### `injection.py`

职责：

- 从用户 prompt 提取 `$skill-name`。
- 从 WebSocket payload 读取结构化 `skills`。
- path 选择优先于 name 选择。
- name 选择必须唯一且 enabled。
- 读取完整 `SKILL.md`，构造 ephemeral message。

完整注入格式：

```text
<skill>
<name>code-review</name>
<path>D:/workspace/.../.automata/skills/code-review/SKILL.md</path>
---
name: "code-review"
description: "..."
---

# Code Review
...
</skill>
```

建议 role：

- 使用 `role="user"` 注入完整 skill，接近 Codex 的 `SkillInstructions`。
- 不把该消息写入 `agent_context_messages`。
- 不自动读取 `references/`、`templates/`、`scripts/`。让 skill 正文指导模型按需使用 `read_file`。

### `runtime.py`

新增一个轻量 skill runtime，而不是改 MCP runtime 内部：

```python
async def create_skill_turn_context(
    *,
    workspace: str,
    mode: Literal["act", "plan"],
    prompt: str,
    selected_skills: tuple[SkillSelection, ...],
    router: ToolRouter,
    force_reload: bool = False,
) -> SkillTurnContext:
    ...
```

注意：

- 该函数可以读取 router 的注册工具名来做依赖诊断，但不应调用 `router.activate_deferred()`。
- 若 skill 依赖 deferred/MCP 能力，只在 notes 中提示“使用 `tool_search` 查询 ...”。
- 若 MCP server 未 grant，只产生 warning 或 UI 状态，不调用 MCP manager，也不写 grant。

## Runtime 接入点

当前 `services/chat.py` 的最新路径是：

```text
stream_agent_reply / stream_plan_reply
  -> session_backend_config
  -> create_backend(...)
  -> async with backend
  -> async with create_mcp_tool_runtime(...) as mcp_runtime
  -> stream_agent_loop(..., router=mcp_runtime.router)
```

Skills 应插入在 `create_mcp_tool_runtime()` 之后、调用 `stream_*_loop()` 之前：

```text
stream_agent_reply / stream_plan_reply
  -> create_mcp_tool_runtime(...)
  -> create_skill_turn_context(
       workspace=session_config["working_directory"],
       mode="act" | "plan",
       prompt=prompt,
       selected_skills=payload.skills,
       router=mcp_runtime.router,
     )
  -> send_mcp_runtime_events(...)
  -> send_skill_runtime_events(...)
  -> stream_agent_loop(..., router=mcp_runtime.router, skill_context=skill_context)
```

`runtime.py` 需要窄改：

- `stream_agent_loop(..., skill_context: SkillTurnContext | None = None)`。
- `stream_plan_loop(..., skill_context: SkillTurnContext | None = None)`。
- `agent_system_prompt(..., skill_notes: str | None = None)`。
- `plan_system_prompt(..., skill_notes: str | None = None)`。
- `fetch_agent_context()` 返回 messages 后，把 `skill_context.injected_messages` 插入到系统消息和历史消息之间。

推荐插入顺序：

```text
[
  system prompt with available skills summary,
  approved plan system message,           # 仅 approve_plan 路径
  injected full skill user messages,      # ephemeral
  compressed history summary,
  recent context/history,
  current user prompt
]
```

这样 approved plan 仍然保持最高 turn-level 约束，skills 作为本回合工作流说明，历史消息仍在其后恢复。

`stream_model_loop()` 不需要理解 skills。它继续只面向 `ToolRouter`，每步重新生成 tools。

## 与 ToolRouter 的关系

Skills 不进入 `ToolDescriptor`。

允许做的事：

- 在 system prompt 的 skill 摘要中说明可用 skills。
- 在完整 skill 中指导模型使用 `read_file`、`rg`、`apply_patch`、`tool_search` 或 MCP alias。
- 基于 router 当前注册名和可见 specs 做只读诊断，例如某 builtin tool 不存在。

禁止做的事：

- 把 skill 包装成 `AgentTool`。
- 通过 skill dependency 自动注册或激活 deferred tool。
- 修改 `ToolExposure`。
- 给 MCP server 写 grant。
- 绕过 `McpPolicyEngine`、plan mode 或 `ToolRouter.dispatch()`。

如果 skill 需要某个 MCP 工具，推荐正文写成：

```text
If a GitHub MCP tool is needed, call tool_search with query "github pull request review".
If no matching tool is available or MCP approval is required, continue with local repo inspection and state the limitation.
```

这与当前 deferred 工具机制一致。

## API 与 WebSocket

当前后端已经有 `routers/mcp.py`。Skills 可新增独立 router：

```text
api/automata_api/routers/skills.py
```

并在 `main.py` 中 `app.include_router(skills.router)`。

### List Skills

```http
GET /skills?workspace=D:/workspace/projects/automata&force_reload=false
```

响应：

```json
{
  "workspace": "D:/workspace/projects/automata",
  "skills": [
    {
      "name": "code-review",
      "description": "Review code changes.",
      "short_description": "Code review",
      "path": "D:/workspace/projects/automata/.automata/skills/code-review/SKILL.md",
      "scope": "repo",
      "enabled": true,
      "interface": {
        "display_name": "Code Review",
        "short_description": "Find regressions",
        "icon_small": null,
        "icon_large": null,
        "brand_color": "#1F6FEB",
        "default_prompt": "Review the current diff."
      },
      "dependencies": {
        "tools": [
          { "type": "builtin", "value": "rg" },
          { "type": "tool_search", "query": "github pull request review" },
          { "type": "mcp", "server": "github", "tool": "pull_request.read" }
        ]
      }
    }
  ],
  "errors": []
}
```

### Extra Roots

```http
POST /skills/extra-roots
```

```json
{
  "roots": ["D:/workspace/generated-skills"]
}
```

MVP 可只保存在进程内，服务重启后失效。

### Enable Or Disable

```http
POST /skills/config
```

```json
{
  "path": "D:/.../SKILL.md",
  "name": null,
  "enabled": false
}
```

`path` 和 `name` 二选一。MVP 可延后持久化，先只实现 list 和 turn 注入。

### Chat Payload

当前 `ChatPayload` 还没有 `skills` 字段。建议扩展：

```python
class SkillSelectionPayload(TypedDict):
    name: str
    path: str

class ChatPayload(TypedDict):
    ...
    skills: NotRequired[list[SkillSelectionPayload]]
```

WebSocket payload：

```json
{
  "type": "prompt",
  "session_id": "...",
  "prompt": "Review the current diff",
  "mode": "act",
  "skills": [
    {
      "name": "code-review",
      "path": "D:/.../SKILL.md"
    }
  ]
}
```

服务端同时支持：

- 用户只输入 `$code-review`：后端按 name 解析。
- UI 发送结构化 `skills`：后端按 path 精确解析。
- 两者同时存在：结构化 path 优先，并去重。

### WebSocket Events

新增事件：

```json
{ "type": "skills_loaded", "count": 3, "enabled_count": 2 }
{ "type": "skill_injected", "name": "code-review", "path": "D:/.../SKILL.md" }
{ "type": "skills_warning", "message": "Skill name is ambiguous: code-review" }
```

前端需要同步更新 `ui/src/types/socket.ts`，否则当前 TypeScript union 不包含这些事件。`useAgentSocket.ts` 可以先把 warning/status 事件转成 `runEventAppended`，类似 `context_compressed` 的处理。

## UI 设计

MVP UI 不需要 marketplace。建议：

1. 在当前 session workspace 变化时请求 `GET /skills`。
2. Composer 支持 `$` 后的 skill hint。
3. 选择 skill 后不要强行改写用户文本，优先通过 WebSocket `skills` payload 发送结构化选择。
4. Skills 面板显示 scope、enabled、description、dependencies。
5. 对 MCP dependency 显示“需要授权”状态时，跳转或引导到现有 `/mcp/servers` grant 流程，而不是在 skill 面板里直接写 grant。

前端新增文件建议：

```text
ui/src/types/skills.ts
ui/src/api/skills.ts
ui/src/hooks/useSkills.ts
ui/src/components/skills/SkillsPanel.tsx
ui/src/components/composer/SkillMentionMenu.tsx
```

## 配置与持久化

MVP 环境变量：

```text
AUTOMATA_SKILLS_ENABLED=true
AUTOMATA_SYSTEM_SKILLS_ENABLED=true
AUTOMATA_SKILL_ROOTS=D:\custom\skills;D:\other\skills
AUTOMATA_SKILL_METADATA_BUDGET_CHARS=8000
AUTOMATA_SKILL_BODY_BUDGET_CHARS=65536
```

User-level 状态建议放在现有 Automata data dir：

```text
{get_database_config().path.parent}/skills/
{get_database_config().path.parent}/skills-config.json
```

后续如果需要数据库持久化，再增加表：

```sql
CREATE TABLE IF NOT EXISTS skill_settings (
    id TEXT PRIMARY KEY,
    selector_type TEXT NOT NULL CHECK (selector_type IN ('path', 'name')),
    selector_value TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (selector_type, selector_value)
);
```

不建议把完整 `SKILL.md` 存入数据库。数据库或 JSON 配置只存 enable/disable 规则和 extra roots。

## 安全边界

1. Skill 是不可信本地文本，不能直接执行。
2. Workspace skill 可以影响模型工作流，但不能授予本地进程、网络、MCP 或 mutating tool 权限。
3. Skill dependency 不能改变 `ToolExposure`、`McpServerGrant`、`McpPolicyEngine` 或 plan mode。
4. `SKILL.md` 读取必须限制在已发现的 skill path，不接受任意 path 注入。
5. `agents/openai.yaml` 的 icon path 必须位于 `assets/` 下，禁止绝对路径和越界 `..`。
6. 注入完整 skill 前做大小限制，避免把巨大文件放进模型上下文。
7. 不自动读取 skill 下 `scripts/`、`references/`、`templates/`。
8. 执行 skill 脚本仍必须由模型通过 `exec_command` 调用，并受现有 backend 和 plan mode 限制。
9. 如果 skill 建议使用 MCP，模型仍必须经过 `tool_search`、router dispatch、MCP policy 和 result validation。
10. MCP server candidate、grant、fingerprint 仍由 `routers/mcp.py` 和 `McpTrustStore` 管理，skills API 只展示依赖状态。

## 测试计划

后端单元测试：

- `loader` 解析合法 `SKILL.md`。
- 缺 frontmatter、非法 YAML、缺 description 返回 error。
- `agents/openai.yaml` 解析 interface、policy、dependencies。
- 非法 icon path 被忽略。
- root 扫描深度、隐藏目录、重复路径和同名 skill 行为正确。
- `render` 在预算内完整渲染，超预算时逐级降级。
- `injection` 支持 `$name`、结构化 `{name,path}`、去重和歧义检测。
- Plan mode 过滤 `policy.modes`。
- 完整 skill 注入不写入 `agent_context_messages`。
- Skill dependency 不会调用 `router.activate_deferred()`。
- MCP dependency 不会写 grant，也不会启动未授权 server。

集成测试：

- WebSocket prompt 携带 `$skill-name` 后产生 `skill_injected`。
- Skill 摘要进入 system prompt，完整 skill message 插入在历史上下文前。
- 未配置 skills 时，现有 chat/MCP/tool router 行为不变。
- 在已授权 MCP server 存在时，skill 仍不能预激活 MCP alias，模型必须通过 `tool_search`。
- Plan mode 中 skill 注入不暴露 mutating tools。

推荐命令：

```powershell
uv run --directory api --group dev --locked pytest tests/test_agent_skills_unit.py tests/test_agent_runtime_unit.py tests/test_chat.py
uv run --directory api --group dev --locked pytest tests/test_agent_tool_router_unit.py tests/test_agent_mcp_runtime_unit.py tests/test_agent_mcp_provider_unit.py
uv run --directory api --group dev --locked pytest
```

## 分阶段实施

### Phase 1：后端上下文 MVP

- 新增 `agent/skills` 包。
- 支持 repo/user/packaged/extra roots。
- 支持 `SKILL.md` frontmatter 和可选 `agents/openai.yaml`。
- 在 `prompts.py` 增加 `skill_notes` 参数。
- 在 `runtime.py` 增加 `skill_context` 参数和 ephemeral message 插入。
- 在 `services/chat.py` 的 `create_mcp_tool_runtime()` 之后创建 skill turn context。
- 支持 `$name` 文本触发。
- 增加后端单元测试。

交付标准：不改 UI 时，用户输入 `$some-skill ...` 即可让后端注入该 skill，且不影响现有 MCP/tool_search 行为。

### Phase 2：API 与基础 UI

- 新增 `routers/skills.py` 和 schemas。
- 前端展示当前 session workspace 的 skills。
- Composer 支持结构化选择，并通过 WebSocket payload 发送 `skills`。
- WebSocket 增加 `skills_loaded`、`skill_injected`、`skills_warning` 类型。
- UI 展示 MCP dependency 的 grant 状态，但跳转到现有 MCP grant API 处理授权。

交付标准：用户无需记忆 skill 名称，可以通过 UI 选择。

### Phase 3：启用/禁用与配置

- 新增 skill config 写入接口。
- 使用 data dir 下的 `skills-config.json` 或 SQLite 表保存 enabled rules。
- `SkillManager` cache key 纳入 disabled rules。
- UI 支持 enable/disable。

交付标准：用户可以稳定关闭某些 user/packaged skills。

### Phase 4：高级能力

- 文件 watcher：skill 文件变化时清 cache，并向 UI 发 `skills_changed`。
- implicit invocation telemetry：当模型读取 `SKILL.md` 或运行 skill `scripts/` 时记录事件。
- dependency diagnostics：更完整展示 builtin/deferred/MCP/env 缺失原因。
- plugin skill roots：插件贡献 skills，但仍不进入 `ToolProvider`。

## 推荐最小代码改动

Phase 1 建议只触碰：

- `api/automata_api/agent/skills/*`：新增 skills 子系统。
- `api/automata_api/agent/prompts.py`：新增 `skill_notes` 参数。
- `api/automata_api/agent/runtime.py`：新增 `skill_context` 并插入 ephemeral messages。
- `api/automata_api/services/chat.py`：在 MCP runtime 之后创建 skill context 并发送 skill events。
- `api/automata_api/schemas.py`：给 `ChatPayload` 增加结构化 skills 字段。
- `api/tests/test_agent_skills_unit.py`：新增 skill loader/render/injection 测试。
- `api/tests/test_agent_runtime_unit.py`、`api/tests/test_chat.py`：补上下文插入和 WebSocket 行为测试。

Phase 1 不需要改：

- `ToolRouter`。
- `ToolRouterBuilder`。
- `ToolRegistry`。
- `McpConnectionManager`。
- `McpPolicyEngine`。
- SQLite schema。
- React UI。

这个边界能保证 skills 先作为可验证的上下文能力落地，同时不破坏当前已经实现的 runtime tool discovery 和 MCP 调用路径。

## 待确认问题

1. Packaged system skills 是否随 Automata 首版一起发布。如果不发布，MVP 可只支持 repo/user/extra roots。
2. Skill dependency 是否需要 `required: true` 阻断语义。建议 MVP 只 warning，不阻断。
3. Skill 摘要预算是否需要随模型 context window 动态调整。当前默认 8000 字符可先使用。
4. UI 是否显示 `$skill-name` 在用户输入框。建议结构化 payload 优先，避免污染用户原始 prompt。
5. 是否需要把 skill selection 记录到消息 metadata。建议只记录轻量 metadata，不记录完整 `SKILL.md` 内容。
