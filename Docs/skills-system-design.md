# Automata Skills 系统设计方案

本文档设计一个类似 Codex 的 skills 系统，用于让 Automata 在不同任务类型下加载可复用的本地工作流说明、脚本、模板和资源。设计基于当前 Automata 项目结构，并参考了本机 Codex 源码 `D:\workspace\projects\codex` 中的 skills 实现。

## 参考源码与当前结构

Codex 的 skills 相关实现主要分布在：

- `D:\workspace\projects\codex\codex-rs\core-skills\src\loader.rs`：发现 skill root，扫描 `SKILL.md`，解析 frontmatter 和 `agents/openai.yaml`。
- `D:\workspace\projects\codex\codex-rs\core-skills\src\manager.rs`：缓存、按 cwd/config 加载、启用/禁用过滤。
- `D:\workspace\projects\codex\codex-rs\core-skills\src\render.rs`：把可用 skills 渲染为模型可见的摘要块，并做上下文预算裁剪。
- `D:\workspace\projects\codex\codex-rs\core-skills\src\injection.rs`：解析显式 `$skill-name` 或结构化 skill 选择，并按需读取完整 `SKILL.md`。
- `D:\workspace\projects\codex\codex-rs\core\src\context\available_skills_instructions.rs`：会话上下文中的可用 skills 摘要。
- `D:\workspace\projects\codex\codex-rs\core\src\context\skill_instructions.rs`：显式使用 skill 时注入完整 skill 内容。
- `D:\workspace\projects\codex\codex-rs\app-server\src\request_processors\catalog_processor.rs`：`skills/list`、`skills/extraRoots/set`、`skills/config/write` 等客户端接口。

Automata 当前相关结构：

- `api/automata_api/agent/runtime.py`：agent/plan loop 入口，负责构造上下文、调用模型、执行工具。
- `api/automata_api/agent/prompts.py`：system prompt 和工具说明。
- `api/automata_api/services/chat.py`：WebSocket 流程，按 session 创建 backend 和 `ToolRegistry`。
- `api/automata_api/repositories/sessions.py`：session 的 `working_directory`、`backend` 和上下文持久化。
- `api/automata_api/agent/tools/registry.py`：静态工具注册表。
- `ui/src/components/composer/PromptComposer.tsx`：用户输入入口。

核心判断：skills 应该作为“上下文构建前的可发现指令层”接入，而不是作为新的工具类型混入 `ToolRegistry`。工具负责执行动作，skill 负责告诉模型在特定任务中如何组织动作、读哪些参考、运行哪些脚本。

## 目标

1. 支持本地 `SKILL.md` skill 包，模型先看到可用 skill 摘要，显式触发后再看到完整指令。
2. 支持按 workspace/cwd 发现 repo skills，按用户目录发现 user skills，并预留 system skills 和 plugin skills。
3. 支持 `$skill-name` 文本触发和 UI 结构化选择两种触发路径。
4. 保持 Plan 模式可用，但 Plan 模式只能使用当前 read-only 工具集合。
5. 让 skill 加载、上下文注入和 UI 管理成为独立模块，避免污染 `runtime.py` 主循环。
6. 对损坏的 skill fail-open：跳过该 skill、返回 warning/error，不阻断正常对话。
7. 为后续模板、脚本、依赖检查、隐式调用遥测和插件化留下扩展点。

## 非目标

- 不在 MVP 中实现插件安装、市场、远程 skill 下载。
- 不在 MVP 中自动安装 Python/Node/MCP 依赖。
- 不让 skill 直接获得额外权限；所有文件、命令和网络行为仍通过现有 tools/backend 策略执行。
- 不在 MVP 中实现复杂 fuzzy matching。名称冲突时只允许通过结构化 path 选择。
- 不要求修改现有 LLM provider 协议；仍使用当前 OpenAI-compatible chat/completions 消息结构。

## Skill 包格式

