import type {
  ChatMessage,
  InputDelivery,
  PendingInput,
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
  inputsBySession: Record<string, PendingInput[]>;
};

type ToolCallPayload = Extract<SocketPayload, { type: "tool_call" }>;
type ToolOutputPayload = Extract<SocketPayload, { type: "tool_output_delta" }>;
type ToolResultPayload = Extract<SocketPayload, { type: "tool_result" }>;
type PlanReadyPayload = Extract<SocketPayload, { type: "plan_ready" }>;

export type ChatAction =
  | {
      type: "messagesLoaded";
      sessionId: string;
      messages: ChatMessage[];
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
  | {
      type: "toolOutputReceived";
      sessionId: string;
      payload: ToolOutputPayload;
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
    }
  | {
      type: "inputSubmitted";
      sessionId: string;
      requestId: string;
      prompt: string;
      delivery: Exclude<InputDelivery, "new">;
      runId?: string;
    }
  | {
      type: "inputAccepted";
      sessionId: string;
      requestId: string;
      inputId?: string;
      position?: number | null;
      runId?: string | null;
    }
  | {
      type: "inputSteerRequested";
      sessionId: string;
      inputId: string;
      requestId: string;
    }
  | { type: "inputSteerFailed"; sessionId: string; requestId: string }
  | { type: "inputCancelling"; sessionId: string; inputId: string }
  | { type: "inputCancelled"; sessionId: string; inputId?: string }
  | { type: "inputMaterialized"; sessionId: string; inputId: string }
  | {
      type: "inputFailed";
      sessionId: string;
      requestId?: string;
      inputId?: string;
    };

export const initialChatState: ChatState = {
  messagesBySession: {},
  runsById: {},
  activeRunIdBySession: {},
  approvalsByRun: {},
  inputsBySession: {},
};
