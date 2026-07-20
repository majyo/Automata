# Agent Run Trace Side Quest 设计方案

## Status

- 文档状态：Planned，尚未实现 Run Trace 扩展
- 最近核对：2026-07-20
- 代码基线：`main` / `d0f2eea`
- 功能定位：建立在现有持久化 Run 之上的可选展示能力，不新建第二套 Run 生命周期
- 关联文档：[Skills 系统设计方案](./skills-system-design.md)、[Run 持久化恢复方案](./Archived/agent-run-persistence-recovery-design.md)

## 结论

当前项目已经实现通用 Run 的创建、状态机、事件持久化、重放、取消、审批等待和前端断线恢复，但尚未实现面向用户的“规划 / 分析 / 执行 / 汇总”轨迹。

本 side quest 的正确落点是：

1. 复用现有 `agent_runs`、`agent_run_events`、`run_id`、`seq` 和恢复协议。
2. 在同一 Run 事件序列中追加 `category='trace'` 的扩展事件。
3. 由后端根据真实 runtime 事件生成执行事实；模型只能通过受约束的 control tool 提交公开进度摘要。
4. 前端新增独立的 Trace 状态和折叠 UI，不把 phase/item 伪装成聊天消息。
5. 不展示或持久化 provider 的原始 `reasoning_content`。

## 当前实际代码基线

### 已实现的通用 Run 基础设施

| 能力 | 当前实现 |
| --- | --- |
| Run 数据 | `api/automata_api/db/migrations/v0002_runs.py` 创建 `agent_runs` |
| Run 类型 | `chat_act`、`chat_plan`、`plan_execution` |
| Run 状态 | `queued`、`running`、`waiting_approval`、`cancelling`、`completed`、`failed`、`cancelled`、`interrupted` |
| 并发约束 | 数据库唯一索引保证一个 session 同时最多一个非终态 Run |
| 事件存储 | `agent_run_events` 使用 `(run_id, sequence)` 主键，category 已允许 `runtime` / `trace` |
| 事件信封 | repository 为事件补充 `session_id`、`run_id`、`seq`、`schema_version=1` |
| 事件发送 | `DurableRunEventSink` 先持久化再通过 `RunEventHub` 广播 |
| Token 合并 | 默认累计到 4096 字符或 100 ms 后写入一条持久化 `token` 事件 |
| 单事件限制 | `AUTOMATA_RUN_EVENT_MAX_BYTES`，默认 65536 bytes |
| 生命周期 | `RunCoordinator` 让 Run 独立于 WebSocket 连接继续执行 |
| 取消 | 明确 `cancel_run` 后进入 `cancelling`，取消 task 并终止已注册的进程树 |
| 重启恢复 | API 启动时把其他 instance 遗留的非终态 Run 标为 `interrupted` |
| 事件保留 | 终态 Run 默认保留 30 天事件；过期后仍保留最后一条终态事件 |
| REST 查询 | `/runs`、`/sessions/{session_id}/runs`、单 Run 和 events cursor API |
| WebSocket 恢复 | `ready.active_runs`、`resume_run`、`run_resume_started`、`run_resume_complete` |
| 前端状态 | `chatReducer.ts` 已维护 `runsById`、active Run、审批和 last sequence |
| 序列缺口 | `useAgentSocket.ts` 检测重复/缺口并通过 `resume_run` 补发 |

当前持久化事件的真实形状是：

```json
{
  "type": "tool_result",
  "session_id": "session-id",
  "run_id": "run-id",
  "seq": 8,
  "schema_version": 1,
  "tool_call_id": "call-id",
  "tool": "exec_command",
  "success": true,
  "content": "..."
}
```

`category` 当前只存在于数据库列中，不会由 `list_events()` 自动放回 payload。Trace 客户端必须依赖版本化的 `type` / `event` 字段识别 Trace 事件，不能假设 REST 响应中存在 `category`。

### 已实现但尚未形成 Trace UI 的事件

当前 runtime 和 service 已持久化、广播以下事件：

