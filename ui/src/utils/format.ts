import type { PlanStatus, ToolRunStatus } from "../types/chat";
import type { SocketPayload } from "../types/socket";

export const WORKING_DIRECTORY_PLACEHOLDER = "默认工作区";

export function formatPlanStatus(status?: PlanStatus): string {
  if (status === "approving") {
    return "批准中";
  }
  if (status === "executing") {
    return "执行中";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "executed") {
    return "已执行";
  }
  if (status === "superseded") {
    return "已被替代";
  }
  return "待批准";
}

export function formatToolRunStatus(status: ToolRunStatus): string {
  if (status === "failed") {
    return "失败";
  }
  if (status === "completed") {
    return "已完成";
  }
  return "运行中";
}

export function formatContextCompressed(payload: Extract<SocketPayload, { type: "context_compressed" }>): string {
  const scope = payload.scope === "loop" ? "工具上下文" : "会话上下文";
  const compressed = typeof payload.compressed_messages === "number" ? `${payload.compressed_messages} 条消息` : "上下文";
  return `上下文已压缩：${scope}\n已压缩 ${compressed}。`;
}

export function formatDirectoryName(path?: string): string {
  const value = path?.trim() || WORKING_DIRECTORY_PLACEHOLDER;
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? value;
}
