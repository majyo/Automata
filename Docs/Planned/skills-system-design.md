# Automata Skills 系统：当前实现与后续计划

## Status

- 文档状态：后端上下文 MVP 已实现；前端、配置和依赖诊断可实施；packaged/plugin 能力暂缓
- 最近核对：2026-07-23
- 代码基线：`main` / `814d963`
- 关联文档：[运行期工具发现设计](../Archived/runtime-tool-discovery-design.md)、[MCP 调用设计](../Archived/mcp-tool-calling-design.md)

## 结论

Automata 当前已经能够：

- 从 workspace、用户数据目录和环境变量目录发现 `SKILL.md`；
- 解析 skill 基础 frontmatter 和可选 `agents/openai.yaml`；
- 将可用 skills 摘要加入 system prompt；
- 通过 `$skill-name` 或 WebSocket `skills` payload 选择 skill；
- 把选中 skill 的完整 `SKILL.md` 作为本回合临时消息注入模型上下文；
- 提供只读 `GET /skills` API；
- 发送并持久化 `skills_loaded`、`skills_warning`、`skill_injected` 事件；
- 保持 Skills 与 ToolRouter / MCP 权限边界分离。

当前还没有：

- React 端 skill 列表或 Composer 选择器；
- 前端 Skills event 类型和展示；
- enable/disable 写接口；
- `skills-config.json` 或数据库持久化规则；
- plugin skill roots；
- dependency diagnostics；
- 随应用发布的 system skills 内容。

因此当前能力可通过 `$skill-name` 或手工构造 WebSocket payload 使用，但还不是完整的用户可操作产品界面。

## 核心边界

Skills 是上下文指令层，不是工具执行层。

```text
Skill loader / manager
  -> 可用 skill 摘要
  -> 选中 skill 完整正文
  -> turn-scoped model context

ToolProvider / ToolRouter
  -> 模型当前可见工具
  -> plan mode 策略
  -> tool_search 激活
  -> 实际 dispatch

MCP runtime
  -> config / grant / connection
  -> tools/list
  -> tools/call
```

Skill 不能：

- 注册 `ToolDescriptor`；
- 自动激活 deferred tool；
- 启动未授权 MCP server；
- 写入 MCP grant；
- 绕过 Plan mode；
- 绕过 `ToolExecutionOrchestrator` 和审批；
- 直接执行 `scripts/`；
- 把自身正文持久化到 agent context store。

当前 `create_skill_turn_context()` 接受 `router` 参数，但立即丢弃该参数。依赖信息只被解析和返回给 API，尚不影响工具发现、诊断或执行。

## 当前实现地图

| 模块 | 当前职责 |
| --- | --- |
| `agent/skills/config.py` | 环境变量、roots 和字符预算 |
| `agent/skills/model.py` | metadata、interface、dependency、policy、selection、turn context |
| `agent/skills/loader.py` | 扫描 `SKILL.md`、解析受限 YAML、加载 `openai.yaml` |
| `agent/skills/manager.py` | workspace roots、进程内 instance cache |
| `agent/skills/render.py` | 生成 model-visible skills 摘要 |
| `agent/skills/injection.py` | `$name`、结构化选择、完整正文消息 |
| `agent/skills/runtime.py` | 组合单回合 `SkillTurnContext` |
| `services/chat.py` | act/plan reply 创建 context 并发送事件 |
| `agent/runtime.py` | 摘要进入 prompt，完整正文插入临时 messages |
| `routers/skills.py` | `GET /skills` |
| `schemas.py` | Skills API 和 Chat payload 类型 |
| `tests/test_agent_skills_unit.py` | loader、selection、runtime、API 测试 |

前端 `ui/src` 当前没有 Skills 相关组件、API client、类型或 reducer action。

## Skill 包格式

最小结构：

```text
some-skill/
  SKILL.md
```

可选结构：

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

Loader 只解析 `SKILL.md` 和 `agents/openai.yaml`。其他目录只是供 skill 指令引用的普通文件，不会被 Automata 自动执行或注入。

### `SKILL.md`

当前要求 YAML frontmatter：

```markdown
---
name: code-review
description: Review code changes and prioritize correctness issues.
metadata:
  short-description: Review a patch
---

# Code Review

Read the diff, inspect relevant code, and report actionable findings.
```

真实校验：

