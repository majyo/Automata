import { useCallback, useEffect, useRef, useState } from "react";
import { createAgentSocket } from "../api/websocket";
import type { ChatAction } from "../state/chatReducer";
import type { ApiRuntimeConfig } from "../types/api";
import type { ApprovalDecision, ChatMessage, SendMode, ToolApprovalRequest } from "../types/chat";
import type { SocketPayload } from "../types/socket";
import { formatContextCompressed } from "../utils/format";

const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000];

type UseAgentSocketOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  activeSessionIdRef: React.MutableRefObject<string | null>;
  chatDispatch: React.Dispatch<ChatAction>;
  ensureActiveSession(): Promise<string>;
  refreshSessionList(): Promise<unknown>;
};

export function useAgentSocket({
  apiConfigRef,
  activeSessionIdRef,
  chatDispatch,
  ensureActiveSession,
  refreshSessionList,
}: UseAgentSocketOptions) {
  const [socketStatus, setSocketStatus] = useState("Connecting");
  const [isStreaming, setIsStreaming] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingSessionIdRef = useRef<string | null>(null);
  const executingPlanIdRef = useRef<string | null>(null);
  const nextTokenStartsNewAgentMessageRef = useRef(false);
  const toolRunMessageIdsRef = useRef<Record<string, string>>({});
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const shouldReconnectRef = useRef(true);
  const isStreamingRef = useRef(false);
  const activeRunIdRef = useRef<string | null>(null);

  useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current === null) {
      return;
    }

    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }, []);

  const finishStreamingWithError = useCallback(
    (errorText: string) => {
      const messageId = streamingMessageIdRef.current;
      const sessionId = streamingSessionIdRef.current;
      const planId = executingPlanIdRef.current;
      const runId = activeRunIdRef.current;

      chatDispatch({
        type: "streamingFailed",
        messageId,
        sessionId,
        errorText,
      });

      setIsStreaming(false);
      isStreamingRef.current = false;
      chatDispatch({ type: "runFinished", runId: runId ?? undefined });
      activeRunIdRef.current = null;
      streamingMessageIdRef.current = null;
      streamingSessionIdRef.current = null;
      executingPlanIdRef.current = null;
      nextTokenStartsNewAgentMessageRef.current = false;

      if (planId) {
        chatDispatch({ type: "planStatusChanged", planId, status: "error" });
      }

      if (sessionId) {
        void refreshSessionList().catch(() => undefined);
      }
    },
    [chatDispatch, refreshSessionList],
  );

  const scheduleReconnect = useCallback(() => {
    if (!shouldReconnectRef.current || reconnectTimerRef.current !== null) {
      return;
    }

    const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
    reconnectAttemptRef.current += 1;
    setSocketStatus("Reconnecting");
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      connectSocket(apiConfigRef.current);
    }, delay);
  }, [apiConfigRef]);

  const handlePayload = useCallback(
    (payload: SocketPayload) => {
      if (payload.type === "ready") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : "Ready");
        return;
      }

      if (payload.type === "started") {
        setSocketStatus("Streaming");
        setIsStreaming(true);
        isStreamingRef.current = true;
        activeRunIdRef.current = payload.run_id;
        chatDispatch({ type: "runStarted", runId: payload.run_id });
        nextTokenStartsNewAgentMessageRef.current = false;
        return;
      }

      if (payload.type === "tool_approval_required") {
        setSocketStatus(`Approval required: ${payload.tool}`);
        chatDispatch({ type: "approvalRequired", approval: payload as ToolApprovalRequest });
        return;
      }

      if (payload.type === "tool_approval_resolved") {
        chatDispatch({ type: "approvalResolved", approvalId: payload.approval_id });
        setSocketStatus("Streaming");
        return;
      }

      if (payload.type === "run_cancel_requested") {
        chatDispatch({ type: "runCancelRequested", runId: payload.run_id });
        setSocketStatus("Cancelling");
        return;
      }

      if (payload.type === "run_cancelled") {
        chatDispatch({ type: "runFinished", runId: payload.run_id });
        setSocketStatus("Ready");
        setIsStreaming(false);
        isStreamingRef.current = false;
        activeRunIdRef.current = null;
        streamingMessageIdRef.current = null;
        streamingSessionIdRef.current = null;
        executingPlanIdRef.current = null;
        nextTokenStartsNewAgentMessageRef.current = false;
        toolRunMessageIdsRef.current = {};
        return;
      }

      if (payload.type === "approval_error" || payload.type === "run_error") {
        const message = payload.message ?? payload.code ?? "Run request failed";
        setSocketStatus(message);
        if (payload.type === "run_error") {
          finishStreamingWithError(message);
        }
        return;
      }

      if (payload.type === "agent_step") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : `Agent step ${payload.step ?? ""}`);
        return;
      }

      if (payload.type === "context_compressed") {
        setSocketStatus(payload.scope === "loop" ? "Compressed tool context" : "Compressed session context");
        chatDispatch({
          type: "runEventAppended",
          id: crypto.randomUUID(),
          sessionId: streamingSessionIdRef.current ?? activeSessionIdRef.current ?? undefined,
          text: formatContextCompressed(payload),
        });
        return;
      }

      if (payload.type === "tool_call") {
        const toolCallId = payload.tool_call_id || crypto.randomUUID();
        const messageId = crypto.randomUUID();
        toolRunMessageIdsRef.current[toolCallId] = messageId;
        nextTokenStartsNewAgentMessageRef.current = true;
        setSocketStatus(payload.tool ? `Tool: ${payload.tool}` : "Calling tool");
        chatDispatch({
          type: "toolCallStarted",
          sessionId: streamingSessionIdRef.current ?? activeSessionIdRef.current ?? undefined,
          payload,
          messageId,
          toolCallId,
        });
        return;
      }

      if (payload.type === "tool_result") {
        const toolCallId = payload.tool_call_id || "unknown_tool_call";
        const messageId = toolRunMessageIdsRef.current[toolCallId] ?? crypto.randomUUID();
        if (!toolRunMessageIdsRef.current[toolCallId]) {
          toolRunMessageIdsRef.current[toolCallId] = messageId;
        }
        nextTokenStartsNewAgentMessageRef.current = true;
        setSocketStatus(payload.tool ? `Tool complete: ${payload.tool}` : "Tool complete");
        chatDispatch({
          type: "toolCallCompleted",
          sessionId: streamingSessionIdRef.current ?? activeSessionIdRef.current ?? undefined,
          payload,
          messageId,
          toolCallId,
        });
        return;
      }

      if (payload.type === "plan_ready") {
        const messageId =
          nextTokenStartsNewAgentMessageRef.current || !streamingMessageIdRef.current
            ? crypto.randomUUID()
            : streamingMessageIdRef.current;
        streamingMessageIdRef.current = messageId;
        nextTokenStartsNewAgentMessageRef.current = false;
        streamingSessionIdRef.current = payload.session_id;
        setSocketStatus("Plan ready");
        chatDispatch({ type: "planReady", messageId, payload });
        return;
      }

      if (payload.type === "plan_approved") {
        setSocketStatus("Plan approved");
        executingPlanIdRef.current = payload.plan_id;
        chatDispatch({ type: "planStatusChanged", planId: payload.plan_id, status: "executing" });
        return;
      }

      if (payload.type === "plan_error") {
        const message = typeof payload.message === "string" ? payload.message : "Plan error";
        setSocketStatus(message);
        chatDispatch({ type: "currentPlanError", planId: executingPlanIdRef.current });
        finishStreamingWithError(message);
        return;
      }

      if (payload.type === "token") {
        if (nextTokenStartsNewAgentMessageRef.current || !streamingMessageIdRef.current) {
          streamingMessageIdRef.current = crypto.randomUUID();
          nextTokenStartsNewAgentMessageRef.current = false;
        }

        chatDispatch({
          type: "tokenReceived",
          messageId: streamingMessageIdRef.current,
          sessionId: streamingSessionIdRef.current ?? undefined,
          content: payload.content ?? "",
        });
        return;
      }

      if (payload.type === "done") {
        setSocketStatus("Ready");
        setIsStreaming(false);
        isStreamingRef.current = false;
        chatDispatch({ type: "runFinished", runId: payload.run_id });
        activeRunIdRef.current = null;
        if (executingPlanIdRef.current) {
          chatDispatch({ type: "planStatusChanged", planId: executingPlanIdRef.current, status: "executed" });
          executingPlanIdRef.current = null;
        }
        streamingMessageIdRef.current = null;
        nextTokenStartsNewAgentMessageRef.current = false;
        toolRunMessageIdsRef.current = {};
        const sessionId = streamingSessionIdRef.current;
        streamingSessionIdRef.current = null;
        if (sessionId) {
          void refreshSessionList();
        }
        return;
      }

      if (payload.type === "error") {
        const message = typeof payload.message === "string" ? payload.message : "Backend error";
        setSocketStatus(message);
        finishStreamingWithError(message);
      }
    },
    [activeSessionIdRef, chatDispatch, finishStreamingWithError, refreshSessionList],
  );

  const connectSocket = useCallback(
    (config = apiConfigRef.current) => {
      clearReconnectTimer();
      setSocketStatus("Connecting");

      const socket = createAgentSocket(config.wsChatUrl, config.apiToken, {
        onOpen: () => {
          reconnectAttemptRef.current = 0;
          setSocketStatus("Connected");
        },
        onPayload: handlePayload,
        onInvalidPayload: () => setSocketStatus("Invalid backend event"),
        onClose: (closedSocket) => {
          if (socketRef.current !== closedSocket) {
            return;
          }

          setSocketStatus("Offline");
          socketRef.current = null;
          if (streamingMessageIdRef.current) {
            finishStreamingWithError("Backend connection closed before a response was received.");
          } else {
            setIsStreaming(false);
            isStreamingRef.current = false;
          }
          scheduleReconnect();
        },
        onError: () => {
          if (socketRef.current !== socket) {
            return;
          }

          setSocketStatus("Backend offline");
        },
      });

      socketRef.current = socket;
    },
    [apiConfigRef, clearReconnectTimer, finishStreamingWithError, handlePayload, scheduleReconnect],
  );

  useEffect(
    () => () => {
      shouldReconnectRef.current = false;
      clearReconnectTimer();
      socketRef.current?.close();
      socketRef.current = null;
    },
    [clearReconnectTimer],
  );

  const sendPrompt = useCallback(
    async (prompt: string, sendMode: SendMode) => {
      const trimmedPrompt = prompt.trim();
      const socket = socketRef.current;
      if (!trimmedPrompt || isStreamingRef.current) {
        return false;
      }

      let sessionId: string;
      try {
        sessionId = await ensureActiveSession();
      } catch {
        setSocketStatus("Could not create session");
        return false;
      }

      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "user",
        text: trimmedPrompt,
      };
      const agentMessage: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "agent",
        text: "",
      };

      chatDispatch({ type: "userMessageQueued", message: userMessage });
      setIsStreaming(true);
      isStreamingRef.current = true;
      setSocketStatus("Starting");
      streamingMessageIdRef.current = agentMessage.id;
      nextTokenStartsNewAgentMessageRef.current = false;
      streamingSessionIdRef.current = sessionId;

      if (socket?.readyState === WebSocket.OPEN) {
        const payload =
          sendMode === "plan"
            ? { type: "prompt", session_id: sessionId, prompt: trimmedPrompt, mode: "plan" }
            : { type: "prompt", session_id: sessionId, prompt: trimmedPrompt };
        socket.send(JSON.stringify(payload));
        return true;
      }

      chatDispatch({
        type: "agentMessageQueued",
        message: {
          ...agentMessage,
          text: "Backend is offline. Restart the desktop app and try again.",
        },
      });
      setSocketStatus("Backend offline");
      scheduleReconnect();
      setIsStreaming(false);
      isStreamingRef.current = false;
      streamingMessageIdRef.current = null;
      streamingSessionIdRef.current = null;
      nextTokenStartsNewAgentMessageRef.current = false;
      return true;
    },
    [chatDispatch, ensureActiveSession, scheduleReconnect],
  );

  const approvePlan = useCallback(
    (message: ChatMessage) => {
      const socket = socketRef.current;
      const sessionId = message.session_id ?? activeSessionIdRef.current;
      const planId = message.plan_id;
      if (!sessionId || !planId || isStreamingRef.current || message.plan_status !== "pending") {
        return;
      }

      const agentMessage: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "agent",
        text: "",
      };

      chatDispatch({ type: "planStatusChanged", planId, status: "approving" });
      streamingMessageIdRef.current = agentMessage.id;
      streamingSessionIdRef.current = sessionId;
      executingPlanIdRef.current = planId;
      nextTokenStartsNewAgentMessageRef.current = false;
      setIsStreaming(true);
      isStreamingRef.current = true;

      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "approve_plan", session_id: sessionId, plan_id: planId }));
        return;
      }

      chatDispatch({
        type: "streamingFailed",
        messageId: agentMessage.id,
        sessionId,
        errorText: "Backend is offline. Restart the desktop app and try again.",
      });
      chatDispatch({ type: "planStatusChanged", planId, status: "error" });
      setSocketStatus("Backend offline");
      scheduleReconnect();
      setIsStreaming(false);
      isStreamingRef.current = false;
      streamingMessageIdRef.current = null;
      streamingSessionIdRef.current = null;
      executingPlanIdRef.current = null;
      nextTokenStartsNewAgentMessageRef.current = false;
      toolRunMessageIdsRef.current = {};
    },
    [activeSessionIdRef, chatDispatch, scheduleReconnect],
  );

  const respondToApproval = useCallback((approval: ToolApprovalRequest, decision: ApprovalDecision) => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) {
      setSocketStatus("Backend offline");
      return;
    }
    socket.send(
      JSON.stringify({
        type: "tool_approval_response",
        run_id: approval.run_id,
        approval_id: approval.approval_id,
        decision,
      }),
    );
  }, []);

  const cancelRun = useCallback(() => {
    const socket = socketRef.current;
    const runId = activeRunIdRef.current;
    if (!runId || socket?.readyState !== WebSocket.OPEN) {
      return;
    }
    setSocketStatus("Cancelling");
    chatDispatch({ type: "runCancelRequested", runId });
    socket.send(JSON.stringify({ type: "cancel_run", run_id: runId }));
  }, [chatDispatch]);

  return {
    socketStatus,
    setSocketStatus,
    isStreaming,
    isStreamingRef,
    connectSocket,
    sendPrompt,
    approvePlan,
    respondToApproval,
    cancelRun,
  };
}
