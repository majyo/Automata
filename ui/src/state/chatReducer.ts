import type { ChatMessage, PlanStatus } from "../types/chat";
import type { SocketPayload } from "../types/socket";

export type ChatState = {
  messages: ChatMessage[];
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
  | { type: "streamingFailed"; messageId?: string | null; sessionId?: string | null; errorText: string };

export const initialChatState: ChatState = {
  messages: [],
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.type === "messagesLoaded") {
    return { messages: action.messages };
  }

  if (action.type === "messagesCleared") {
    return { messages: [] };
  }

  if (action.type === "userMessageQueued" || action.type === "agentMessageQueued") {
    return { messages: [...state.messages, action.message] };
  }

  if (action.type === "tokenReceived") {
    if (!action.content) {
      return state;
    }

    const hasMessage = state.messages.some((message) => message.id === action.messageId);
    if (!hasMessage) {
      return {
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
          messages: state.messages.map((message) => (message.id === messageId ? { ...message, ...nextMessage } : message)),
        }
      : { messages: [...state.messages, nextMessage] };
  }

  if (action.type === "planStatusChanged") {
    return {
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
      messages: state.messages.map((message) =>
        message.kind === "plan" && (message.plan_status === "pending" || message.plan_status === "approving")
          ? { ...message, plan_status: "error" }
          : message,
      ),
    };
  }

  if (action.type === "runEventAppended") {
    return {
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
      messages: state.messages.map((message) =>
        message.id === action.messageId && !message.text.trim() ? { ...message, text: action.errorText } : message,
      ),
    };
  }

  return state;
}
