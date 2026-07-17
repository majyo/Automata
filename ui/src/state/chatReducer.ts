import type {
  ChatMessage,
  PersistedRunStatus,
  PlanStatus,
  ToolApprovalRequest,
} from "../types/chat";
import type { SocketPayload } from "../types/socket";

export type RunClientState = {
  runId: string;
  sessionId: string;
  status: PersistedRunStatus;
  lastSequence: number;
  isReplaying: boolean;
};

export type ChatState = {
  messagesBySession: Record<string, ChatMessage[]>;
  runsById: Record<string, RunClientState>;
  activeRunIdBySession: Record<string, string | undefined>;
  approvalsByRun: Record<string, ToolApprovalRequest[]>;
};

type ToolCallPayload = Extract<SocketPayload, { type: "tool_call" }>;
type ToolResultPayload = Extract<SocketPayload, { type: "tool_result" }>;
type PlanReadyPayload = Extract<SocketPayload, { type: "plan_ready" }>;

export type ChatAction =
  | {
      type: "messagesLoaded";
      sessionId: string;
      messages: ChatMessage[];
      preserveTransient?: boolean;
    }
  | { type: "sessionMessagesCleared"; sessionId: string }
  | { type: "userMessageQueued"; message: ChatMessage }
  | { type: "agentMessageQueued"; message: ChatMessage }
  | { type: "tokenReceived"; messageId: string; sessionId: string; content: string }
  | { type: "planReady"; messageId: string; payload: PlanReadyPayload }
  | { type: "planStatusChanged"; sessionId: string; planId: string; status: PlanStatus }
  | { type: "currentPlanError"; sessionId: string; planId?: string | null }
  | { type: "runEventAppended"; sessionId: string; text: string; id: string }
  | {
      type: "toolCallStarted";
      sessionId: string;
      payload: ToolCallPayload;
      messageId: string;
      toolCallId: string;
    }
  | {
      type: "toolCallCompleted";
      sessionId: string;
      payload: ToolResultPayload;
      messageId: string;
      toolCallId: string;
    }
  | { type: "streamingFailed"; messageId?: string | null; sessionId: string; errorText: string }
  | {
      type: "runDiscovered";
      runId: string;
      sessionId: string;
      status: PersistedRunStatus;
      lastSequence: number;
    }
  | { type: "runStarted"; runId: string; sessionId: string; sequence: number }
  | {
      type: "runSequenceAdvanced";
      runId: string;
      sessionId: string;
      sequence: number;
    }
  | { type: "runReplayChanged"; runId: string; replaying: boolean }
  | {
      type: "runStatusChanged";
      runId: string;
      sessionId: string;
      status: PersistedRunStatus;
    }
  | { type: "approvalRequired"; approval: ToolApprovalRequest }
  | { type: "approvalResolved"; runId: string; approvalId: string }
  | {
      type: "runFinished";
      runId: string;
      sessionId: string;
      status: Extract<PersistedRunStatus, "completed" | "failed" | "cancelled" | "interrupted">;
      sequence?: number;
    };

