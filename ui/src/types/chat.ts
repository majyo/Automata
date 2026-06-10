export type ToolRunResult = {
  success?: boolean;
  content?: string;
};

export type ToolRunMetadata = {
  tool_call_id?: string;
  tool?: string;
  arguments?: string;
  result?: ToolRunResult | null;
};

export type PersistedPlanStatus = "pending" | "approved" | "executed" | "superseded";
export type PlanStatus = PersistedPlanStatus | "approving" | "executing" | "error";
export type SendMode = "execute" | "plan";
export type ToolRunStatus = "running" | "completed" | "failed";

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