- `started`
- `agent_step`
- `context_compressed`
- `tool_call`
- `tool_result`
- `token`
- `plan_ready`
- `plan_approved`
- `tool_approval_required`
- `tool_approval_resolved`
- `run_cancel_requested`
- `done`
- `error`
- `run_cancelled`
- `run_interrupted`
- MCP status/candidate 事件
- `skills_loaded`、`skills_warning`、`skill_injected`

这些事件足以派生执行阶段、工具成功失败、审批等待和汇总开始时间，但当前前端只把它们用于消息、工具卡、审批卡和 Run 状态恢复。

### 当前明确未实现

仓库中目前没有：

- `api/automata_api/agent/trace/` 包；
- `RunTraceRecorder`；
- `report_progress` control tool；
- 任何以 `category='trace'` 调用 `append_event()` 的生产代码；
- trace phase、item 或 artifact schema；
- Run Trace feature flag；
- `runTraceReducer`、`RunTraceCard` 或 Trace UI；
- Skill trace profile；
- Markdown/GFM renderer。

`MessageBubble.tsx` 对普通 agent 消息仍使用 `<p>`，不支持标题、列表、代码块或 GFM table。`SocketPayload` 也尚未定义 Trace 或 Skills 事件类型。

## 产品边界

### 展示公开运行摘要，不展示原始思维链

用户可见的“分析”只能包含：

- 正在比较什么；
- 当前证据支持或不支持什么；
- 为什么增加或停止某类查询；
- 最终选择依据；
- 证据不足和降级说明。

以下内容不得进入 Trace：

- provider 原始 `reasoning_content`；
- 隐藏 chain-of-thought；
- 未经工具结果支持的数字；
- API key、Authorization header、密码或 secret；
- 未截断的 stdout、文件内容或 MCP 原始大响应。

Runtime 负责计算工具名、成功/失败、耗时和实际引用；模型不能填写这些事实字段。

### 与 Plan mode 的边界

Trace 的 `planning` phase 只是单次 Run 的展示阶段，不是可审批计划。

- Trace phase 不写 `session_plans`。
- Trace phase 不复用 `PlanBubble` 或 plan status。
- `chat_plan` 和 `plan_execution` 仍遵循现有 Plan 业务语义。
- MVP 先只为 `chat_act` 启用 Trace；扩展到其他 Run kind 时不得改变审批和重试语义。

### 与 Skills 和 ToolRouter 的边界

- Skill 可以声明推荐 phase 和公开摘要规则。
- Skill 不能注册工具、写事件、修改数据库或绕过策略。
- `report_progress` 必须由 runtime provider 注册。
- 所有执行类工具继续经过 `ToolRouter` 和 `ToolExecutionOrchestrator`。
- Trace control tool 不计入普通工具成功/失败统计，也不保存为普通 `tool_run` 消息。

## 协议设计

### 复用现有 Run

不再定义第二个 Trace Run 状态机。身份和终态直接使用现有 `agent_runs`：

```text
run_id
session_id
kind
mode
status
request_message_id
response_message_id
last_sequence
error_code
public_error
```

Trace 自己只维护展示投影：

```typescript
type RunTrace = {
  runId: string;
  phasesById: Record<string, TracePhase>;
  phaseOrder: string[];
  itemsById: Record<string, TraceItem>;
  artifactsById: Record<string, TraceArtifact>;
  lastTraceSequence: number;
  incomplete: boolean;
};
```

### Trace 事件信封

Trace 事件继续使用现有顶层序列字段：

```json
{
  "type": "run_trace",
  "event": "item_upsert",
  "session_id": "session-id",
  "run_id": "run-id",
  "seq": 12,
  "schema_version": 1,
  "data": {}
}
```

初始 `event` 集合：

- `trace_started`
- `phase_upsert`
- `item_upsert`
- `artifact_upsert`
- `trace_completed`
- `trace_failed`

规则：

