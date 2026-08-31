# Agent Run Trace Side Quest 设计方案

## Status

- 文档状态：`DEFERRED`，保留设计方向，但短期内没有实施计划
- 最近核对：2026-07-23
- 代码基线：`main` / `814d963`
- 功能定位：建立在现有持久化 Run 之上的可选展示能力，不新建第二套 Run 生命周期
- 重新进入 Planned 的条件：确认近期产品需求、实施 owner、交付优先级和可执行时间窗口
- 关联文档：[Skills 系统设计方案](../Planned/skills-system-design.md)、[Run 持久化恢复方案](../Archived/agent-run-persistence-recovery-design.md)

## 结论

当前项目已经实现通用 Run 的创建、状态机、事件持久化、重放、取消、审批等待和前端断线恢复，但尚未实现面向用户的“规划 / 分析 / 执行 / 汇总”轨迹。

本 side quest 的正确落点是：

1. 复用现有 `agent_runs`、`agent_run_events`、`run_id`、`seq` 和恢复协议。
2. 在同一 Run 事件序列中追加 `category='trace'` 的扩展事件。
3. 第一版只由后端根据真实 runtime 事件生成执行事实，不接受模型自报的 planning、analysis 或 progress。
4. 前端使用固定的核心 phase schema 和独立的折叠 UI，不把 phase/item 伪装成聊天消息。
5. 不展示或持久化 provider 的原始 `reasoning_content`。

第一版只承诺“准备 / 执行 / 汇总”的可验证事实投影。没有可信 runtime 事件来源的“规划 / 分析”不生成占位内容，也不通过提示词要求模型解释自己的思考过程。

## 当前实际代码基线

### 已实现的通用 Run 基础设施

| 能力 | 当前实现 |
| --- | --- |
| Run 数据 | `api/automata_api/db/baseline.py` 在当前基线中创建 `agent_runs` |
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
- `RunTraceProjector`；
- 模型进度上报 control tool（第一版明确不实现）；
- 任何以 `category='trace'` 调用 `append_event()` 的生产代码；
- trace phase 或 item schema；
- Run Trace feature flag；
- `runTraceReducer`、`RunTraceCard` 或 Trace UI；
- Skill trace profile（第一版明确不实现）；
- Markdown/GFM renderer。

`MessageBubble.tsx` 对普通 agent 消息仍使用 `<p>`，不支持标题、列表、代码块或 GFM table。`SocketPayload` 也尚未定义 Trace 或 Skills 事件类型。

## 产品边界

### 展示公开运行摘要，不展示原始思维链

如果未来增加公开分析摘要，其内容只能是面向用户的结论和证据说明，例如：

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

第一版不实现模型驱动的 `report_progress`。原因是模型自报的阶段、进度和“分析”不是 runtime 权威事实，容易与真实工具执行不一致，也会把提示工程噪声固化为产品协议。将来只有在出现独立、可验证的公开摘要来源和明确产品需求后，才重新立项评估。

### 与 Plan mode 的边界

第一版 Trace 没有 `planning` phase。`preparation` 只表示 Run 已开始、模型调用和 runtime 初始化状态，不是可审批计划，也不声称展示了模型规划。

- Trace phase 不写 `session_plans`。
- Trace phase 不复用 `PlanBubble` 或 plan status。
- `chat_plan` 和 `plan_execution` 仍遵循现有 Plan 业务语义。
- MVP 先只为 `chat_act` 启用 Trace；扩展到其他 Run kind 时不得改变审批和重试语义。

### 与 Skills 和 ToolRouter 的边界

- 第一版 phase id、状态转换和 item kind 由 runtime 固定定义。
- Skill 不能声明任意 phase、改变 Trace 状态机或决定 UI 结构。
- Skill 不能注册工具、写事件、修改数据库或绕过策略。
- 所有执行类工具继续经过 `ToolRouter` 和 `ToolExecutionOrchestrator`。
- 第一版不向 ToolRouter 注册 Trace control tool。

未来若确有多个展示风格，只允许 Skill 选择 runtime 预注册的有限 profile；profile 只能改变标签或默认展开策略，不能新增 phase、事件类型或权限。

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

- `phase_upsert`
- `item_upsert`

规则：

