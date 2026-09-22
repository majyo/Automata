import { reduceMessages } from "../features/conversation/messagesSlice";
import { reduceInputs } from "../features/runs/inputsSlice";
import { reduceApprovals } from "../features/runs/approvalsSlice";
import { reducePlans } from "../features/runs/plansSlice";
import { reduceRuns } from "../features/runs/runsSlice";
import { initialChatState } from "./chatTypes";
import type { ChatAction, ChatState, RunClientState } from "./chatTypes";
import type { ChatMessage, PendingInput, ToolApprovalRequest } from "../types/chat";

export { initialChatState };
export type { ChatAction, ChatState, RunClientState };

/**
 * Composition root for chat state. Each feature slice owns one part of the
 * state and returns null when the action is not its concern; one accepted
 * backend event still produces exactly one coordinated state update.
 */
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  const messages = reduceMessages(state, action);
  const plans = reducePlans(state, action);
  const runs = reduceRuns(state, action);
  const approvals = reduceApprovals(state, action);
  const inputs = reduceInputs(state, action);

  if (!messages && !plans && !runs && !approvals && !inputs) {
    return state;
  }

  return {
    ...state,
    ...(messages ?? {}),
    ...(plans ?? {}),
    ...(runs ?? {}),
    ...(approvals ?? {}),
    ...(inputs ?? {}),
  };
}

export function selectMessages(
  state: ChatState,
  sessionId: string | null,
): ChatMessage[] {
  return sessionId ? state.messagesBySession[sessionId] ?? [] : [];
}

export function selectActiveRun(
  state: ChatState,
  sessionId: string | null,
): RunClientState | null {
  if (!sessionId) {
    return null;
  }
  const runId = state.activeRunIdBySession[sessionId];
  return runId ? state.runsById[runId] ?? null : null;
}

export function selectSessionApprovals(
  state: ChatState,
  sessionId: string | null,
): ToolApprovalRequest[] {
  const run = selectActiveRun(state, sessionId);
  return run ? state.approvalsByRun[run.runId] ?? [] : [];
}

/** Inputs submitted for this session that the backend has not delivered yet. */
export function selectPendingInputs(
  state: ChatState,
  sessionId: string | null,
): PendingInput[] {
  return sessionId ? state.inputsBySession[sessionId] ?? [] : [];
}
