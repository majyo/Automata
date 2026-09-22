export type ToolRunResult = {
  success?: boolean;
  content?: string;
};

export type ToolLiveOutput = {
  stdout: string;
  stderr: string;
  truncated?: boolean;
};

export type ToolRunMetadata = {
  tool_call_id?: string;
  tool?: string;
  arguments?: string;
  result?: ToolRunResult | null;
  live_output?: ToolLiveOutput;
};

/**
 * Metadata the backend stores on a user message that came from a steer or
 * queue input; it is how a settled input is reconciled with its message.
 */
export type InputMessageMetadata = {
  input_id?: string;
  delivery?: InputDelivery;
  request_id?: string;
};

export type MessageMetadata = ToolRunMetadata & InputMessageMetadata;

export type PersistedPlanStatus = "pending" | "executing" | "failed" | "executed" | "superseded";
export type PlanStatus = PersistedPlanStatus | "approving";
export type SendMode = "execute" | "plan";

/**
 * Where a submitted prompt goes: a new Run, a steering message for the Run
 * that is already active, or a queued follow-up that becomes its own Run
 * once the session is free.
 */
export type InputDelivery = "new" | "steer" | "queue";

export type PendingInputStatus = "sending" | "pending" | "cancelling" | "cancelled";

/**
 * A prompt the user submitted while the session's Run was still active.
 *
 * It is deliberately not part of the conversation yet: the backend decides
 * where the input lands, so the message bubble is only created when an
 * `input_applied` event or the queued Run's `started` event reports it.
 *
 * A cancelled entry stays in the list instead of disappearing: when the
 * backend withdraws it because the Run it followed failed, the user still
 * needs the text in order to queue it again.
 */
export type PendingInput = {
  requestId: string;
  sessionId: string;
  prompt: string;
  delivery: Exclude<InputDelivery, "new">;
  status: PendingInputStatus;
  inputId?: string;
  position?: number | null;
  runId?: string | null;
  /** Set when the backend withdrew this input instead of delivering it. */
  cancelReason?: "predecessor_failed";
  /** Request id of an in-flight 插话 attempt for this queued input. */
  steerRequestId?: string;
};
export type ToolRunStatus = "running" | "completed" | "failed";
export type PersistedRunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";
export type RunStatus = PersistedRunStatus | "idle";
export type ApprovalDecision = "allow_once" | "allow_for_run" | "deny";

export type ToolApprovalRequest = {
  approval_id: string;
  run_id: string;
  session_id: string;
  tool_call_id: string;
  tool: string;
  risk: "read" | "write" | "command" | "destructive" | "external";
  reason: string;
  summary: string;
  preview: Record<string, unknown>;
  options: ApprovalDecision[];
};

export type ChatMessage = {
  id: string;
  session_id?: string;
  role: "user" | "agent" | "tool";
  text: string;
  kind?: "normal" | "plan" | "tool_run";
  metadata?: MessageMetadata | null;
  plan_id?: string;
  plan_status?: PlanStatus;
  sequence?: number;
  created_at?: string;
};