1. `seq` 使用当前 Run 的全局事件序号，不创建 Trace 私有序号。
2. `phase_id`、`item_id` 必须稳定，更新使用 upsert。
3. UI 按 `run_id + seq` 幂等处理。
4. 未识别的 Trace event 在相同 schema major 下安全忽略。
5. Trace 事件必须以 `category='trace'` 持久化。
6. REST 和 WebSocket 传输完全相同的 payload。
7. Trace 不定义 `trace_started`、`trace_completed` 或 `trace_failed`；开始和终态直接复用现有 `started`、`done`、`error`、`run_cancelled`、`run_interrupted`。

### Phase 和 Item

第一版固定 phase：

| phase id | 标签 | 权威数据来源 |
| --- | --- | --- |
| `preparation` | 准备 | `started`、`agent_step`、context / MCP / Skills 状态事件 |
| `execution` | 执行 | runtime 派生的工具、审批和结果 |
| `summary` | 汇总 | `done` 对应的最终响应引用，或失败/取消/中断终态 |

第一版 item kind：

- `step`
- `warning`
- `metric`
- `tool_ref`
- `approval_ref`

所有 item 都只能由 runtime 生成。`agent_step.message` 只能作为“正在调用模型”的执行状态使用，不能改写为模型的推理或计划内容。

### Artifact 边界

第一版不建立通用 Artifact 子系统，也不定义 `artifact_upsert`。当前已有的权威载体已经覆盖第一版需求：

- 工具调用和结果：`tool_call_id` 关联的现有 tool run message / `ToolCard`；
- 最终回答：现有 agent message；
- 外部资源：工具结果中的引用或链接。

只有在项目中出现真实的结构化内容生产者、稳定 schema 和明确 UI 消费者后，才单独设计 Artifact。届时也应保存引用而不是复制完整工具输出。

建议限制：

- 每个 Run 最多 500 条 Trace 事件；
- 最多 100 个 items；
- 单 Trace event 不超过现有 65536-byte Run event 上限；
- Trace 扩展累计 payload 默认不超过 2 MiB。

## 后端实现建议

### 新模块

```text
api/automata_api/agent/trace/
  model.py
  events.py
  limits.py
  projector.py
  sink.py
```

`RunTraceProjector` 不依赖 FastAPI、WebSocket 或 repository。它接收已经发生的 runtime 事件并生成 transport-neutral 的 phase/item upsert payload。

Service 增加独立 Trace sink：

1. 调用 `run_repository.append_event(..., category="trace")`。
2. 通过现有 `RunEventHub` 广播已经持久化的事件。
3. 捕获 Trace 写入失败，停用本 Run 后续 Trace；不能让主 Run 失败。
4. 只消费 runtime 已确认事件，不解析模型原始 stream 或 `reasoning_content`。

不能直接复用 `DurableRunEventSink.send_json()` 写 Trace，因为该方法当前总是使用 repository 的默认 `category='runtime'`。

### Runtime 投影规则

- `started` 创建 `preparation` phase；
- `agent_step` 更新当前 step，只显示 step number、mode 和“调用模型”这类 runtime 文案；
- `tool_call` / `tool_result` 创建或更新稳定的 `tool_ref`；
- `tool_approval_required` / `tool_approval_resolved` 创建或更新 `approval_ref`；
- `token` 只能更新“模型正在输出”和字符计数；当前协议无法在流式过程中保证某个 token 属于最终回答，因为 token 后仍可能出现 `tool_call`；
- `done` 使用现有最终响应引用创建并完成 `summary`；`error`、`run_cancelled`、`run_interrupted` 创建对应终态摘要并收束所有未终结 phase；
- 工具状态、成功失败和耗时必须来自编排器或真实工具结果，不能由模型文本推断；
- 重放同一 runtime / Trace 事件必须得到相同投影。

## 前端实现建议

新增：

```text
ui/src/types/runTrace.ts
ui/src/state/runTraceReducer.ts
ui/src/components/conversation/RunTraceCard.tsx
ui/src/components/conversation/TracePhase.tsx
ui/src/components/conversation/TraceItem.tsx
```

规则：

- Trace state 与 `ChatMessage[]` 分离；
- 复用现有 Run resume 流程，不新增第二个 WebSocket；
- 收到 `run_trace` 时才创建 Trace UI；
- 当前 phase 在 streaming 时可自动展开，完成后默认折叠；
- 工具详情复用现有 `ToolCard` 展示逻辑；
- 当前 UI 尚无 Vitest；若要做 reducer/component 自动测试，必须先明确引入测试框架。

安全 Markdown/GFM 是普通 agent message 的全局 UI 能力，不属于 Trace 协议前置条件。它应独立实现、独立测试，并禁止 raw HTML。