1. `seq` 使用当前 Run 的全局事件序号，不创建 Trace 私有序号。
2. `phase_id`、`item_id`、`artifact_id` 必须稳定，更新使用 upsert。
3. UI 按 `run_id + seq` 幂等处理。
4. 未识别的 Trace event 在相同 schema major 下安全忽略。
5. Trace 事件必须以 `category='trace'` 持久化。
6. REST 和 WebSocket 传输完全相同的 payload。

### Phase 和 Item

MVP 标准 phase：

| phase id | 标签 | 权威数据来源 |
| --- | --- | --- |
| `planning` | 规划 | 受约束的公开 query plan |
| `analysis` | 分析 | 受约束的公开摘要和决策 |
| `execution` | 执行 | runtime 派生的工具、审批和结果 |
| `summary` | 汇总 | 首个最终 token、完成或失败状态 |

MVP item kind：

- `public_summary`
- `query_plan`
- `decision`
- `warning`
- `metric`
- `tool_ref`
- `artifact_ref`

`metric`、`tool_ref` 和执行状态只能由 runtime 生成。

### Artifact

MVP 只支持有限结构：

- `table`
- `key_value`
- `text_excerpt`
- `tool_result_ref`

Artifact 不复制完整工具输出。大内容继续以现有 tool run message 或外部资源为权威来源。

建议限制：

- 每个 Run 最多 500 条 Trace 事件；
- 最多 100 个 items；
- table 最多 200 行、20 列；
- cell 最大 1 KiB；
- 单 Trace event 不超过现有 65536-byte Run event 上限；
- Trace 扩展累计 payload 默认不超过 2 MiB。

## 后端实现建议

### 新模块

```text
api/automata_api/agent/trace/
  model.py
  events.py
  limits.py
  recorder.py
  progress_tool.py
  provider.py
  profile.py
```

`RunTraceRecorder` 不依赖 FastAPI、WebSocket 或 repository。它生成 transport-neutral payload。

Service 增加独立 Trace sink：

1. 调用 `run_repository.append_event(..., category="trace")`。
2. 通过现有 `RunEventHub` 广播已经持久化的事件。
3. 捕获 Trace 写入失败，停用本 Run 后续 Trace；不能让主 Run 失败。

不能直接复用 `DurableRunEventSink.send_json()` 写 Trace，因为该方法当前总是使用 repository 的默认 `category='runtime'`。

### `report_progress`

建议输入：

```json
{
  "phase": "analysis",
  "item_id": "compare-options",
  "kind": "public_summary",
  "title": "比较候选方案",
  "summary": "现有证据支持方案 A，但缺少长期稳定性数据。",
  "status": "completed",
  "evidence_refs": ["call_7"]
}
```

服务端必须验证：

- phase 和 item kind 白名单；
- title/summary 长度；
- status 转换；
- evidence ref 是否属于当前 Run；
- 禁止模型提交 metric、耗时和成功失败计数。

当前 `create_mcp_tool_runtime()` 只接收 Backend provider 和 MCP providers，没有额外 provider 参数。实现 control tool 时应扩展 builder 输入，或在 service 中统一构造 router；不要让 Skill 直接注册工具。

## 前端实现建议

新增：

```text
ui/src/types/runTrace.ts
ui/src/state/runTraceReducer.ts
ui/src/components/conversation/RunTraceCard.tsx
ui/src/components/conversation/TracePhase.tsx
ui/src/components/conversation/TraceItem.tsx
ui/src/components/conversation/TraceArtifact.tsx
ui/src/components/conversation/MarkdownMessage.tsx
```

规则：

- Trace state 与 `ChatMessage[]` 分离；
- 复用现有 Run resume 流程，不新增第二个 WebSocket；
- 收到 `run_trace` 时才创建 Trace UI；
- 当前 phase 在 streaming 时可自动展开，完成后默认折叠；
- 工具详情复用现有 `ToolCard` 展示逻辑；
- Markdown 使用成熟 renderer 和 GFM 插件，禁止 raw HTML；
- 当前 UI 尚无 Vitest；若要做 reducer/component 自动测试，必须先明确引入测试框架。