一个 skill 是一个目录，至少包含 `SKILL.md`：

```text
some-skill/
  SKILL.md
  agents/
    openai.yaml        # 可选，UI/依赖/策略元数据
  scripts/             # 可选，skill 自带脚本
  references/          # 可选，长参考文档
  templates/           # 可选，模板文件
  assets/              # 可选，图标或其他静态资源
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

- `name`：必填或可从目录名推导；建议只包含 `a-zA-Z0-9_-`，插件 skill 以后可使用 `plugin:skill` 命名。
- `description`：必填，进入模型可见的 skill 摘要。
- `metadata.short-description`：可选，供 UI 更紧凑显示。
- 正文是完整指令，只有显式触发后才注入上下文。

可选 `agents/openai.yaml`：

```yaml
interface:
  display_name: "Code Review"
  short_description: "Find bugs and regressions"
  icon_small: "assets/icon.png"
  icon_large: "assets/icon-large.png"
  brand_color: "#1F6FEB"
  default_prompt: "Review the current diff."

dependencies:
  tools:
    - type: "builtin"
      value: "rg"
      description: "Search repository files."

policy:
  allow_implicit_invocation: true
  modes: ["act", "plan"]
```

Automata MVP 只需要解析并返回这些元数据，不需要执行 dependency install。`policy.modes` 是 Automata 扩展字段，用来控制该 skill 是否在 Plan 模式出现。

## Skill Roots 与优先级

建议 root 解析顺序：

1. Repo roots：从 session `working_directory` 向上查找项目根，再读取沿途 `.automata/skills`。
2. User root：`%USERPROFILE%\.automata\skills`，或 `AUTOMATA_HOME\skills`。
3. System root：项目内置 `api/automata_api/skills/.system`，用于随 Automata 分发的基础 skills。
4. Extra roots：`AUTOMATA_SKILL_ROOTS` 分号分隔，或运行时 API 设置。
5. Future plugin roots：后续插件系统提供的 skill roots。

排序建议：

- Repo > User > System。
- 同一 root 内按 `name` 排序。
- 相同 `SKILL.md` 路径去重。
- 相同 `name` 不覆盖；保留所有项，但纯 `$name` 触发必须要求名称唯一，否则返回歧义 warning，并提示 UI/path 选择。

扫描限制：

- 最大深度默认 6。
- 每个 root 最多扫描 2000 个目录。
- 忽略隐藏目录。
- repo root 允许 symlink 但必须 canonicalize；system root 可以不跟随 symlink。
- 损坏 frontmatter、超长字段或非法 YAML 记录到 `errors`，不阻断其他 skills。

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
  config.py
```

### `model.py`

定义纯数据结构：

```python
@dataclass(frozen=True)
class SkillRoot:
    path: Path
    scope: Literal["repo", "user", "system", "extra", "plugin"]

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
```

### `loader.py`

职责：

- 从 `SkillRoot` 扫描 `SKILL.md`。
- 解析 YAML frontmatter。
- 解析可选 `agents/openai.yaml`。
- 做字段长度限制、单行清洗、路径 canonicalize。
- 生成 `SkillLoadOutcome`。

建议限制：

- `name` 最大 64 字符。
- `description` 最大 1024 字符。
- `default_prompt` 最大 1024 字符。
- icon path 必须是 skill 目录下的 `assets/` 相对路径。

### `manager.py`

职责：

- 根据 `cwd` 和配置计算 roots。
- 缓存 `SkillLoadOutcome`。
- 支持 `force_reload`。
- 应用启用/禁用规则。
- 暴露 `skills_for_cwd(cwd, force_reload=False)`。

缓存 key：

```text
(resolved_cwd, roots_digest, disabled_rules_digest)
```

MVP 可先使用进程内 `dict` 缓存；文件 watcher 和跨进程 cache 不做。

### `render.py`

职责：把可用 skills 渲染成模型可见摘要块，并控制上下文预算。

