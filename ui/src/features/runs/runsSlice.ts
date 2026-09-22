import type { ChatAction, ChatState, RunClientState } from "../../state/chatTypes";
import type { PersistedRunStatus } from "../../types/chat";
import { isTerminalRunStatus } from "../../shared/runStatus";

export type RunsSliceState = Pick<ChatState, "runsById" | "activeRunIdBySession">;
export type RunsSliceUpdate = Pick<ChatState, "runsById" | "activeRunIdBySession">;

/**
 * Pure update of run lifecycle state: discovery, sequence projection, replay
 * flag, status transitions and terminal states. Approvals are stored by
 * features/runs/approvalsSlice; this slice only reflects their run status.
 */
export function reduceRuns(
  state: RunsSliceState,
  action: ChatAction,
): RunsSliceUpdate | null {
  if (action.type === "runDiscovered") {
    const run = state.runsById[action.runId];
    return {
      runsById: {
        ...state.runsById,
        [action.runId]: {
          runId: action.runId,
          sessionId: action.sessionId,
          status: action.status,
          lastSequence: Math.max(run?.lastSequence ?? 0, action.lastSequence),
          isReplaying: run?.isReplaying ?? false,
        },
      },
      activeRunIdBySession: isTerminalRunStatus(action.status)
        ? state.activeRunIdBySession
        : {
            ...state.activeRunIdBySession,
            [action.sessionId]: action.runId,
          },
    };
  }

  if (action.type === "runStarted") {
    return upsertRun(state, {
      runId: action.runId,
      sessionId: action.sessionId,
      status: "running",
      lastSequence: action.sequence,
      isReplaying: state.runsById[action.runId]?.isReplaying ?? false,
    });
  }

  if (action.type === "runSequenceAdvanced") {
    const current = state.runsById[action.runId];
    return upsertRun(state, {
      runId: action.runId,
      sessionId: action.sessionId,
      status: current?.status ?? "running",
      lastSequence: Math.max(current?.lastSequence ?? 0, action.sequence),
      isReplaying: current?.isReplaying ?? false,
    });
  }

  if (action.type === "runReplayChanged") {
    const current = state.runsById[action.runId];
    if (!current) {
      return null;
    }
    return {
      runsById: {
        ...state.runsById,
        [action.runId]: { ...current, isReplaying: action.replaying },
      },
      activeRunIdBySession: state.activeRunIdBySession,
    };
  }

  if (action.type === "runStatusChanged") {
    const current = state.runsById[action.runId];
    return upsertRun(state, {
      runId: action.runId,
      sessionId: action.sessionId,
      status: action.status,
      lastSequence: current?.lastSequence ?? 0,
      isReplaying: current?.isReplaying ?? false,
    });
  }

  if (action.type === "approvalRequired") {
    return upsertRunStatus(
      state,
      action.approval.run_id,
      action.approval.session_id,
      "waiting_approval",
    );
  }

  if (action.type === "approvalResolved") {
    const current = state.runsById[action.runId];
    if (!current) {
      return null;
    }
    return upsertRunStatus(state, action.runId, current.sessionId, "running");
  }

  if (action.type === "runFinished") {
    const current = state.runsById[action.runId];
    const activeRunIdBySession = { ...state.activeRunIdBySession };
    if (activeRunIdBySession[action.sessionId] === action.runId) {
      delete activeRunIdBySession[action.sessionId];
    }
    return {
      runsById: {
        ...state.runsById,
        [action.runId]: {
          runId: action.runId,
          sessionId: action.sessionId,
          status: action.status,
          lastSequence: Math.max(
            current?.lastSequence ?? 0,
            action.sequence ?? 0,
          ),
          isReplaying: false,
        },
      },
      activeRunIdBySession,
    };
  }

  return null;
}

function upsertRun(state: RunsSliceState, run: RunClientState): RunsSliceUpdate {
  const activeRunIdBySession = { ...state.activeRunIdBySession };
  if (isTerminalRunStatus(run.status)) {
    if (activeRunIdBySession[run.sessionId] === run.runId) {
      delete activeRunIdBySession[run.sessionId];
    }
  } else {
    activeRunIdBySession[run.sessionId] = run.runId;
  }
  return {
    runsById: { ...state.runsById, [run.runId]: run },
    activeRunIdBySession,
  };
}

function upsertRunStatus(
  state: RunsSliceState,
  runId: string,
  sessionId: string,
  status: PersistedRunStatus,
): RunsSliceUpdate {
  const current = state.runsById[runId];
  return upsertRun(state, {
    runId,
    sessionId,
    status,
    lastSequence: current?.lastSequence ?? 0,
    isReplaying: current?.isReplaying ?? false,
  });
}
