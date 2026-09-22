import type { ChatAction, ChatState, RunClientState } from "../../state/chatTypes";
import { isTerminalRunStatus } from "../../shared/runStatus";
import type { ChatMessage } from "../../types/chat";

export type MessagesSliceState = Pick<ChatState, "messagesBySession"> &
  Partial<Pick<ChatState, "runsById">>;
export type MessagesSliceUpdate = Pick<ChatState, "messagesBySession">;

/**
 * Pure update of the per-session message list. Returns null when the action
 * does not touch messages so the composing reducer can keep the previous state.
 *
 * Plan message updates live in features/runs/plansSlice; this slice owns plain
 * conversation messages, tool cards and streaming text.
 *
 * A session load merges the persisted history with whatever is still being
 * streamed: a Run that has not reached a terminal status keeps the messages it
 * is still producing, and everything else is dropped because the history now
 * owns it. That is what lets a queued Run stream while the load triggered by
 * the Run it followed is still in flight.
 */
export function reduceMessages(
  state: MessagesSliceState,
  action: ChatAction,
): MessagesSliceUpdate | null {
  if (action.type === "messagesLoaded") {
    const persistedIds = new Set(action.messages.map((message) => message.id));
    const persistedInputIds = new Set(
      action.messages
        .map((message) => message.metadata?.input_id)
        .filter((inputId): inputId is string => Boolean(inputId)),
    );
    const streaming = (state.messagesBySession[action.sessionId] ?? []).filter(
      (message) =>
        isStreamedMessage(message) &&
        !persistedIds.has(message.id) &&
        !(
          message.metadata?.input_id &&
          persistedInputIds.has(message.metadata.input_id)
        ) &&
        belongsToLiveRun(message.id, state.runsById ?? {}),
    );
    return {
      messagesBySession: {
        ...state.messagesBySession,
        [action.sessionId]: [...action.messages, ...streaming],
      },
    };
  }

  if (action.type === "sessionMessagesCleared") {
    const messagesBySession = { ...state.messagesBySession };
    delete messagesBySession[action.sessionId];
    return { messagesBySession };
  }

  if (action.type === "userMessageQueued" || action.type === "agentMessageQueued") {
    if (!action.message.session_id) {
      return null;
    }
    return appendMessage(state, action.message.session_id, action.message);
  }
  if (action.type === "tokenReceived") {
    if (!action.content) {
      return null;
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

  if (action.type === "toolOutputReceived") {
    return updateSessionMessages(state, action.sessionId, (messages) => {
      const current = messages.find((message) => message.id === action.messageId);
      const previousOutput = current?.metadata?.live_output ?? {
        stdout: "",
        stderr: "",
      };
      const liveOutput = {
        ...previousOutput,
        [action.payload.stream]: appendLiveOutput(
          previousOutput[action.payload.stream],
          action.payload.content,
        ),
        truncated:
          previousOutput.truncated === true ||
          action.payload.truncated === true,
      };

      if (!current) {
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
              result: null,
              live_output: liveOutput,
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
                live_output: liveOutput,
              },
            }
          : message,
      );
    });
  }

  if (action.type === "streamingFailed") {
    if (!action.messageId) {
      return null;
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

  return null;
}

function appendMessage(
  state: MessagesSliceState,
  sessionId: string,
  message: ChatMessage,
): MessagesSliceUpdate {
  // An input message is identified by the input it came from, not by the
  // bubble id: the backend persists the queued prompt under its own message id,
  // so a reload can arrive before the Run start that would add the bubble.
  const inputId = message.metadata?.input_id;
  return updateSessionMessages(state, sessionId, (messages) =>
    messages.some(
      (existing) =>
        existing.id === message.id ||
        (inputId !== undefined && existing.metadata?.input_id === inputId),
    )
      ? messages
      : [...messages, message],
  );
}

/** A message the client is producing; the backend has not persisted it yet. */
function isStreamedMessage(message: ChatMessage): boolean {
  return message.sequence === undefined && message.id.includes(":");
}

/**
 * Whether a streamed message id (`<runId>:<kind>:<local id>`) belongs to a Run
 * that is still going, so a reloading history must not delete it.
 */
function belongsToLiveRun(
  id: string,
  runs: Record<string, RunClientState>,
): boolean {
  const separator = id.indexOf(":");
  if (separator <= 0) {
    return false;
  }
  const run = runs[id.slice(0, separator)];
  return run !== undefined && !isTerminalRunStatus(run.status);
}

const LIVE_OUTPUT_MAX_CHARS = 64 * 1024;
const LIVE_OUTPUT_TRUNCATION_MARKER = "\n... live output truncated ...\n";

function appendLiveOutput(previous: string, content: string): string {
  const combined = `${previous}${content}`;
  if (combined.length <= LIVE_OUTPUT_MAX_CHARS) {
    return combined;
  }
  const available = LIVE_OUTPUT_MAX_CHARS - LIVE_OUTPUT_TRUNCATION_MARKER.length;
  const headLength = Math.ceil(available / 2);
  const tailLength = Math.floor(available / 2);
  return `${combined.slice(0, headLength)}${LIVE_OUTPUT_TRUNCATION_MARKER}${combined.slice(-tailLength)}`;
}

function updateSessionMessages(
  state: MessagesSliceState,
  sessionId: string,
  update: (messages: ChatMessage[]) => ChatMessage[],
): MessagesSliceUpdate {
  return {
    messagesBySession: {
      ...state.messagesBySession,
      [sessionId]: update(state.messagesBySession[sessionId] ?? []),
    },
  };
}
