import type { PersistedPlanStatus, ToolRunMetadata } from "./chat";
import type { PersistedRunStatus } from "./chat";
import type { PermissionPreset } from "./session";

export type ApiRuntimeConfig = {
  httpBaseUrl: string;
  wsChatUrl: string;
  defaultWorkingDirectory: string;
  apiToken: string;
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

export type ApiRun = {
  id: string;
  session_id: string;
  kind: "chat_act" | "chat_plan" | "plan_execution";
  mode: "act" | "plan";
  permission_preset: PermissionPreset;
  permission_profile_version?: number | null;
  permission_profile?: Record<string, unknown> | null;
  sandbox_backend?: string | null;
  status: PersistedRunStatus;
  request_message_id?: string | null;
  response_message_id?: string | null;
  plan_id?: string | null;
  last_sequence: number;
  error_code?: string | null;
  public_error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};