export const initialChatState: ChatState = {
  messagesBySession: {},
  runsById: {},
  activeRunIdBySession: {},
  approvalsByRun: {},
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.type === "messagesLoaded") {
    const transient = action.preserveTransient
      ? (state.messagesBySession[action.sessionId] ?? []).filter(
          (message) => message.id.includes(":") && message.sequence === undefined,
        )
      : [];
    const persistedIds = new Set(action.messages.map((message) => message.id));
    return {
      ...state,
      messagesBySession: {
        ...state.messagesBySession,
        [action.sessionId]: [
          ...action.messages,
          ...transient.filter((message) => !persistedIds.has(message.id)),
        ],
      },
    };
  }

  if (action.type === "sessionMessagesCleared") {
    const messagesBySession = { ...state.messagesBySession };
    delete messagesBySession[action.sessionId];
    return { ...state, messagesBySession };
  }

  if (action.type === "runDiscovered") {
    const run = state.runsById[action.runId];
    return {
      ...state,
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
      activeRunIdBySession: isTerminal(action.status)
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
      return state;
    }
    return {
      ...state,
      runsById: {
        ...state.runsById,
        [action.runId]: { ...current, isReplaying: action.replaying },
      },
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
    const approvals = state.approvalsByRun[action.approval.run_id] ?? [];
    return {
      ...upsertRunStatus(
        state,
        action.approval.run_id,
        action.approval.session_id,
        "waiting_approval",
      ),
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
    const approvalsByRun = {
      ...state.approvalsByRun,
      [action.runId]: approvals.filter((item) => item.approval_id !== action.approvalId),
    };
    const current = state.runsById[action.runId];
    return {
      ...(current
        ? upsertRunStatus(state, action.runId, current.sessionId, "running")
        : state),
      approvalsByRun,
    };
  }

  if (action.type === "runFinished") {
    const current = state.runsById[action.runId];
    const activeRunIdBySession = { ...state.activeRunIdBySession };
    if (activeRunIdBySession[action.sessionId] === action.runId) {
      delete activeRunIdBySession[action.sessionId];
    }
    const approvalsByRun = { ...state.approvalsByRun };
    delete approvalsByRun[action.runId];
    return {
      ...state,
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
      approvalsByRun,
    };
  }

  if (action.type === "userMessageQueued" || action.type === "agentMessageQueued") {
    if (!action.message.session_id) {
      return state;
    }
    return appendMessage(state, action.message.session_id, action.message);
  }

  if (action.type === "tokenReceived") {
    if (!action.content) {
      return state;
    }
    return updateSessionMessages(state, action.sessionId, (messages) => {
      const hasMessage = messages.some((message) => message.id === action.messageId);
      if (!hasMessage) {
        return [
          ...messages,
          {
            id: action.messageId,
            session_id: action.sessionId,
            role: "agent",
            text: action.content,
          },
        ];
      }
      return messages.map((message) =>
        message.id === action.messageId
          ? { ...message, text: `${message.text}${action.content}` }
          : message,
      );
    });
  }

  if (action.type === "planReady") {
    const { payload, messageId } = action;
    const nextMessage: ChatMessage = {
      id: messageId,
      session_id: payload.session_id,
      role: "agent",
      text: payload.content,
      kind: "plan",
      plan_id: payload.plan_id,
      plan_status: "pending",
    };
    return updateSessionMessages(state, payload.session_id, (messages) =>
      messages.some((message) => message.id === messageId)
        ? messages.map((message) =>
            message.id === messageId ? { ...message, ...nextMessage } : message,
          )
        : [...messages, nextMessage],
    );
  }

  if (action.type === "planStatusChanged") {
    return updateSessionMessages(state, action.sessionId, (messages) =>
      messages.map((message) =>
        message.plan_id === action.planId
          ? { ...message, plan_status: action.status }
          : message,
      ),
    );
  }

  if (action.type === "currentPlanError") {
    return updateSessionMessages(state, action.sessionId, (messages) =>
      messages.map((message) => {
        if (action.planId && message.plan_id === action.planId) {
          return { ...message, plan_status: "failed" };
        }
        if (
          !action.planId &&
          message.kind === "plan" &&
          (message.plan_status === "pending" ||
            message.plan_status === "approving" ||
            message.plan_status === "executing")
        ) {
          return { ...message, plan_status: "failed" };
        }
        return message;
      }),
    );
  }

  if (action.type === "runEventAppended") {
    return appendMessage(state, action.sessionId, {
      id: action.id,
      session_id: action.sessionId,
      role: "tool",
      text: action.text,
    });
  }

  if (action.type === "toolCallStarted") {
    return updateSessionMessages(state, action.sessionId, (messages) => {
      if (messages.some((message) => message.id === action.messageId)) {
        return messages;
      }
      return [
        ...messages,
        {
          id: action.messageId,
          session_id: action.sessionId,
          role: "tool",
          text: "",
          kind: "tool_run",
          metadata: {
            tool_call_id: action.toolCallId,
            tool: action.payload.tool ?? "unknown_tool",
            arguments: action.payload.arguments ?? "{}",
            result: null,
          },
        },
      ];
    });
  }

  if (action.type === "toolCallCompleted") {
    const result = {
      success: action.payload.success !== false,
      content: action.payload.content ?? "",
    };
    return updateSessionMessages(state, action.sessionId, (messages) => {
      const hasMessage = messages.some((message) => message.id === action.messageId);
      if (!hasMessage) {
        return [
          ...messages,
          {
            id: action.messageId,
            session_id: action.sessionId,
            role: "tool",
            text: "",
            kind: "tool_run",
            metadata: {
              tool_call_id: action.toolCallId,
              tool: action.payload.tool ?? "unknown_tool",
              arguments: "{}",
              result,
            },
          },
        ];
      }
      return messages.map((message) =>
        message.id === action.messageId
          ? {
              ...message,
              metadata: {
                ...(message.metadata ?? {}),
                tool_call_id: action.toolCallId,
                tool:
                  message.metadata?.tool ??
                  action.payload.tool ??
                  "unknown_tool",
                arguments: message.metadata?.arguments ?? "{}",
                result,
              },
            }
          : message,
      );
    });
  }

  if (action.type === "streamingFailed") {
    if (!action.messageId) {
      return state;
    }
    const messageId = action.messageId;
    return updateSessionMessages(state, action.sessionId, (messages) => {
      const hasMessage = messages.some((message) => message.id === messageId);
      if (!hasMessage) {
        return [
          ...messages,
          {
            id: messageId,
            session_id: action.sessionId,
            role: "agent",
            text: action.errorText,
          },
        ];
      }
      return messages.map((message) =>
        message.id === messageId && !message.text.trim()
          ? { ...message, text: action.errorText }
          : message,
      );
    });
  }

  return state;
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

function appendMessage(
  state: ChatState,
  sessionId: string,
  message: ChatMessage,
): ChatState {
  return updateSessionMessages(state, sessionId, (messages) => [...messages, message]);
}

function updateSessionMessages(
  state: ChatState,
  sessionId: string,
  update: (messages: ChatMessage[]) => ChatMessage[],
): ChatState {
  return {
    ...state,
    messagesBySession: {
      ...state.messagesBySession,
      [sessionId]: update(state.messagesBySession[sessionId] ?? []),
    },
  };
}

function upsertRun(state: ChatState, run: RunClientState): ChatState {
  const activeRunIdBySession = { ...state.activeRunIdBySession };
  if (isTerminal(run.status)) {
    if (activeRunIdBySession[run.sessionId] === run.runId) {
      delete activeRunIdBySession[run.sessionId];
    }
  } else {
    activeRunIdBySession[run.sessionId] = run.runId;
  }
  return {
    ...state,
    runsById: { ...state.runsById, [run.runId]: run },
    activeRunIdBySession,
  };
}

function upsertRunStatus(
  state: ChatState,
  runId: string,
  sessionId: string,
  status: PersistedRunStatus,
): ChatState {
  const current = state.runsById[runId];
  return upsertRun(state, {
    runId,
    sessionId,
    status,
    lastSequence: current?.lastSequence ?? 0,
    isReplaying: current?.isReplaying ?? false,
  });
}

function isTerminal(status: PersistedRunStatus): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(status);
}
