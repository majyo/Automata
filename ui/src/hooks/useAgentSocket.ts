import { useCallback, useEffect, useRef, useState } from "react";
import { createAgentSocket } from "../api/websocket";
import type { ChatAction } from "../state/chatReducer";
import type { ApiRuntimeConfig } from "../types/api";
import type {
  ApprovalDecision,
  ChatMessage,
  PersistedRunStatus,
  SendMode,
  ToolApprovalRequest,
} from "../types/chat";
import { isSequencedRunEvent } from "../types/socket";
import type { SequencedSocketPayload, SocketPayload } from "../types/socket";
import type { SkillSocketPayload } from "../types/socket";
import type { SkillSelection } from "../types/skills";
import { formatContextCompressed } from "../utils/format";

const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000];

type UseAgentSocketOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  activeSessionIdRef: React.MutableRefObject<string | null>;
  chatDispatch: React.Dispatch<ChatAction>;
  ensureActiveSession(): Promise<string>;
  refreshSessionList(): Promise<unknown>;
  reloadSessionMessages(sessionId: string): Promise<unknown>;
  onSkillEvent?(payload: SkillSocketPayload): void;
};

type RunRuntime = {
  runId: string;
  sessionId: string;
  lastSequence: number;
  agentSegment: number;
  executingPlanId?: string;
  replaying: boolean;
  terminal: boolean;
};

