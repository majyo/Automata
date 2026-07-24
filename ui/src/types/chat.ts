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

export type PersistedPlanStatus = "pending" | "executing" | "failed" | "executed" | "superseded";
export type PlanStatus = PersistedPlanStatus | "approving";
export type SendMode = "execute" | "plan";
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
  metadata?: ToolRunMetadata | null;
  plan_id?: string;
  plan_status?: PlanStatus;
  sequence?: number;
  created_at?: string;
};
