import type { ChatMessage, PlanStatus, RunStatus, ToolApprovalRequest } from "../types/chat";
import type { SocketPayload } from "../types/socket";

export type ChatState = {
  messages: ChatMessage[];
  activeRunId: string | null;
  runStatus: RunStatus;
  approvals: ToolApprovalRequest[];
};

type ToolCallPayload = Extract<SocketPayload, { type: "tool_call" }>;
type ToolResultPayload = Extract<SocketPayload, { type: "tool_result" }>;
type PlanReadyPayload = Extract<SocketPayload, { type: "plan_ready" }>;

export type ChatAction =
  | { type: "messagesLoaded"; messages: ChatMessage[] }
  | { type: "messagesCleared" }
  | { type: "userMessageQueued"; message: ChatMessage }
  | { type: "agentMessageQueued"; message: ChatMessage }
  | { type: "tokenReceived"; messageId: string; sessionId?: string; content: string }
  | { type: "planReady"; messageId: string; payload: PlanReadyPayload }
  | { type: "planStatusChanged"; planId: string; status: PlanStatus }
  | { type: "currentPlanError"; planId?: string | null }
  | { type: "runEventAppended"; sessionId?: string; text: string; id: string }
  | { type: "toolCallStarted"; sessionId?: string; payload: ToolCallPayload; messageId: string; toolCallId: string }
  | { type: "toolCallCompleted"; sessionId?: string; payload: ToolResultPayload; messageId: string; toolCallId: string }
  | { type: "streamingFailed"; messageId?: string | null; sessionId?: string | null; errorText: string }
  | { type: "runStarted"; runId: string }
  | { type: "approvalRequired"; approval: ToolApprovalRequest }
  | { type: "approvalResolved"; approvalId: string }
  | { type: "runCancelRequested"; runId: string }
  | { type: "runFinished"; runId?: string };

export const initialChatState: ChatState = {
  messages: [],
  activeRunId: null,
  runStatus: "idle",
  approvals: [],
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.type === "runStarted") {
    return { ...state, activeRunId: action.runId, runStatus: "running", approvals: [] };
  }

  if (action.type === "approvalRequired") {
    return {
      ...state,
      runStatus: "waiting_approval",
      approvals: [...state.approvals.filter((item) => item.approval_id !== action.approval.approval_id), action.approval],
    };
  }

  if (action.type === "approvalResolved") {
    return {
      ...state,
      runStatus: "running",
      approvals: state.approvals.filter((item) => item.approval_id !== action.approvalId),
    };
  }

  if (action.type === "runCancelRequested") {
    if (state.activeRunId !== action.runId) {
      return state;
    }
    return { ...state, runStatus: "cancelling", approvals: [] };
  }

  if (action.type === "runFinished") {
    if (action.runId && state.activeRunId && action.runId !== state.activeRunId) {
      return state;
    }
    return { ...state, activeRunId: null, runStatus: "idle", approvals: [] };
  }

  if (action.type === "messagesLoaded") {
    return { ...state, messages: action.messages };
  }

  if (action.type === "messagesCleared") {
    return { ...state, messages: [] };
  }

  if (action.type === "userMessageQueued" || action.type === "agentMessageQueued") {
    return { ...state, messages: [...state.messages, action.message] };
  }

  if (action.type === "tokenReceived") {
    if (!action.content) {
      return state;
    }

    const hasMessage = state.messages.some((message) => message.id === action.messageId);
    if (!hasMessage) {
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: action.messageId,
            session_id: action.sessionId,
            role: "agent",
            text: action.content,
          },
        ],
      };
    }

    return {
      ...state,
      messages: state.messages.map((message) =>
        message.id === action.messageId ? { ...message, text: `${message.text}${action.content}` } : message,
      ),
    };
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

    return state.messages.some((message) => message.id === messageId)
      ? {
          ...state,
          messages: state.messages.map((message) => (message.id === messageId ? { ...message, ...nextMessage } : message)),
        }
      : { ...state, messages: [...state.messages, nextMessage] };
  }

  if (action.type === "planStatusChanged") {
    return {
      ...state,
      messages: state.messages.map((message) =>
        message.plan_id === action.planId ? { ...message, plan_status: action.status } : message,
      ),
    };
  }

  if (action.type === "currentPlanError") {
    if (action.planId) {
      return chatReducer(state, { type: "planStatusChanged", planId: action.planId, status: "error" });
    }

    return {
      ...state,
      messages: state.messages.map((message) =>
        message.kind === "plan" && (message.plan_status === "pending" || message.plan_status === "approving")
          ? { ...message, plan_status: "error" }
          : message,
      ),
    };
  }

  if (action.type === "runEventAppended") {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: action.id,
          session_id: action.sessionId,
          role: "tool",
          text: action.text,
        },
      ],
    };
  }

  if (action.type === "toolCallStarted") {
    return {
      ...state,
      messages: [
        ...state.messages,
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
      ],
    };
  }

  if (action.type === "toolCallCompleted") {
    const result = {
      success: action.payload.success !== false,
      content: action.payload.content ?? "",
    };

    const hasMessage = state.messages.some((message) => message.id === action.messageId);
    if (!hasMessage) {
      return {
        ...state,
        messages: [
          ...state.messages,
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
        ],
      };
    }

    return {
      ...state,
      messages: state.messages.map((message) =>
        message.id === action.messageId
          ? {
              ...message,
              metadata: {
                ...(message.metadata ?? {}),
                tool_call_id: action.toolCallId,
                tool: message.metadata?.tool ?? action.payload.tool ?? "unknown_tool",
                arguments: message.metadata?.arguments ?? "{}",
                result,
              },
            }
          : message,
      ),
    };
  }

  if (action.type === "streamingFailed") {
    if (!action.messageId) {
      return state;
    }

    const hasMessage = state.messages.some((message) => message.id === action.messageId);
    if (!hasMessage) {
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: action.messageId,
            session_id: action.sessionId ?? undefined,
            role: "agent",
            text: action.errorText,
          },
        ],
      };
    }

    return {
      ...state,
      messages: state.messages.map((message) =>
        message.id === action.messageId && !message.text.trim() ? { ...message, text: action.errorText } : message,
      ),
    };
  }

  return state;
}