Automata 没有 Codex 的 developer role 分层，建议把摘要追加到 system prompt 中：

```text
## Skills
A skill is a local reusable instruction package stored in SKILL.md.

### Available skills
- code-review: Review code changes and prioritize bugs. (file: D:/.../SKILL.md)

### How to use skills
- If the user names a skill with $skill-name or the task clearly matches a skill description, use it for that turn.
- After deciding to use a skill, follow the injected full skill instructions.
- Do not carry a skill into later turns unless it is re-mentioned or still clearly applies.
```

预算：

- 默认 skill 摘要预算 8000 字符。
- 如果超出，先截断 description。
- 如果仍超出，只保留 `name + path`。
- 如果仍超出，按优先级保留前 N 个，并在 WebSocket 发 `skills_warning`。

### `injection.py`

职责：

- 从用户 prompt 中提取 `$skill-name`。
- 从 WebSocket payload 中读取结构化 skill 选择。
- 对 path 选择优先于 name 选择。
- name 选择必须唯一且 enabled。
- 读取完整 `SKILL.md`，构造临时上下文消息。

建议完整注入格式：

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

这条消息不应写入 `agent_context_messages`，否则会让后续回合重复携带大段 skill 内容。它应该是本次 turn 的 ephemeral context。

## Runtime 接入点

当前 `services/chat.py` 在每次流式回复前做：

1. 读取 session 的 `working_directory` 和 `backend`。
2. 创建 backend。
3. 创建 `ToolRegistry`。
4. 调用 `stream_agent_loop` 或 `stream_plan_loop`。

建议改为：

```text
stream_agent_reply / stream_plan_reply
  -> session_config
  -> backend
  -> registry
  -> skill_manager.skills_for_cwd(session_config["working_directory"])
  -> collect explicit skill mentions from prompt + payload.skills
  -> render available skills into system prompt
  -> build full skill instruction messages
  -> stream_agent_loop(..., skill_context=...)
```

`runtime.py` 的改动应保持窄：

- `stream_agent_loop` 新增 `skill_context: SkillTurnContext | None`。
- `stream_plan_loop` 同样新增该参数。
- `agent_system_prompt(...)` / `plan_system_prompt(...)` 新增 `skill_notes` 参数。
- `fetch_agent_context(...)` 返回 messages 后，在 system message 后插入 `skill_context.injected_messages`。

插入位置：

```text
[
  system prompt with available skill summary,
  injected full skill messages,
  compressed history summary,
  recent history,
  current user prompt
]
```

这样模型先看到全局规则和可用列表，再看到本回合显式 skill 的完整内容，最后看到真实对话上下文。

Plan 模式：

- 仍渲染可用 skills。
- 仍允许注入完整 skill。
- `policy.modes` 不包含 `plan` 的 skill 不在 Plan 模式摘要中出现，也不能显式注入。
- 注入 skill 不改变 Plan 模式工具白名单。

## API 与 WebSocket 设计

当前后端是 FastAPI REST + WebSocket，不需要复制 Codex app-server 的 JSON-RPC 形态。建议新增 `api/automata_api/routers/skills.py`。

### 列出 skills

```http
GET /api/skills?cwd=D:/workspace/projects/automata&force_reload=false
```

响应：

```json
{
  "cwd": "D:/workspace/projects/automata",
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
      "dependencies": null
    }
  ],
  "errors": []
}
```

### 设置额外 roots

```http
POST /api/skills/extra-roots
```

```json
{
  "roots": ["D:/workspace/generated-skills"]
}
```

MVP 可以只保存在进程内，服务重启后失效；后续再持久化。

### 启用/禁用 skill

```http
POST /api/skills/config
```

```json
{
  "path": "D:/.../SKILL.md",
  "name": null,
  "enabled": false
}
```

MVP 如果不做持久化 UI，可以延后该接口。实现时要求 `path` 和 `name` 二选一。