- `name` 必须非空，最大 64 字符；
- `description` 必须非空，最大 1024 字符；
- `metadata.short-description` 可选；
- 整个文件默认最大 65536 字符；
- 没有 frontmatter 或解析失败时，该 skill 产生 `SkillError`，其他 skills 继续加载；
- name 缺失时会使用 skill 目录名，但 description 仍必须提供。

当前 loader 使用项目内的简化 YAML parser，不是 PyYAML。文档不能承诺完整 YAML 1.2 能力；复杂 anchor、tag 或高级 scalar 语法不应作为 skill 格式依赖。

### `agents/openai.yaml`

当前支持：

```yaml
interface:
  display_name: Code Review
  short_description: Review changes
  icon_small: assets/icon-small.png
  icon_large: assets/icon-large.png
  brand_color: "#3366ff"
  default_prompt: Review the current change.

dependencies:
  tools:
    - type: builtin
      value: read_file
      description: Read repository files
      read_only: true
    - type: deferred
      query: calendar
      description: Search for a deferred calendar tool
    - type: mcp
      server: company
      tool: search

policy:
  allow_implicit_invocation: true
  modes: [act, plan]
```

真实行为：

- icon 必须是 skill 目录下 `assets/` 内的相对路径；
- `brand_color` 只接受 `#RRGGBB`；
- modes 只接受 `act` / `plan`；
- dependencies 仅作为 metadata；
- 文件不存在时正常加载基础 skill；
- `openai.yaml` 解析错误当前会被静默忽略，不会产生 `SkillError`。

最后一点是当前实现限制。后续应改成 warning，否则用户难以定位 metadata 配置错误。

## Skill Roots

当前 `SkillManager` 按以下 roots 扫描：

1. 从检测到的项目根到 session workspace，每一级目录的 `.automata/skills`；
2. Automata data dir 下的 `skills`；
3. `api/automata_api/skills/.system`，前提是 `AUTOMATA_SYSTEM_SKILLS_ENABLED=true`；
4. `AUTOMATA_SKILL_ROOTS` 指定的额外目录。

当前仓库没有 `api/automata_api/skills/.system` 内容，因此“system skill root 已配置”不等于“应用已经随包提供 system skills”。

扫描限制：

- 每个 root 最大深度 6；
- 每个 root 最多访问 2000 个目录；
- 跳过以 `.` 开头的子项；
- 路径 canonicalize 后去重；
- 不可读目录 fail-open。

Scope 排序为：

```text
repo -> user -> packaged -> extra -> plugin
```

这只是展示和解析顺序，不是 name override 规则。不同 roots 中存在同名 skill 时：

- `$name` 选择会报告 ambiguous；
- 只传 name 的结构化选择也无法解析；
- 传已发现的精确 `SKILL.md` path 可以消歧。

当前只有 `SkillScope` 类型预留了 `plugin`，没有任何 plugin root provider。

## 缓存的实际状态

`SkillManager` 内部有：

```python
dict[SkillCacheKey, SkillLoadOutcome]
```

但当前 wiring 在每次 `create_skill_turn_context()` 和每次 `GET /skills` 时都会新建 `SkillManager`。因此 cache 只在同一个 manager instance 被重复调用时有效，默认请求链路没有跨回合或跨请求缓存。

`GET /skills?force_reload=true` 参数已经存在，但在当前“一次请求一个新 manager”的路径下几乎没有可观察差异。

后续应把 manager 放到 app/runtime 生命周期，并让 cache key 包含 workspace、roots、配置版本和 disabled rules。默认失效策略使用 root / `SKILL.md` / `openai.yaml` 的 mtime 或轻量 fingerprint，加短 TTL 和显式 `force_reload`；当前规模不引入常驻文件 watcher。

## 发现、选择和注入

### 可用摘要

每个 act/plan reply 都会：

1. 根据 workspace 扫描 skills；
2. 按 `policy.modes` 过滤；
3. 生成 `## Skills` 摘要；
4. 通过 `skill_notes` 加入 system prompt。

摘要默认预算：

```text
AUTOMATA_SKILL_METADATA_BUDGET_CHARS=8000
```

超限时先移除 description，再按顺序省略 skills，并产生 warning。

### 显式选择

WebSocket prompt 可传：

```json
{
  "type": "prompt",
  "session_id": "session-id",
  "prompt": "Review this change",
  "mode": "act",
  "skills": [
    {"name": "code-review"},
    {"path": "D:/repo/.automata/skills/code-review/SKILL.md"}
  ]
}
```

