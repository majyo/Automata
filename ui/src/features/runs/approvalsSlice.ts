import type { ChatAction, ChatState } from "../../state/chatTypes";

export type ApprovalsSliceState = Pick<ChatState, "approvalsByRun">;
export type ApprovalsSliceUpdate = Pick<ChatState, "approvalsByRun">;

/**
 * Pure update of pending tool approvals, keyed by run. Terminal runs drop
 * their approvals here; the run status itself belongs to runsSlice.
 */
export function reduceApprovals(
  state: ApprovalsSliceState,
  action: ChatAction,
): ApprovalsSliceUpdate | null {
  if (action.type === "approvalRequired") {
    const approvals = state.approvalsByRun[action.approval.run_id] ?? [];
    return {
      approvalsByRun: {
        ...state.approvalsByRun,
        [action.approval.run_id]: [
          ...approvals.filter((item) => item.approval_id !== action.approval.approval_id),
          action.approval,
        ],
      },
    };
  }

  if (action.type === "approvalResolved") {
    const approvals = state.approvalsByRun[action.runId] ?? [];
    return {
      approvalsByRun: {
        ...state.approvalsByRun,
        [action.runId]: approvals.filter((item) => item.approval_id !== action.approvalId),
      },
    };
  }

  if (action.type === "runFinished") {
    const approvalsByRun = { ...state.approvalsByRun };
    delete approvalsByRun[action.runId];
    return { approvalsByRun };
  }

  return null;
}