### Chat payload 扩展

现有 WebSocket prompt payload：

```json
{
  "type": "prompt",
  "session_id": "...",
  "prompt": "...",
  "mode": "plan"
}
```

扩展为：

```json
{
  "type": "prompt",
  "session_id": "...",
  "prompt": "$code-review Review the current diff",
  "mode": "act",
  "skills": [
    {
      "name": "code-review",
      "path": "D:/.../SKILL.md"
    }
  ]
}
```

服务端应同时支持：

- 用户只输入 `$code-review`：后端按 name 解析。
- UI 发送结构化 `skills`：后端按 path 精确解析。
- 两者同时存在：结构化 path 优先，并去重。

### WebSocket 事件

新增非阻断事件：

```json
{ "type": "skills_loaded", "count": 3, "enabled_count": 2 }
{ "type": "skill_injected", "name": "code-review", "path": "D:/.../SKILL.md" }
{ "type": "skills_warning", "message": "Skill name is ambiguous: code-review" }
```

这些事件用于 UI 展示和调试，不进入模型上下文。

## UI 设计

MVP 不需要完整 skill marketplace。建议先做三处小改动：

1. Composer 支持输入 `$` 后的 skill hint。
2. Sidebar 或 FloatingInspector 增加 “Skills” 面板，展示当前 session cwd 下的 skills。
3. 点击 skill 后把 `[$skill-name](skill://absolute-path)` 或结构化 `skills` payload 加入下一次发送。

UI 展示字段：

