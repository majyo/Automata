import type { PersistedRunStatus } from "../../types/chat";

const TERMINAL_RUN_STATUSES: PersistedRunStatus[] = [
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

export function isTerminalRunStatus(status: PersistedRunStatus): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}