当前解析规则：

- payload 不是 list 时按空选择处理；
- 非 object item 被忽略；
- path 会 expand/resolve；
- path 必须精确匹配已发现且当前 mode 启用的 skill；
- name 必须唯一匹配；
- 无效或歧义选择产生 warning，不阻断 reply。

### `$skill-name`

Prompt 中的 `$code-review` 会按 name 触发 skill。重复 mention 去重。

当前实现已经解析 `policy.allow_implicit_invocation`，但 `$name` 选择逻辑不检查这个字段。例如 metadata 写成：

```yaml
policy:
  allow_implicit_invocation: false
```

当前 `$name` 仍可注入该 skill。这个行为应保留：`$skill-name` 是用户写出的显式调用，不是 implicit invocation，因此 `allow_implicit_invocation=false` 不应阻止 `$name`，结构化 `skills` payload 也不应受它影响。

当前项目没有基于自然语言自动匹配 Skill 的机制，所以该字段目前没有运行时作用。实现配置阶段时应保留兼容解析但明确标为“仅预留给未来 automatic invocation”；如果未来新增自动匹配，建议改用更清晰的 `allow_automatic_invocation` 名称，并只控制自动候选，不影响任何显式选择。

### 完整正文

选中 skill 的完整 `SKILL.md` 被包装为：

```xml
<skill>
<name>code-review</name>
<path>D:/repo/.automata/skills/code-review/SKILL.md</path>
...完整文件内容...
</skill>
```

Runtime 通常在 model messages 的 index 1 插入这些 `role=user` 消息，即 system message 之后、普通历史上下文之前。执行已批准计划时，approved-plan message 占用 index 1，skills 改在 index 2 插入。

这些消息：

- 只存在于当前 reply 的内存 messages；
- 不写入 `agent_context_messages`；
- 不写入普通 session message；
- 下个 turn 会重新发现和选择；
- 单个正文超过 body budget 时不注入并产生 warning。

## 与 ToolRouter / MCP 的实际关系

当前顺序：

```text
services/chat.py
  -> create_backend()
  -> create_mcp_tool_runtime()
     -> BackendToolProvider
     -> granted McpToolProvider
     -> ToolRouter
  -> create_skill_turn_context()
  -> stream_agent_loop() / stream_plan_loop()
```

Skill dependency 当前不会：

- 调用 `tool_search`；
- 预激活 deferred tool；
- 连接 MCP server；
- 校验 grant；
- 阻止 skill 注入；
- 改变 Plan mode 可见工具。

模型必须按普通流程调用 `tool_search`，MCP 仍由现有 grant 和 policy 管理。

## API

### 已实现：列出 Skills

```http
GET /skills?workspace=D:/repo&force_reload=false
```

响应：

```json
{
  "workspace": "D:\\repo",
  "skills": [
    {
      "name": "code-review",
      "description": "Review code changes.",
      "short_description": "Review a patch",
      "path": "D:\\repo\\.automata\\skills\\code-review\\SKILL.md",
      "scope": "repo",
      "enabled": true,
      "interface": null,
      "dependencies": null
    }
  ],
  "errors": []
}
```

Workspace 必须是已存在目录，否则返回 422。

当前 `enabled` 实际总是由 `disabled_paths` 判断，而生产代码从未填充 `disabled_paths`，所以所有成功加载且 mode 未过滤的记录都显示为 enabled。

### 尚未实现

以下接口不存在：

```text
PUT /settings/skill-roots
PUT /skills/{skill_id}/enabled
GET /skills/{skill_id}/diagnostics
```

额外 roots 只能通过 `AUTOMATA_SKILL_ROOTS` 配置，不能由 UI 写入。

## WebSocket Events

后端当前会发送：

```json
{"type": "skills_loaded", "run_id": "...", "count": 3, "enabled_count": 2}
{"type": "skills_warning", "run_id": "...", "message": "..."}
{"type": "skill_injected", "run_id": "...", "name": "code-review", "path": "..."}
```

这些 payload 经过 `DurableRunEventSink` 后会补充：

```text
session_id
run_id
seq
schema_version
```

并作为普通 runtime Run events 持久化。

当前 `ui/src/types/socket.ts` 没有这些 union members，`useAgentSocket.ts` 也没有处理分支，因此事件不会在 UI 中显示。