- display name：优先 `interface.display_name`，否则 `name`。
- description：优先 `interface.short_description`，否则 `short_description`，否则 `description`。
- scope badge：repo/user/system/extra。
- enabled toggle：第二阶段再做。

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
AUTOMATA_HOME=D:\Users\...\ .automata
AUTOMATA_SKILLS_ENABLED=true
AUTOMATA_SYSTEM_SKILLS_ENABLED=true
AUTOMATA_SKILL_ROOTS=D:\custom\skills;D:\other\skills
AUTOMATA_SKILL_METADATA_BUDGET_CHARS=8000
```

第二阶段持久化：

- user-level skill config 可以先放在 `AUTOMATA_HOME/config.json`。
- 如果需要和 session 绑定，再增加 SQLite 表：

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

不建议把完整 `SKILL.md` 内容存入数据库。数据库只存 enabled/disabled 规则；skill 内容始终从磁盘读取，便于编辑后 reload。

## 安全边界

1. Skill 只是指令和资源，不直接执行。
2. Skill 中的脚本只能通过现有 `exec_command` 或未来明确工具执行。
3. `SKILL.md` 读取应限制在已发现的 skill path，不接受任意用户 path 注入。
4. `agents/openai.yaml` 的 icon path 必须位于 `assets/` 下，禁止绝对路径和越界 `..`。
5. 对损坏 YAML、过长字段、不可读文件 fail-open。
6. repo skills 来自当前 workspace，属于仓库内容；user/system skills 来自本机配置。UI 应显示 scope，避免用户误以为所有指令都来自仓库。
7. 注入完整 skill 时不要自动读取 `references/`、`templates/`、`scripts/` 下所有内容；让 skill 正文指导模型按需用 `read_file` 读取。

## 测试计划

后端单元测试：

- `loader` 能解析合法 `SKILL.md`。
- 缺 frontmatter、非法 YAML、缺 description 时返回 error。
- `agents/openai.yaml` 可选字段解析正确，非法 icon path 被忽略。
- root 扫描深度限制、隐藏目录跳过、重复路径去重。
- 同名 skill 保留多个，纯 `$name` 触发时判定歧义。
- `render` 在预算内完整渲染，超预算时截断 description，再降级为 name/path。
- `injection` 能从 `$name` 和结构化 `{name,path}` 中选择 skill。
- Plan 模式过滤 `policy.modes`。
- 完整 skill 注入不写入 `agent_context_messages`。

集成测试：

- `GET /api/skills` 返回当前 cwd 的 repo/user/system skills。
- WebSocket prompt 携带 `$skill-name` 后产生 `skill_injected` 事件。
- 被禁用 skill 不出现在摘要中，也不能被显式注入。
- 损坏 skill 只产生 `skills_warning`，正常 agent 回复不失败。

回归测试命令建议沿用当前 API 测试入口：

```powershell
uv run --directory api --group dev --locked pytest tests/test_agent_skills_unit.py
uv run --directory api --group dev --locked pytest tests/test_chat.py tests/test_agent_runtime_unit.py
uv run --directory api --group dev --locked pytest
```

## 分阶段实施

### Phase 1：后端 MVP

- 新增 `agent/skills` 包。
- 支持 repo/user/system/extra root 扫描。
- 支持 `SKILL.md` frontmatter 和可选 `agents/openai.yaml`。
- 在 `services/chat.py` 中加载 skills。
- 在 `prompts.py` 中渲染可用 skills 摘要。
- 在 `runtime.py` 中插入本回合完整 skill 消息。
- 支持 `$name` 文本触发。
- 增加后端单元测试。

交付标准：不改 UI 时，用户输入 `$some-skill ...` 即可让后端注入该 skill。

### Phase 2：API 与基础 UI

- 新增 `GET /api/skills`。
- 前端展示当前 session 的 skills。
- Composer 支持结构化选择，并通过 WebSocket payload 发送 `{name,path}`。
- WebSocket 增加 `skills_loaded`、`skill_injected`、`skills_warning`。

交付标准：用户无需记住精确 skill 名称，可以在 UI 中选择 skill。

### Phase 3：启用/禁用与配置

- 新增 skill config 写入接口。
- 增加 `skill_settings` 或 `AUTOMATA_HOME/config.json`。
- UI 支持 enable/disable。
- `SkillManager` cache key 纳入 disabled rules。

交付标准：用户可以稳定关闭某些 user/system skills。

### Phase 4：高级能力

- 文件 watcher：skill 文件变化时清 cache，并向 UI 发 `skills_changed`。
- implicit invocation：当模型运行 skill 自带 `scripts/` 或读取 `SKILL.md` 时记录事件。
- dependencies：对声明的 builtin tool/MCP/env var 做可用性检查和提示。
- plugin roots：未来插件系统可贡献 skills。

## 推荐的最小代码改动清单

Phase 1 的最小实现应只触碰：

- `api/automata_api/agent/skills/*`：新增 skills 子系统。
- `api/automata_api/agent/prompts.py`：增加 `skill_notes` 参数。
- `api/automata_api/agent/runtime.py`：接收 `skill_context` 并插入 ephemeral messages。
- `api/automata_api/services/chat.py`：加载 skills、解析 prompt、传入 runtime。
- `api/tests/test_agent_skills_unit.py`：新增单元测试。
- `api/tests/test_agent_runtime_unit.py`：补上下文插入测试。

Phase 1 不需要改：

- `ToolRegistry`。
- `Backend` 抽象。
- SQLite schema。
- React UI。

这个边界能让 skills 系统先成为可验证的上下文能力，避免和工具执行、前端管理、插件安装同时耦合。

## 待确认问题

1. Automata 是否需要内置 system skills。如果需要，system skills 应该放在源码包内并随后端发布；如果不需要，MVP 可只支持 repo/user/extra roots。
2. 当前默认模型是否稳定支持较大的 system prompt。如果不稳定，skill 摘要预算应保守设为 4000 字符。
3. UI 选择 skill 时是否要把 `$name` 写入可见 prompt。建议可见 prompt 保留用户原文，结构化选择走 payload，避免污染用户文本。
4. 启用/禁用配置是 user-level 还是 workspace-level。建议先 user-level，再按需要增加 repo 配置。
