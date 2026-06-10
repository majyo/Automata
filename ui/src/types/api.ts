import type { PersistedPlanStatus, ToolRunMetadata } from "./chat";

export type ApiRuntimeConfig = {
  httpBaseUrl: string;
  wsChatUrl: string;
  defaultWorkingDirectory: string;
};

export type ApiMessage = {
  id: string;
  session_id: string;
  role: "user" | "agent" | "tool";
  kind?: "message" | "tool_run";
  content: string;
  metadata?: ToolRunMetadata | null;
  sequence: number;
  created_at: string;
  plan_id?: string | null;
  plan_status?: PersistedPlanStatus | null;
};