## 配置

当前环境变量：

```text
AUTOMATA_SKILLS_ENABLED=true
AUTOMATA_SYSTEM_SKILLS_ENABLED=true
AUTOMATA_SKILL_ROOTS=<os.pathsep separated paths>
AUTOMATA_SKILL_METADATA_BUDGET_CHARS=8000
AUTOMATA_SKILL_BODY_BUDGET_CHARS=65536
```

用户 root 位于数据库 data dir 的同级 `skills` 目录。项目没有持久化 enable/disable 规则，也没有数据库 migration。

## 当前安全边界

已有：

- 只把已扫描的 canonical `SKILL.md` path 作为可选 skill；
- 结构化 path 不能选择未发现文件；
- asset metadata 拒绝绝对路径和 `..`，且必须位于 `assets/`；
- scan depth、目录数、metadata/body 字符预算有上限；
- 损坏 skill fail-open；
- skill 不直接获得工具权限；
- body 只进入临时上下文。

仍需注意：

- Skill 内容是本地 prompt 指令，可能影响模型行为；roots 本身必须被视为受信任配置；
- `scripts/` 不会自动执行，但模型可能按 skill 指令调用已获准工具执行脚本；
- dependencies 不是安全策略；
- `openai.yaml` 错误当前静默忽略；
- 当前没有 remote install 或签名校验；
- 当前没有 plugin root trust model。

## UI 后续设计

当前 UI 没有 Skills 能力。最小产品化需要：

```text
ui/src/types/skills.ts
ui/src/api/skills.ts
ui/src/hooks/useSkills.ts
ui/src/components/composer/SkillPicker.tsx
ui/src/components/conversation/SkillEvent.tsx
```

行为：

1. workspace 确定后调用 `GET /skills`；
2. Composer 允许选择一个或多个 skill；
3. 发送 prompt 时写入结构化 `skills` payload；
4. session/workspace 切换时清理选择；
5. 展示 loader warning 和 injected 状态；
6. 同名 skill 使用 path 消歧；
7. 不在前端读取 `SKILL.md` 正文。

当前 UI 没有 Vitest。若实现 picker/reducer 自动测试，需要先引入前端测试框架；否则至少保证 TypeScript build。

基础 UI 属于当前可实施范围。为避免 Composer 选择、session/workspace 切换和 WebSocket reducer 只能靠手工回归，Phase 2B 应同时引入最小前端测试框架，而不是长期只依赖 build。

## Enable / Disable 后续设计

不使用 canonical path 作为唯一身份。绝对路径会随 workspace 移动、用户目录变化或 root 重配而失效。建议先给 API 返回稳定 `skill_id`，其组成至少包含：

```text
scope
root provenance / root id
skill 在 root 内的相对目录
name
```

canonical path 和内容 fingerprint 只作为诊断、迁移和冲突检测信息。配置可持久化在 Automata data dir：

```json
{
  "version": 2,
  "disabled": [
    {
      "skill_id": "user:user-data:code-review",
      "scope": "user",
      "root_id": "user-data",
      "relative_dir": "code-review",
      "name": "code-review",
      "path_hint": "D:/.../SKILL.md",
      "fingerprint": "sha256:..."
    }
  ]
}
```

实现要求：

- repo / user / packaged roots 使用确定的 provenance；extra root 在配置中必须有稳定 id；
- `skill_id` 精确匹配时应用规则；path hint 不作为唯一匹配键；
- name 或 fingerprint 只用于迁移提示，歧义时不自动套用旧规则；
- 写接口只能接受当前已发现的 `skill_id`，不能借此禁用任意文件；
- shared `SkillManager` cache key 包含 config version；
- API 写入后清 cache；
- disabled skill 不进入摘要、`$name` 或结构化选择；
- repo skill 是否允许由 user 全局禁用需要明确优先级。

在这些规则落地前，不应向 UI 暴露无效的开关。

## 测试现状

当前自动化覆盖：

- frontmatter、interface 和 dependencies；
- 坏 skill 不阻断好 skill；
- 摘要和选择注入；
- plan mode 的 `policy.modes`；
- payload path 解析；
- runtime 临时消息不持久化；
- `GET /skills`。

当前缺口：