export function useAgentSocket({
  apiConfigRef,
  activeSessionIdRef,
  chatDispatch,
  ensureActiveSession,
  refreshSessionList,
  reloadSessionMessages,
  onSkillEvent,
}: UseAgentSocketOptions) {
  const [socketStatus, setSocketStatus] = useState("Connecting");
  const [activeRunIdBySession, setActiveRunIdBySession] = useState<Record<string, string>>({});
  const socketRef = useRef<WebSocket | null>(null);
  const runtimesRef = useRef<Record<string, RunRuntime>>({});
  const activeRunsRef = useRef<Record<string, string>>({});
  const pendingSessionsRef = useRef<Set<string>>(new Set());
  const planRequestIdsRef = useRef<Record<string, string>>({});
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const shouldReconnectRef = useRef(true);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current === null) {
      return;
    }
    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }, []);

  const updateActiveRun = useCallback((sessionId: string, runId?: string) => {
    const next = { ...activeRunsRef.current };
    if (runId) {
      next[sessionId] = runId;
    } else {
      delete next[sessionId];
    }
    activeRunsRef.current = next;
    setActiveRunIdBySession(next);
  }, []);

  const runtimeFor = useCallback((runId: string, sessionId: string): RunRuntime => {
    const current = runtimesRef.current[runId];
    if (current) {
      return current;
    }
    const runtime: RunRuntime = {
      runId,
      sessionId,
      lastSequence: 0,
      agentSegment: 0,
      replaying: false,
      terminal: false,
    };
    runtimesRef.current[runId] = runtime;
    return runtime;
  }, []);

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

  const requestResume = useCallback(
    (runId: string, sessionId: string, afterSequence: number) => {
      const socket = socketRef.current;
      const runtime = runtimeFor(runId, sessionId);
      runtime.replaying = true;
      chatDispatch({ type: "runReplayChanged", runId, replaying: true });
      if (socket?.readyState !== WebSocket.OPEN) {
        return;
      }
      socket.send(
        JSON.stringify({
          type: "resume_run",
          session_id: sessionId,
          run_id: runId,
          after_sequence: afterSequence,
        }),
      );
    },
    [chatDispatch, runtimeFor],
  );

  const refreshCompletedRun = useCallback(
    (sessionId: string) => {
      void Promise.all([
        refreshSessionList(),
        reloadSessionMessages(sessionId),
      ]).catch(() => undefined);
    },
    [refreshSessionList, reloadSessionMessages],
  );

  const applyRunEvent = useCallback(
    (payload: SequencedSocketPayload) => {
      const runtime = runtimeFor(payload.run_id, payload.session_id);
      if (payload.seq <= runtime.lastSequence) {
        return;
      }
      if (payload.seq > runtime.lastSequence + 1) {
        requestResume(payload.run_id, payload.session_id, runtime.lastSequence);
        return;
      }

      runtime.lastSequence = payload.seq;
      chatDispatch({
        type: "runSequenceAdvanced",
        runId: payload.run_id,
        sessionId: payload.session_id,
        sequence: payload.seq,
      });

      if (payload.type === "started") {
        runtime.agentSegment = 0;
        pendingSessionsRef.current.delete(payload.session_id);
        updateActiveRun(payload.session_id, payload.run_id);
        chatDispatch({
          type: "runStarted",
          runId: payload.run_id,
          sessionId: payload.session_id,
          sequence: payload.seq,
        });
        setSocketStatus("Streaming");
        return;
      }

      if (payload.type === "agent_step") {
        setSocketStatus(
          typeof payload.message === "string"
            ? payload.message
            : `Agent step ${payload.step ?? ""}`,
        );
        return;
      }

      if (payload.type === "context_compressed") {
        chatDispatch({
          type: "runEventAppended",
          id: `${payload.run_id}:context:${payload.seq}`,
          sessionId: payload.session_id,
          text: formatContextCompressed(payload),
        });
        return;
      }

      if (
        payload.type === "skills_loaded" ||
        payload.type === "skills_warning" ||
        payload.type === "skill_injected"
      ) {
        onSkillEvent?.(payload);
        if (payload.type === "skills_warning") {
          setSocketStatus(payload.message);
        }
        return;
      }

      if (payload.type === "tool_call") {
        runtime.agentSegment += 1;
        const toolCallId = payload.tool_call_id || `tool-${payload.seq}`;
        chatDispatch({
          type: "toolCallStarted",
          sessionId: payload.session_id,
          payload,
          messageId: `${payload.run_id}:tool:${toolCallId}`,
          toolCallId,
        });
        setSocketStatus(payload.tool ? `Tool: ${payload.tool}` : "Calling tool");
        return;
      }

      if (payload.type === "tool_result") {
        runtime.agentSegment += 1;
        const toolCallId = payload.tool_call_id || `tool-${payload.seq}`;
        chatDispatch({
          type: "toolCallCompleted",
          sessionId: payload.session_id,
          payload,
          messageId: `${payload.run_id}:tool:${toolCallId}`,
          toolCallId,
        });
        setSocketStatus(payload.tool ? `Tool complete: ${payload.tool}` : "Tool complete");
        return;
      }

      if (payload.type === "tool_output_delta") {
        const toolCallId = payload.tool_call_id || `tool-${payload.seq}`;
        chatDispatch({
          type: "toolOutputReceived",
          sessionId: payload.session_id,
          payload,
          messageId: `${payload.run_id}:tool:${toolCallId}`,
          toolCallId,
        });
        return;
      }

      if (payload.type === "token") {
        chatDispatch({
          type: "tokenReceived",
          messageId: `${payload.run_id}:agent:${runtime.agentSegment}`,
          sessionId: payload.session_id,
          content: payload.content ?? "",
        });
        return;
      }

      if (payload.type === "plan_ready") {
        runtime.executingPlanId = payload.plan_id;
        chatDispatch({
          type: "planReady",
          messageId: `${payload.run_id}:plan:${payload.plan_id}`,
          payload,
        });
        setSocketStatus("Plan ready");
        return;
      }

      if (payload.type === "plan_approved") {
        runtime.executingPlanId = payload.plan_id;
        chatDispatch({
          type: "planStatusChanged",
          sessionId: payload.session_id,
          planId: payload.plan_id,
          status: "executing",
        });
        return;
      }

      if (payload.type === "tool_approval_required") {
        chatDispatch({
          type: "approvalRequired",
          approval: payload as ToolApprovalRequest,
        });
        setSocketStatus(`Approval required: ${payload.tool}`);
        return;
      }

      if (payload.type === "tool_approval_resolved") {
        chatDispatch({
          type: "approvalResolved",
          runId: payload.run_id,
          approvalId: payload.approval_id,
        });
        setSocketStatus("Streaming");
        return;
      }

      if (payload.type === "run_cancel_requested") {
        chatDispatch({
          type: "runStatusChanged",
          runId: payload.run_id,
          sessionId: payload.session_id,
          status: "cancelling",
        });
        setSocketStatus("Cancelling");
        return;
      }

      if (payload.type === "done") {
        runtime.terminal = true;
        chatDispatch({
          type: "runFinished",
          runId: payload.run_id,
          sessionId: payload.session_id,
          status: "completed",
          sequence: payload.seq,
        });
        if (runtime.executingPlanId) {
          chatDispatch({
            type: "planStatusChanged",
            sessionId: payload.session_id,
            planId: runtime.executingPlanId,
            status: "executed",
          });
        }
        updateActiveRun(payload.session_id);
        setSocketStatus("Ready");
        refreshCompletedRun(payload.session_id);
        return;
      }

      if (payload.type === "run_cancelled" || payload.type === "run_interrupted") {
        runtime.terminal = true;
        const status = payload.type === "run_cancelled" ? "cancelled" : "interrupted";
        chatDispatch({
          type: "runFinished",
          runId: payload.run_id,
          sessionId: payload.session_id,
          status,
          sequence: payload.seq,
        });
        if (runtime.executingPlanId) {
          chatDispatch({
            type: "planStatusChanged",
            sessionId: payload.session_id,
            planId: runtime.executingPlanId,
            status: "failed",
          });
        }
        updateActiveRun(payload.session_id);
        setSocketStatus(status === "cancelled" ? "Cancelled" : "Interrupted");
        refreshCompletedRun(payload.session_id);
        return;
      }

      if (payload.type === "error") {
        runtime.terminal = true;
        const message = payload.message ?? "Agent run failed";
        chatDispatch({
          type: "streamingFailed",
          messageId: `${payload.run_id}:error`,
          sessionId: payload.session_id,
          errorText: message,
        });
        chatDispatch({
          type: "runFinished",
          runId: payload.run_id,
          sessionId: payload.session_id,
          status: "failed",
          sequence: payload.seq,
        });
        if (runtime.executingPlanId) {
          chatDispatch({
            type: "planStatusChanged",
            sessionId: payload.session_id,
            planId: runtime.executingPlanId,
            status: "failed",
          });
        }
        updateActiveRun(payload.session_id);
        setSocketStatus(message);
        refreshCompletedRun(payload.session_id);
      }
    },
    [
      chatDispatch,
      refreshCompletedRun,
      requestResume,
      runtimeFor,
      onSkillEvent,
      updateActiveRun,
    ],
  );

  const handlePayload = useCallback(
    (payload: SocketPayload) => {
      if (payload.type === "ready") {
        setSocketStatus(payload.message ?? "Ready");
        const activeRuns = payload.active_runs ?? [];
        const discoveredIds = new Set(activeRuns.map((run) => run.id));
        for (const run of activeRuns) {
          const hadRuntime = Boolean(runtimesRef.current[run.id]);
          runtimeFor(run.id, run.session_id);
          updateActiveRun(run.session_id, run.id);
          chatDispatch({
            type: "runDiscovered",
            runId: run.id,
            sessionId: run.session_id,
            status: run.status,
            lastSequence: hadRuntime ? runtimesRef.current[run.id].lastSequence : 0,
          });
          requestResume(
            run.id,
            run.session_id,
            hadRuntime ? runtimesRef.current[run.id].lastSequence : 0,
          );
        }
        for (const [sessionId, runId] of Object.entries(activeRunsRef.current)) {
          if (discoveredIds.has(runId)) {
            continue;
          }
          const runtime = runtimesRef.current[runId];
          if (runtime) {
            requestResume(runId, sessionId, runtime.lastSequence);
          }
        }
        return;
      }

      if (payload.type === "run_resume_started") {
        runtimeFor(payload.run_id, payload.session_id).replaying = true;
        chatDispatch({ type: "runReplayChanged", runId: payload.run_id, replaying: true });
        return;
      }

      if (payload.type === "run_resume_complete") {
        const runtime = runtimeFor(payload.run_id, payload.session_id);
        runtime.replaying = false;
        runtime.lastSequence = Math.max(runtime.lastSequence, payload.last_sequence);
        chatDispatch({ type: "runReplayChanged", runId: payload.run_id, replaying: false });
        if (!runtime.terminal) {
          chatDispatch({
            type: "runStatusChanged",
            runId: payload.run_id,
            sessionId: payload.session_id,
            status: payload.status,
          });
        }
        if (isTerminal(payload.status)) {
          updateActiveRun(payload.session_id);
          refreshCompletedRun(payload.session_id);
        }
        return;
      }

      if (payload.type === "plan_execution_created" || payload.type === "plan_execution_attached") {
        const runtime = runtimeFor(payload.run_id, payload.session_id);
        runtime.executingPlanId = payload.plan_id;
        pendingSessionsRef.current.delete(payload.session_id);
        delete planRequestIdsRef.current[payload.plan_id];
        updateActiveRun(payload.session_id, payload.run_id);
        chatDispatch({
          type: "runDiscovered",
          runId: payload.run_id,
          sessionId: payload.session_id,
          status: payload.status === "executing" ? "queued" : payload.status,
          lastSequence: runtime.lastSequence,
        });
        chatDispatch({
          type: "planStatusChanged",
          sessionId: payload.session_id,
          planId: payload.plan_id,
          status: "executing",
        });
        if (payload.type === "plan_execution_attached") {
          requestResume(payload.run_id, payload.session_id, runtime.lastSequence);
        }
        return;
      }

      if (payload.type === "plan_error") {
        const message = payload.message ?? payload.code ?? "Plan error";
        if (payload.session_id) {
          pendingSessionsRef.current.delete(payload.session_id);
          chatDispatch({
            type: "currentPlanError",
            sessionId: payload.session_id,
            planId: payload.plan_id,
          });
        }
        setSocketStatus(message);
        return;
      }

      if (payload.type === "approval_error" || payload.type === "run_error") {
        if (payload.type === "run_error" && payload.session_id) {
          pendingSessionsRef.current.delete(payload.session_id);
        }
        setSocketStatus(payload.message ?? payload.code ?? "Run request failed");
        return;
      }

      if (isSequencedRunEvent(payload)) {
        applyRunEvent(payload);
      }
    },
    [
      applyRunEvent,
      chatDispatch,
      refreshCompletedRun,
      requestResume,
      runtimeFor,
      updateActiveRun,
    ],
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
          socketRef.current = null;
          setSocketStatus("Reconnecting");
          scheduleReconnect();
        },
        onError: () => {
          if (socketRef.current === socket) {
            setSocketStatus("Backend offline");
          }
        },
      });
      socketRef.current = socket;
    },
    [apiConfigRef, clearReconnectTimer, handlePayload, scheduleReconnect],
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
    async (
      prompt: string,
      sendMode: SendMode,
      skills: SkillSelection[] = [],
    ) => {
      const trimmedPrompt = prompt.trim();
      const socket = socketRef.current;
      if (!trimmedPrompt || socket?.readyState !== WebSocket.OPEN) {
        setSocketStatus("Backend offline");
        scheduleReconnect();
        return false;
      }

      let sessionId: string;
      try {
        sessionId = await ensureActiveSession();
      } catch {
        setSocketStatus("Could not create session");
        return false;
      }
      if (activeRunsRef.current[sessionId] || pendingSessionsRef.current.has(sessionId)) {
        setSocketStatus("This session already has an active run");
        return false;
      }

      pendingSessionsRef.current.add(sessionId);
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "user",
        text: trimmedPrompt,
      };
      chatDispatch({ type: "userMessageQueued", message: userMessage });
      setSocketStatus("Starting");
      socket.send(JSON.stringify({
        type: "prompt",
        session_id: sessionId,
        prompt: trimmedPrompt,
        ...(sendMode === "plan" ? { mode: "plan" } : {}),
        ...(skills.length ? { skills } : {}),
      }));
      return true;
    },
    [chatDispatch, ensureActiveSession, scheduleReconnect],
  );

  const approvePlan = useCallback(
    (message: ChatMessage) => {
      const socket = socketRef.current;
      const sessionId = message.session_id;
      const planId = message.plan_id;
      if (
        !sessionId ||
        !planId ||
        socket?.readyState !== WebSocket.OPEN ||
        activeRunsRef.current[sessionId] ||
        pendingSessionsRef.current.has(sessionId)
      ) {
        return;
      }

      const retry = message.plan_status === "failed";
      if (retry && !window.confirm("Retrying may repeat side effects from the previous attempt. Continue?")) {
        return;
      }
      if (!retry && message.plan_status !== "pending") {
        return;
      }

      const requestId = planRequestIdsRef.current[planId] ?? crypto.randomUUID();
      planRequestIdsRef.current[planId] = requestId;
      pendingSessionsRef.current.add(sessionId);
      chatDispatch({
        type: "planStatusChanged",
        sessionId,
        planId,
        status: "approving",
      });
      socket.send(
        JSON.stringify(
          retry
            ? {
                type: "retry_plan",
                session_id: sessionId,
                plan_id: planId,
                request_id: requestId,
                confirm_possible_duplicate_side_effects: true,
              }
            : {
                type: "approve_plan",
                session_id: sessionId,
                plan_id: planId,
                request_id: requestId,
              },
        ),
      );
    },
    [chatDispatch],
  );

  const respondToApproval = useCallback(
    (approval: ToolApprovalRequest, decision: ApprovalDecision) => {
      const socket = socketRef.current;
      if (socket?.readyState !== WebSocket.OPEN) {
        setSocketStatus("Backend offline");
        return;
      }
      socket.send(
        JSON.stringify({
          type: "tool_approval_response",
          session_id: approval.session_id,
          run_id: approval.run_id,
          approval_id: approval.approval_id,
          decision,
        }),
      );
    },
    [],
  );

  const cancelRun = useCallback(() => {
    const socket = socketRef.current;
    const sessionId = activeSessionIdRef.current;
    const runId = sessionId ? activeRunsRef.current[sessionId] : undefined;
    if (!sessionId || !runId || socket?.readyState !== WebSocket.OPEN) {
      return;
    }
    setSocketStatus("Cancelling");
    socket.send(
      JSON.stringify({
        type: "cancel_run",
        session_id: sessionId,
        run_id: runId,
      }),
    );
  }, [activeSessionIdRef]);

  const activeSessionId = activeSessionIdRef.current;
  const isStreaming = Boolean(
    activeSessionId &&
      (activeRunIdBySession[activeSessionId] ||
        pendingSessionsRef.current.has(activeSessionId)),
  );

  return {
    socketStatus,
    setSocketStatus,
    isStreaming,
    activeRunIdBySession,
    isSessionRunning: (sessionId: string | null) =>
      Boolean(
        sessionId &&
          (activeRunsRef.current[sessionId] ||
            pendingSessionsRef.current.has(sessionId)),
      ),
    connectSocket,
    sendPrompt,
    approvePlan,
    respondToApproval,
    cancelRun,
  };
}

function isTerminal(status: PersistedRunStatus): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(status);
}
