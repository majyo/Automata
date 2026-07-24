# Automata Skills 系统实现说明

## Status

- 文档状态：`DONE`
- 最近核对：2026-07-23
- 代码基线：`main` / `220db76` 加本文对应工作区改动
- 已完成范围：后端上下文、API/事件、Composer UI、稳定身份、启停配置、shared cache、依赖诊断
- Deferred：随包 system skill 内容、plugin roots/trust

## 结论

Automata Skills 已成为可由用户操作的 turn-scoped 指令系统：

- 从 repo、用户数据目录、可选 packaged root 和显式 extra roots 发现 `SKILL.md`；
- 解析基础 frontmatter 和 `agents/openai.yaml`；
- 把可用 skill 摘要加入 system prompt；
- 通过 `$skill-name` 或 Composer picker 显式选择；
- 只把选中 skill 的完整正文注入当前 reply；
- 用稳定 `skill_id` 持久化 enable/disable；
- 在 UI 显示 loader warning、注入通知和 dependency diagnostics；
- 保持 Skill 与工具发现、MCP grant、审批和 Plan mode 完全分离。

Skills 仍只是上下文指令。它不能注册工具、自动连接 MCP、修改 grant、激活 deferred tool、绕过审批或自动执行 `scripts/`。

## 实现地图

| 职责 | 文件 |
| --- | --- |
| roots、预算、TTL 配置 | `api/automata_api/agent/skills/config.py` |
| metadata 和稳定身份模型 | `api/automata_api/agent/skills/model.py` |
| `SKILL.md` / `openai.yaml` loader | `api/automata_api/agent/skills/loader.py` |
| shared manager 和 fingerprint cache | `api/automata_api/agent/skills/manager.py` |
| enable/disable 持久化 | `api/automata_api/agent/skills/settings.py` |
| 只读依赖诊断 | `api/automata_api/agent/skills/diagnostics.py` |
| 单回合 context | `api/automata_api/agent/skills/runtime.py` |
| API | `api/automata_api/routers/skills.py` |
| UI API client/hook | `ui/src/api/skills.ts`、`ui/src/hooks/useSkills.ts` |
| Composer picker | `ui/src/components/composer/SkillPicker.tsx` |
| WebSocket event 消费 | `ui/src/hooks/useAgentSocket.ts` |

## Skill 包和解析

最小结构：

```text
some-skill/
  SKILL.md
```

可选结构：

```text
some-skill/
  SKILL.md
  agents/openai.yaml
  scripts/
  references/
  templates/
  assets/
```

Loader 只主动解析 `SKILL.md` 和 `agents/openai.yaml`。其余文件只供指令引用，不会自动执行或整体注入。

### SKILL.md

```markdown
---
name: code-review
description: Review code changes and prioritize correctness issues.
metadata:
  short-description: Review a patch
---

# Code Review

Read the diff and report actionable findings.
```

约束：

- `name` 非空，最多 64 字符；
- `description` 非空，最多 1024 字符；
- `metadata.short-description` 可选；
- 单文件默认最多 65536 字符；
- 缺少 frontmatter、非法 UTF-8 或解析失败只产生该文件的 error，不阻断其他 skills；
- 使用项目内受限 YAML parser，不承诺完整 YAML 1.2。

### agents/openai.yaml

支持 `interface`、`dependencies.tools` 和 `policy`。icon 必须是 skill 目录内 `assets/` 下的相对路径，颜色只接受 `#RRGGBB`，mode 只接受 `act` / `plan`。

`openai.yaml` 不存在时基础 skill 正常加载；语法、编码或结构错误时基础 skill 仍保留，同时返回 `severity=warning` 的 `SkillError`，不再静默忽略。

`allow_implicit_invocation` 继续兼容解析，但当前没有自然语言自动匹配。`$name` 和结构化 picker 选择都是显式调用，不受该字段阻止。

## Roots 与稳定身份

扫描顺序：

1. 项目根到当前 workspace 每一级的 `.automata/skills`；
2. Automata data dir 下的 `skills`；
3. `api/automata_api/skills/.system`，受 `AUTOMATA_SYSTEM_SKILLS_ENABLED` 控制；
4. `AUTOMATA_SKILL_ROOTS` 指定的 extra roots。

当前仓库仍没有实际 packaged system skill 内容。`plugin` scope 只保留类型，没有 root provider。

每个 skill 的身份输入为：

```text
scope + root_id + root 内相对目录 + name
```

再生成 `skill_<24 hex>`。绝对 canonical path 不参与唯一身份，因此 repo 整体移动不会改变 repo skill ID。内容 fingerprint 为 `SKILL.md` 和可选 `openai.yaml` 的 SHA-256，仅用于变化和诊断。

repo/user/packaged roots 有确定的 root ID。extra root 可用以下格式显式给稳定 ID：

```text
AUTOMATA_SKILL_ROOTS=team-skills=D:\shared\skills
```

多个条目仍使用操作系统 `os.pathsep` 分隔。未写 ID 时使用按顺序生成的 `extra-<index>`，root 重排会改变身份，因此长期配置应显式命名。

同名 skill 不覆盖：

- `$name` 和只传 name 的结构化选择会报告歧义；
- UI 始终发送已发现记录的精确 path；
- picker 显示 scope 和 relative dir 以便消歧。

## Shared cache 和失效

生产请求通过 `get_skill_manager()` 复用 app-scoped manager，不再为每次 GET/reply 新建 manager。