- implicit/automatic invocation 字段的清晰命名和兼容语义；
- `openai.yaml` 错误 warning；
- shared cache / reload；
- disabled rules；
- duplicate-name UI 消歧；
- WebSocket Skills event 的前端消费；
- dependency diagnostics；
- packaged skill 内容和 plugin roots（均为 Deferred）。

## 分阶段实施

### Phase 1：后端上下文 MVP

状态：`DONE`

- `agent/skills` 包；
- repo/user/packaged/extra roots；
- `SKILL.md` 和可选 `openai.yaml`；
- system prompt 摘要；
- `$name` 和结构化选择；
- ephemeral full-body injection；
- 后端单元测试。

说明：packaged root 的代码路径已实现，但当前没有随包提供具体 system skill。

### Phase 2A：只读 API 和后端事件

状态：`DONE`

- `GET /skills`；
- Chat payload `skills`；
- `skills_loaded` / `skills_warning` / `skill_injected`；
- Run event 持久化。

### Phase 2B：基础 UI

状态：`TODO`

- skill API client；
- Composer picker；
- event 类型和展示；
- workspace 切换；
- duplicate-name 消歧。
- 最小前端测试框架；
- picker、session/workspace 切换和 Skills event reducer 测试。

### Phase 3：配置、禁用与缓存

状态：`TODO`

- app-scoped/shared manager；
- mtime/fingerprint + TTL 的轻量失效和有效 `force_reload`；
- 基于稳定 `skill_id` 的 enable/disable 持久化和 API；
- UI 开关；
- 保持 `$name` 和结构化 payload 为显式调用，不受 implicit/automatic policy 限制；
- 为未来 automatic invocation 保留兼容字段，但当前不增加自动匹配；
- metadata parse warning。

### Phase 4：Dependency diagnostics

状态：`TODO`

- 将 `dependencies.tools` 与当前 ToolRouter descriptor、deferred candidate 和 MCP grant 做只读比对；
- API/UI 显示 `available`、`not_found`、`not_granted`、`deferred` 等诊断；
- 诊断仅提供建议，不自动激活 deferred tool、不写 MCP grant、不启动 server，也不阻止显式 Skill 注入。

### Deferred：packaged system skills

状态：`DEFERRED`

代码中的 packaged root 已存在，但仓库没有可发布内容。等待具体 Skill 内容、维护 owner、版本/升级策略和 repo/user override 规则后再实施。

### Deferred：plugin roots 和 trust

状态：`DEFERRED`

当前没有 plugin 安装生命周期、来源身份或 trust model。等待这些前置条件后，再定义 plugin root provider、卸载清理、冲突优先级和信任提示。

### 明确不实现

- 常驻文件 watcher：当前规模采用轻量 fingerprint/TTL 和显式 reload；
- invocation telemetry：项目尚无隐私、留存、开关和 telemetry 基础设施，当前没有足够产品需求；
- Skill 自动授予工具或 MCP 权限：违反现有 ToolRouter / grant / approval 边界。

## 验收标准

后端现有 MVP：

1. 没有 skills 时现有 agent/MCP/tool behavior 不变。
2. 坏 skill 不阻断正常 reply。
3. 被选择 skill 的正文只进入当前 reply。
4. Skill 不改变 ToolRouter、MCP grant 或 Plan mode。
5. 字符和扫描预算生效。
6. 后端测试通过。

基础 UI 完成后：

1. 用户无需手写 `$name` 即可选择 skill。
2. 结构化 payload 使用 path 消歧。
3. warning 和 injected 状态可见。
4. workspace/session 切换不会沿用错误选择。
5. UI build 和新增测试通过。

配置阶段完成后：

1. disable 规则跨重启有效。
2. disabled skill 不进入摘要或注入。
3. `$name` 和结构化 payload 始终作为显式调用；implicit/automatic policy 不会错误阻止它们。
4. reload/cache 行为有自动化证明。
5. metadata 错误可诊断。
6. workspace 移动或 root 重配时不会仅因绝对路径变化而静默误用禁用规则。

Dependency diagnostics 完成后：

1. 缺少 builtin/deferred/MCP dependency 时可见且可解释。
2. 诊断不会自动改变 ToolRouter、MCP grant、server 状态或 Plan mode。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest tests/test_agent_skills_unit.py tests/test_agent_runtime_unit.py tests/test_chat.py
uv run --directory api --group dev --locked pytest
npm --prefix ui run build
```
