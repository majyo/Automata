# Automata 可观测性采集系统

## 目标与边界

本系统用于采集足以区分本地 Agent、远端 LLM 调用、工具执行、持久化和
WebSocket 广播耗时的数据。

本轮只实现采集，不实现自动性能归因、阈值、告警、查询 API、Profiler UI、
OTLP exporter 或 Python 调用栈采样。

客户端数据能准确分离“本地 Agent 时间”和“远端 provider 请求时间”，但只有
provider 返回 `Server-Timing` 等服务端信息时，才能继续区分网络、供应商排队
和模型纯计算。

## 启动模式

普通启动始终开启低开销 `diagnostic`：

```powershell
.\run.ps1 -Mode headless
```

Profile 必须在进程启动时显式开启：

```powershell
.\run.ps1 -Mode headless -Profile
.\run.ps1 -Mode headless -Profile -ProfileCaptureContent
```

`-ProfileCaptureContent` 未与 `-Profile` 同时使用时会拒绝启动。运行期间不能
通过 UI、HTTP 或 WebSocket 切换采集模式。

## 三种采集等级

| 能力 | diagnostic | profile | profile + content |
|---|---:|---:|---:|
| 结构化日志和应用 spans | 是 | 是 | 是 |
| LLM SSE chunk 元数据 | 否 | 是 | 是 |
| CPU、RSS、线程数、event-loop lag | 否 | 是 | 是 |
| Prompt、回复和工具正文 | 否 | 否 | 脱敏后保存 |

普通 diagnostic 和 profile 只记录长度、数量、哈希、类型、状态、时间和允许
名单内的响应头。API key、Authorization、cookie、token、password 和 secret
字段永不写入。

完整内容采用 best-effort 脱敏，仍应视为敏感数据。

## Span 结构

```text
agent.run
├─ session.config.load
├─ mcp.runtime.start
│  ├─ mcp.server.initialize
│  └─ mcp.tools.list
├─ skills.resolve
├─ context.load
├─ agent.step
│  ├─ llm.call
│  ├─ tool.call
│  │  ├─ tool.policy.evaluate
│  │  ├─ tool.approval.wait
│  │  └─ tool.execute
│  │     └─ mcp.call
│  ├─ context.message.persist
│  └─ context.compress
├─ response.persist
└─ runtime event sink summary
```

每次 `llm.call` 记录：

- Payload 序列化完成。
- HTTP 请求开始。
- 响应头到达。
- 首个 SSE。
- 首个 reasoning、content 和 tool-call delta。
- 流结束。
- Chunk 数、reasoning/content 字符数、最大 chunk 间隔。
- HTTP 状态、允许名单内的 request ID 和 `Server-Timing`。
- Provider 返回的 usage；未提供时为 `null`。

Profile 模式额外逐 chunk 记录时间偏移、wire 字符数、delta 类型和间隔，不
记录正文。

## 文件与数据库

默认位置：

```text
<AUTOMATA_DATA_DIR>/observability/
├─ observability.db
├─ logs/
│  └─ automata-*.jsonl
└─ profiles/
   └─ <profile-session-id>/
      ├─ manifest.json
      ├─ events-*.jsonl
      ├─ samples-*.jsonl
      └─ content-*.jsonl
```

`observability.db` 只保存 profile session、trace、已完成 span 和 collector
health 的查询索引。高频 samples 和 SSE 元数据只进入 JSONL。

该数据库不与业务 `automata.db` 建立外键，也不复用 `agent_run_events`。
两套数据通过 `run_id` 和 `session_id` 关联。

## 公共记录字段

每条 JSONL 记录至少包含：

```json
{
  "schema_version": 1,
  "record_type": "span_end",
  "timestamp_utc": "2026-07-24T08:00:00+00:00",
  "monotonic_ns": 1234567890,
  "boot_id": "process-id",
  "profile_session_id": null,
  "trace_id": "32-hex",
  "span_id": "16-hex",
  "parent_span_id": null,
  "run_id": "run-id",
  "session_id": "session-id"
}
```

耗时只使用 `monotonic_ns` 计算；UTC 时间仅用于跨文件检索。字段采用只增不改
策略，破坏性修改必须提升 `schema_version`。

## 背压与失败策略

- 普通队列默认 8192 条，满时丢弃低优先级记录。
- 关键队列默认 256 条，保存 terminal、error、trace 和关键 span。
- 关键队列也满时写入 `critical-fallback.jsonl`。
- 每十秒及关闭时记录 collector health 和丢弃数。
- Observability 目录或 writer 故障时采集自动降级，不阻止 Agent 启动和运行。
- Profile sampler 默认每 200ms 采集一次，不进行调用栈采样。

## 默认保留策略

- Diagnostic：30 天或 512MB，先到者生效。
- Profile：7 天或 2GB。
- Profile content：24 小时或 500MB。

清理范围仅限 `observability` 目录及其索引，不删除会话、消息、业务 run 或
`agent_run_events`。
