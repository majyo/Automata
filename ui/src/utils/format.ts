import type { PlanStatus, ToolRunStatus } from "../types/chat";
import type { SocketPayload } from "../types/socket";

export const WORKING_DIRECTORY_PLACEHOLDER = "Backend default workspace";

export function formatPlanStatus(status?: PlanStatus): string {
  if (status === "approving") {
    return "Approving";
  }
  if (status === "approved") {
    return "Approved";
  }
  if (status === "executing") {
    return "Executing";
  }
  if (status === "executed") {
    return "Executed";
  }
  if (status === "superseded") {
    return "Superseded";
  }
  if (status === "error") {
    return "Error";
  }
  return "Pending";
}

export function formatToolRunStatus(status: ToolRunStatus): string {
  if (status === "failed") {
    return "Failed";
  }
  if (status === "completed") {
    return "Completed";
  }
  return "Running";
}

export function formatContextCompressed(payload: Extract<SocketPayload, { type: "context_compressed" }>): string {
  const scope = payload.scope === "loop" ? "tool context" : "session context";
  const compressed = typeof payload.compressed_messages === "number" ? `${payload.compressed_messages} messages` : "context";
  return `Context compressed: ${scope}\nCompressed ${compressed}.`;
}

export function formatDirectoryName(path?: string): string {
  const value = path?.trim() || WORKING_DIRECTORY_PLACEHOLDER;
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? value;
}