## Skill Trace Profile

后续可在 `agents/openai.yaml` 增加：

```yaml
trace:
  profile: research
  phases:
    - id: planning
      label: 规划
      allowed_item_kinds: [query_plan, warning]
    - id: analysis
      label: 分析
      allowed_item_kinds: [public_summary, decision, warning]
```

当前 loader 没有 `trace` 字段，`SkillMetadata` 也没有 `trace_profile`。这部分必须作为新 schema 实现并测试，不能在文档中视为现有能力。

多个被选 Skill 声明 profile 时，建议第一个结构化选择的合法 profile 获胜；不合并 phase 列表。

## Feature Flags

建议新增：

```text
AUTOMATA_RUN_TRACE_ENABLED=false
AUTOMATA_RUN_TRACE_MODEL_REPORTING_ENABLED=false
AUTOMATA_RUN_TRACE_MAX_EVENTS=500
AUTOMATA_RUN_TRACE_MAX_PAYLOAD_BYTES=2097152
```

这些变量当前尚不存在。

- Trace disabled：只保留现有通用 Run 和 runtime 事件。
- Trace enabled / model reporting disabled：只生成 runtime 派生的 execution/summary。
- 两者都启用：额外暴露 `report_progress`。

## 实施阶段

### Foundation：通用 Run 持久化与恢复

状态：`DONE`

现有 `agent_runs`、`agent_run_events`、REST、WebSocket resume、前端 sequence 恢复和取消语义可直接复用。

### S0：协议、限制和 feature flag

状态：`TODO`

- 定义 Trace model、事件 fixture 和限制；
- 增加配置；
- 确认 disabled 时不产生 `category='trace'` 事件。

### S1：Runtime 派生轨迹

状态：`TODO`

- 实现 recorder 和 Trace sink；
- 从 `tool_call` / `tool_result` / `token` / terminal event 派生 execution/summary；
- 不加入模型 reporting。

### S2：前端折叠 UI 与恢复

状态：`TODO`

- 增加类型、reducer 和组件；
- 接入现有 replay 事件流；
- 处理重复、sequence gap 和未知 event。

不新增 Run 表或 runs/events REST API；这些已经实现。

### S3：公开摘要和 Skill Profile

状态：`TODO`

- 实现 `report_progress` provider；
- 扩展 router builder；
- 扩展 Skill loader 和冲突规则。

### S4：Markdown、可靠性和验收

状态：`TODO`

- 安全 Markdown/GFM；
- 容量限制、敏感信息清洗和 Trace 失败隔离；
- 自动化与手工验收。

## 验证计划

后端新增测试：

- recorder 状态、upsert、限制和幂等；
- category 确实为 `trace`；
- disabled 时现有事件序列不变；
- `reasoning_content` 不进入事件；
- tool 事实和真实 `tool_result` 一致；
- Trace repository 失败不影响 `done`；
- REST/WebSocket replay 得到相同 Trace 投影；
- cancel、failed、interrupted 映射正确。

前端新增测试：

- reducer 幂等、gap 和未知事件；
- phase/item/artifact upsert；
- session 切换和 replay；
- Markdown table、代码块和恶意 HTML；
- disabled 时旧消息渲染不变。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest
npm --prefix ui run build
```

## 验收标准

1. 默认配置不改变现有 agent、Plan、Skills、MCP、审批、取消和 UI 行为。
2. Trace 使用现有 Run id 和全局 sequence。
3. Trace 事件写入 `category='trace'`，可经现有 REST/WebSocket 完整回放。
4. 模型不调用 `report_progress` 时仍可显示 runtime 派生轨迹。
5. 工具成功失败与真实 `tool_result` 一致。
6. 原始 `reasoning_content` 不进入 WebSocket、数据库或 UI。
7. Trace 写入或渲染失败不使主 Run 失败。
8. UI 可折叠 phase、复用工具详情并安全显示有限 artifact。
9. 最终回答支持安全 Markdown/GFM。
10. 全量 API 测试和 UI build 通过。