默认策略：

- `AUTOMATA_SKILL_CACHE_TTL_SECONDS=2`；
- TTL 内直接返回 cached load outcome，但每次重新应用当前 disabled settings；
- TTL 后比较已发现 `SKILL.md` / `openai.yaml` 的 path、mtime 和 size fingerprint；
- fingerprint 未变只刷新检查时间；
- `force_reload=true` 强制重新解析；
- SkillsConfig 环境配置变化时重建 shared manager；
- 不使用常驻文件 watcher。

## Enable / Disable

配置保存在 Automata data dir 的 `skills-config.json`：

```json
{
  "version": 2,
  "disabled": [
    {
      "skill_id": "skill_...",
      "scope": "repo",
      "root_id": "repo:.",
      "relative_dir": "code-review",
      "name": "code-review",
      "path_hint": "D:/repo/.automata/skills/code-review/SKILL.md",
      "fingerprint": "sha256:..."
    }
  ]
}
```

只有当前 workspace 中已发现的 `skill_id` 能通过写接口修改。写入使用同目录临时文件后原子替换。`path_hint` 和 fingerprint 是诊断信息，不作为自动模糊匹配键。

disabled skill：

- 不进入可用摘要；
- 不响应 `$name`；
- 不能通过结构化 payload 注入；
- UI 自动从当前选择中移除；
- 跨 manager 重建和应用重启继续生效。

## API

### 列表

```http
GET /skills?workspace=D:/repo&force_reload=false
```

每条记录包含：

```text
skill_id
name / description / short_description
path / scope / root_id / relative_dir
fingerprint
enabled
interface
dependencies
diagnostics
```

errors 带 `warning | error` severity。

### 启停

```http
PUT /skills/{skill_id}/enabled

{
  "workspace": "D:/repo",
  "enabled": false
}
```

未知或非当前发现 ID 返回 404，非法 workspace 返回 422。

### 依赖诊断

```http
GET /skills/{skill_id}/diagnostics?workspace=D:/repo
```

状态：

- `available`
- `deferred`
- `not_granted`
- `not_found`
- `unknown`

列表 API 也内嵌相同诊断，供 picker 直接展示。

## Dependency diagnostics 边界

诊断是只读建议：

- builtin 与当前 `ToolRouter` descriptor 比对；
- deferred/tool_search 只查当前 descriptor/search candidate；
- MCP 先查当前 descriptor，再读 MCP config 和 trust grant；
- 已配置但未授权返回 `not_granted`；
- 已授权但当前未连接/未列出工具返回 `deferred`。

诊断不会：

- 调用 `tool_search`；
- 激活 deferred descriptor；
- 连接或启动 MCP server；
- 写 grant；
- 阻止显式 skill 注入；
- 改变 Plan mode。

因此 `deferred` 表示运行期仍需正常发现/连接流程，不等于依赖已执行验证。

## UI 和 WebSocket

workspace 确定后 `useSkills` 加载列表。Composer picker 支持：

- 多选；
- duplicate name 的 scope/relative-dir 消歧；
- enable/disable；
- loader/API error；
- dependency warning；
- 手动 force reload。

发送 prompt 时使用：

```json
{
  "skills": [
    {
      "name": "code-review",
      "path": "D:/repo/.automata/skills/code-review/SKILL.md"
    }
  ]
}
```

session 或 workspace 变化时选择和 runtime notice 会清空；成功发送后清空本次选择。前端不读取 `SKILL.md` 正文。

以下 durable events 已加入 socket union 并显示为 notice：

- `skills_loaded`
- `skills_warning`
- `skill_injected`

## 安全边界

已实现：

- 只允许选择扫描结果中的 canonical path；
- asset 路径必须位于 skill 的 `assets/`；
- scan depth、目录数、metadata/body 预算；
- 损坏 skill fail-open；
- body 只进入当前 reply；
- enable API 不能操作任意文件；
- Skills 不改变 ToolRouter、MCP grant 或审批。

仍需注意：

- roots 是受信任的本地 prompt 来源；
- `scripts/` 不自动运行，但指令可能要求模型调用正常获批工具；
- dependencies 不是安全策略；
- 没有 remote install、签名或来源验证。

## 测试覆盖

后端：

- frontmatter/interface/dependencies；
- 损坏 skill fail-open 和 metadata warning；
- repo 移动后稳定 ID；
- shared cache 与 force reload；
- disabled state 跨 manager 重建；
- disabled skill 不可显式注入；
- API list/enable/diagnostics；
- act/plan 注入和临时上下文。

前端 Vitest：

- duplicate name 消歧和精确 ID 选择；
- session/workspace 变化清理；
- warning/injected event；
- Skills UI 与 socket 类型编译。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest
npm --prefix ui test
npm --prefix ui run build
```

## Deferred

### Packaged system skills

root wiring 已存在，但没有可发布内容。等待具体 skill、维护 owner、版本升级和 repo/user override 规则。

### Plugin roots 和 trust

等待 plugin 安装生命周期、稳定来源身份、卸载清理、冲突优先级和 trust model。

## 明确不实现

- 常驻文件 watcher：当前规模使用 fingerprint、短 TTL 和显式 reload。
- invocation telemetry：没有隐私、留存、开关和 telemetry 基础设施。
- Skill 自动授予工具/MCP 权限：违反现有 ToolRouter、grant 和审批边界。
- 自然语言 automatic invocation：当前没有足够产品需求，兼容字段只保留解析。
