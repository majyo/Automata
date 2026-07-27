import { requestJson } from "./client";
import type { ApiMessage, ApiRuntimeConfig } from "../types/api";
import type { ChatMessage } from "../types/chat";
import type { PermissionPreset, SessionSummary } from "../types/session";

export async function fetchSessions(config: ApiRuntimeConfig): Promise<SessionSummary[]> {
  return requestJson<SessionSummary[]>(config, "/sessions");
}

export async function createSession(
  config: ApiRuntimeConfig,
  title: string,
  workingDirectory?: string,
  backend?: string,
  permissionPreset: PermissionPreset = "default",
): Promise<SessionSummary> {
  return requestJson<SessionSummary>(config, "/sessions", {
    method: "POST",
    body: JSON.stringify({
      title,
      working_directory: workingDirectory?.trim() || undefined,
      backend: backend?.trim() || undefined,
      permission_preset: permissionPreset,
    }),
  });
}

export async function updateSession(
  config: ApiRuntimeConfig,
  sessionId: string,
  updates: { title?: string; permission_preset?: PermissionPreset },
): Promise<SessionSummary> {
  return requestJson<SessionSummary>(config, `/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function deleteSession(config: ApiRuntimeConfig, sessionId: string): Promise<void> {
  await requestJson<unknown>(config, `/sessions/${sessionId}`, { method: "DELETE" });
}

export async function fetchMessages(config: ApiRuntimeConfig, sessionId: string): Promise<ChatMessage[]> {
  const messages = await requestJson<ApiMessage[]>(config, `/sessions/${sessionId}/messages`);
  return messages.map((message) => ({
    id: message.id,
    session_id: message.session_id,
    role: message.role,
    text: message.content,
    kind: message.kind === "tool_run" ? "tool_run" : message.plan_id ? "plan" : "normal",
    metadata: message.metadata ?? null,
    plan_id: message.plan_id ?? undefined,
    plan_status: message.plan_status ?? undefined,
    sequence: message.sequence,
    created_at: message.created_at,
  }));
}
