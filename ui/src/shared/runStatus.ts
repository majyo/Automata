import type { PersistedRunStatus } from "../types/chat";

const TERMINAL_RUN_STATUSES: PersistedRunStatus[] = [
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

/**
 * Whether a Run will never emit another event.
 *
 * This is shared vocabulary rather than a Runs-feature detail: the
 * conversation slice also needs it, because a session reload must not delete
 * what a still-running Run is streaming.
 */
export function isTerminalRunStatus(status: PersistedRunStatus): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}