## Skill Trace Profile

第一版不实现 Skill Trace Profile，也不扩展 `agents/openai.yaml`。Trace 是 Run 的 runtime 事实投影，不应因选择了不同 Skill 而改变结构或事实。

如果未来确认确有展示 profile 需求，只允许引用 runtime 预注册的 profile id，并定义确定性的多 Skill 冲突规则；任意 phase 列表、item kind 或自定义事件仍不允许由 Skill 提供。

## Feature Flags

建议新增：

```text
AUTOMATA_RUN_TRACE_ENABLED=false
AUTOMATA_RUN_TRACE_MAX_EVENTS=500
AUTOMATA_RUN_TRACE_MAX_PAYLOAD_BYTES=2097152
```

这些变量当前尚不存在。

- Trace disabled：只保留现有通用 Run 和 runtime 事件。
- Trace enabled：生成 runtime 派生的 preparation/execution/summary 投影。

## 未来实施顺序（未排期）

以下顺序只描述未来重新立项后的依赖关系，不代表当前迭代承诺。除已经落地的通用 Run Foundation 外，Run Trace 各阶段均处于 `DEFERRED`。

### Foundation：通用 Run 持久化与恢复

状态：`DONE`

现有 `agent_runs`、`agent_run_events`、REST、WebSocket resume、前端 sequence 恢复和取消语义可直接复用。

### S0：协议、限制和 feature flag

状态：`DEFERRED`

- 定义 Trace model、事件 fixture 和限制；
- 增加配置；
- 确认 disabled 时不产生 `category='trace'` 事件。

### S1：Runtime 派生轨迹

状态：`DEFERRED`

- 实现 projector 和 Trace sink；
- 从 `started` / `agent_step` / `tool_call` / `tool_result` / 审批 / `token` / terminal event 派生固定 phase；
- 不加入模型 reporting、Artifact 或 Skill profile。

### S2：前端折叠 UI 与恢复

状态：`DEFERRED`

- 增加类型、reducer 和组件；
- 接入现有 replay 事件流；
- 处理重复、sequence gap 和未知 event。

不新增 Run 表或 runs/events REST API；这些已经实现。

### S3：可靠性与验收

状态：`DEFERRED`

- 容量限制、敏感信息清洗和 Trace 失败隔离；
- 自动化与手工验收。

### 独立 UI 工作：安全 Markdown/GFM

这不是 Run Trace 的前置条件或阶段门禁，也不由本 Deferred 文档排期。

- 为普通 agent message 使用成熟 renderer 和 GFM 插件；
- 禁止 raw HTML，限制危险链接协议；
- 独立覆盖 table、代码块、链接和恶意 HTML 测试；
- Trace 未启用时也应正常工作。

### Deferred：结构化 Artifact

状态：`DEFERRED`

等待真实的结构化内容生产者、稳定 schema、持久化契约和 UI 消费者后再设计；不作为 Run Trace 第一版的验收前提。

## 验证计划

后端新增测试：

- projector 状态、upsert、限制和幂等；
- category 确实为 `trace`；
- disabled 时现有事件序列不变；
- `reasoning_content` 不进入事件；
- tool 事实和真实 `tool_result` 一致；
- Trace repository 失败不影响 `done`；
- REST/WebSocket replay 得到相同 Trace 投影；
- cancel、failed、interrupted 映射正确。

前端新增测试：

- reducer 幂等、gap 和未知事件；
- phase/item upsert；
- session 切换和 replay；
- disabled 时旧消息渲染不变。

全局 Markdown 按独立 UI 工作测试，不计入 Trace reducer 验收。

回归命令：

```powershell
uv run --directory api --group dev --locked pytest
npm --prefix ui run build
```

## 验收标准

1. 默认配置不改变现有 agent、Plan、Skills、MCP、审批、取消和 UI 行为。
2. Trace 使用现有 Run id 和全局 sequence。
3. Trace 事件写入 `category='trace'`，可经现有 REST/WebSocket 完整回放。
4. Trace 不依赖模型 control tool、Skill profile 或自报进度。
5. 工具成功失败与真实 `tool_result` 一致。
6. 原始 `reasoning_content` 不进入 WebSocket、数据库或 UI。
7. Trace 写入或渲染失败不使主 Run 失败。
8. UI 可折叠固定 phase，并复用现有工具详情。
9. 全量 API 测试和 UI build 通过。
