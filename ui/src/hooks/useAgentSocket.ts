import { useCallback, useEffect, useRef, useState } from "react";
import type {
  RunProjection,
  RunProjectionEffect,
} from "../features/runs/projection";
import { RunStreamController } from "../features/runs/runStreamController";
import type { RunResumeRequest, RunRuntime } from "../features/runs/runStreamController";
import { isTerminalRunStatus } from "../features/runs/runStatus";
import { SessionRunTracker } from "../features/runs/sessionRunTracker";
import { AgentSocketClient } from "../platform/api/agentSocketClient";
import type { ChatAction } from "../state/chatReducer";
import type { ApiRuntimeConfig } from "../types/api";
import type {
  ApprovalDecision,
  ChatMessage,
  PendingInput,
  SendMode,
  ToolApprovalRequest,
} from "../types/chat";
import { isSequencedRunEvent } from "../types/socket";
import type { SkillSocketPayload, SocketPayload } from "../types/socket";
import type { SkillSelection } from "../types/skills";

type UseAgentSocketOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  activeSessionIdRef: React.MutableRefObject<string | null>;
  chatDispatch: React.Dispatch<ChatAction>;
  ensureActiveSession(): Promise<string>;
  refreshSessionList(): Promise<unknown>;
  reloadSessionMessages(sessionId: string): Promise<unknown>;
  onSkillEvent?(payload: SkillSocketPayload): void;
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

  // Session-scoped run bookkeeping (active Run, in-flight command, plan
  // request ids) lives in a store so its rules are testable without React.
  const trackerRef = useRef<SessionRunTracker | null>(null);
  if (trackerRef.current === null) {
    trackerRef.current = new SessionRunTracker();
  }
  const tracker = trackerRef.current;

  const clientRef = useRef<AgentSocketClient | null>(null);
  const controllerRef = useRef<RunStreamController | null>(null);
  const handlePayloadRef = useRef<(payload: SocketPayload) => void>(() => undefined);
  const applyProjectionRef = useRef<(projection: RunProjection, runtime: RunRuntime) => void>(
    () => undefined,
  );
  const requestResumeRef = useRef<(request: RunResumeRequest) => void>(() => undefined);

  const getClient = useCallback((): AgentSocketClient => {
    let client = clientRef.current;
    if (!client) {
      client = new AgentSocketClient({
        onConnecting: () => setSocketStatus("Connecting"),
        onOpen: () => setSocketStatus("Connected"),
        onPayload: (payload) => handlePayloadRef.current(payload),
        onInvalidPayload: () => setSocketStatus("Invalid backend event"),
        onClosed: () => {
          setSocketStatus("Reconnecting");
          clientRef.current?.scheduleReconnect();
        },
        onReconnectScheduled: () => setSocketStatus("Reconnecting"),
        onError: () => setSocketStatus("Backend offline"),
        resolveConnectOptions: () => ({
          url: apiConfigRef.current.wsChatUrl,
          apiToken: apiConfigRef.current.apiToken,
        }),
      });
      clientRef.current = client;
    }
    return client;
  }, [apiConfigRef]);

  const getController = useCallback((): RunStreamController => {
    let controller = controllerRef.current;
    if (!controller) {
      controller = new RunStreamController({
        onRunEvent: (projection, runtime) =>
          applyProjectionRef.current(projection, runtime),
        onResumeRequested: (request) => requestResumeRef.current(request),
      });
      controllerRef.current = controller;
    }
    return controller;
  }, []);

  const updateActiveRun = useCallback(
    (sessionId: string, runId?: string) => {
      tracker.setActiveRun(sessionId, runId);
    },
    [tracker],
  );

  // Mirror the store's active-Run map into React state for rendering.
  useEffect(() => tracker.subscribe(setActiveRunIdBySession), [tracker]);

  const refreshCompletedRun = useCallback(
    (sessionId: string) => {
      void Promise.all([
        refreshSessionList(),
        reloadSessionMessages(sessionId),
      ]).catch(() => undefined);
    },
    [refreshSessionList, reloadSessionMessages],
  );

  const applyProjectionEffect = useCallback(
    (effect: RunProjectionEffect) => {
      switch (effect.kind) {
        case "status":
          setSocketStatus(effect.status);
          break;
        case "skillEvent":
          onSkillEvent?.(effect.payload);
          break;
        case "pendingSessionResolved":
          tracker.clearPending(effect.sessionId);
          break;
        case "runActivated":
          updateActiveRun(effect.sessionId, effect.runId);
          break;
        case "runDeactivated":
          updateActiveRun(effect.sessionId);
          break;
        case "sessionRefreshRequested":
          refreshCompletedRun(effect.sessionId);
          break;
        default:
          break;
      }
    },
    [onSkillEvent, refreshCompletedRun, updateActiveRun],
  );

  const applyProjection = useCallback(
    (projection: RunProjection) => {
      for (const action of projection.actions) {
        chatDispatch(action);
      }
      for (const effect of projection.effects) {
        applyProjectionEffect(effect);
      }
    },
    [applyProjectionEffect, chatDispatch],
  );

  const requestResume = useCallback(
    ({ runId, sessionId, afterSequence }: RunResumeRequest) => {
      chatDispatch({ type: "runReplayChanged", runId, replaying: true });
      getClient().send({
        type: "resume_run",
        session_id: sessionId,
        run_id: runId,
        after_sequence: afterSequence,
      });
    },
    [chatDispatch, getClient],
  );

  const handlePayload = useCallback(
    (payload: SocketPayload) => {
      const controller = getController();

      if (payload.type === "ready") {
        setSocketStatus(payload.message ?? "Ready");
        const activeRuns = payload.active_runs ?? [];
        const discoveredIds = new Set(activeRuns.map((run) => run.id));
        for (const run of activeRuns) {
          const hadRuntime = Boolean(controller.runtimeIfKnown(run.id));
          const runtime = controller.runtimeFor(run.id, run.session_id);
          updateActiveRun(run.session_id, run.id);
          chatDispatch({
            type: "runDiscovered",
            runId: run.id,
            sessionId: run.session_id,
            status: run.status,
            lastSequence: hadRuntime ? runtime.lastSequence : 0,
          });
          controller.requestResume(
            run.id,
            run.session_id,
            hadRuntime ? runtime.lastSequence : 0,
          );
        }
        for (const [sessionId, runId] of Object.entries(tracker.snapshot())) {
          if (discoveredIds.has(runId)) {
            continue;
          }
          const runtime = controller.runtimeIfKnown(runId);
          if (runtime) {
            controller.requestResume(runId, sessionId, runtime.lastSequence);
          }
        }
        return;
      }

      if (payload.type === "run_resume_started") {
        controller.setReplaying(payload.run_id, payload.session_id, true);
        chatDispatch({ type: "runReplayChanged", runId: payload.run_id, replaying: true });
        return;
      }

      if (payload.type === "run_resume_complete") {
        const runtime = controller.completeReplay(
          payload.run_id,
          payload.session_id,
          payload.last_sequence,
        );
        chatDispatch({ type: "runReplayChanged", runId: payload.run_id, replaying: false });
        if (!runtime.terminal) {
          chatDispatch({
            type: "runStatusChanged",
            runId: payload.run_id,
            sessionId: payload.session_id,
            status: payload.status,
          });
        }
        if (isTerminalRunStatus(payload.status)) {
          updateActiveRun(payload.session_id);
          refreshCompletedRun(payload.session_id);
        }
        return;
      }

      if (payload.type === "plan_execution_created" || payload.type === "plan_execution_attached") {
        const runtime = controller.setExecutingPlanId(
          payload.run_id,
          payload.session_id,
          payload.plan_id,
        );
        tracker.clearPending(payload.session_id);
        tracker.releasePlan(payload.plan_id);
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
          controller.requestResume(payload.run_id, payload.session_id, runtime.lastSequence);
        }
        return;
      }

      if (payload.type === "input_accepted") {
        chatDispatch({
          type: "inputAccepted",
          sessionId: payload.session_id,
          requestId: payload.request_id,
          inputId: payload.input_id ?? undefined,
          position: payload.position ?? null,
          runId: payload.run_id ?? null,
        });
        const queuedInputId = tracker.takeSteer(payload.request_id);
        if (queuedInputId) {
          // The steering input is accepted, so the queued copy is withdrawn:
          // the prompt is delivered once, into the Run that is already active.
          getClient().send({
            type: "cancel_input",
            session_id: payload.session_id,
            input_id: queuedInputId,
          });
          chatDispatch({
            type: "inputCancelling",
            sessionId: payload.session_id,
            inputId: queuedInputId,
          });
        }
        return;
      }

      if (payload.type === "input_cancelled") {
        chatDispatch({
          type: "inputCancelled",
          sessionId: payload.session_id,
          inputId: payload.input_id,
        });
        setSocketStatus("Input withdrawn");
        return;
      }

      if (payload.type === "plan_error") {
        const message = payload.message ?? payload.code ?? "Plan error";
        if (payload.session_id) {
          tracker.clearPending(payload.session_id);
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
          tracker.clearPending(payload.session_id);
          if (payload.request_id) {
            const refusedInputId = tracker.takeSteer(payload.request_id);
            if (refusedInputId) {
              // Steering was refused: the queued copy stays the only copy.
              chatDispatch({
                type: "inputSteerFailed",
                sessionId: payload.session_id,
                requestId: payload.request_id,
              });
            }
          }
          if (payload.input_id) {
            chatDispatch({
              type: "inputFailed",
              sessionId: payload.session_id,
              inputId: payload.input_id,
            });
          }
        }
        setSocketStatus(payload.message ?? payload.code ?? "Run request failed");
        return;
      }

      if (isSequencedRunEvent(payload)) {
        controller.acceptEvent(payload);
      }
    },
    [chatDispatch, getClient, getController, refreshCompletedRun, updateActiveRun],
  );

  handlePayloadRef.current = handlePayload;
  applyProjectionRef.current = applyProjection;
  requestResumeRef.current = requestResume;

  const connectSocket = useCallback(
    (config = apiConfigRef.current) => {
      getClient().connect({ url: config.wsChatUrl, apiToken: config.apiToken });
    },
    [apiConfigRef, getClient],
  );

  useEffect(
    () => () => {
      clientRef.current?.close();
    },
    [],
  );

  const sendPrompt = useCallback(
    async (
      prompt: string,
      sendMode: SendMode,
      skills: SkillSelection[] = [],
    ) => {
      const trimmedPrompt = prompt.trim();
      const client = getClient();
      if (!trimmedPrompt || !client.isOpen()) {
        setSocketStatus("Backend offline");
        client.scheduleReconnect();
        return false;
      }

      let sessionId: string;
      try {
        sessionId = await ensureActiveSession();
      } catch {
        setSocketStatus("Could not create session");
        return false;
      }

      const activeRunId = tracker.activeRunId(sessionId);
      const queueBehindActiveRun = Boolean(activeRunId) || tracker.isPending(sessionId);

      if (queueBehindActiveRun) {
        const requestId = crypto.randomUUID();
        // Sending while the session is busy queues the prompt instead of
        // racing the backend's one-Run-per-session rule. The message stays
        // out of the transcript until the Run it becomes reports `started`.
        chatDispatch({
          type: "inputSubmitted",
          sessionId,
          requestId,
          prompt: trimmedPrompt,
          delivery: "queue",
          runId: activeRunId,
        });
        setSocketStatus("Queued");
        client.send({
          type: "prompt",
          session_id: sessionId,
          prompt: trimmedPrompt,
          ...(sendMode === "plan" ? { mode: "plan" as const } : {}),
          ...(skills.length ? { skills } : {}),
          delivery: "queue",
          request_id: requestId,
        });
        return true;
      }

      tracker.markPending(sessionId);
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "user",
        text: trimmedPrompt,
      };
      chatDispatch({ type: "userMessageQueued", message: userMessage });
      setSocketStatus("Starting");
      client.send({
        type: "prompt",
        session_id: sessionId,
        prompt: trimmedPrompt,
        ...(sendMode === "plan" ? { mode: "plan" } : {}),
        ...(skills.length ? { skills } : {}),
      });
      return true;
    },
    [chatDispatch, ensureActiveSession, getClient, tracker],
  );

  /**
   * Deliver an already-queued message into the Run that is active now.
   *
   * The queued copy is only withdrawn after the steering input is accepted
   * (see `input_accepted` handling), so a refused steer never loses the
   * prompt.
   */
  const steerInput = useCallback(
    (input: PendingInput) => {
      const client = getClient();
      if (!input.inputId || input.steerRequestId) {
        return;
      }
      if (!client.isOpen()) {
        setSocketStatus("Backend offline");
        return;
      }
      const runId = tracker.activeRunId(input.sessionId);
      if (!runId) {
        setSocketStatus("No active run to steer");
        return;
      }

      const requestId = crypto.randomUUID();
      tracker.beginSteer(requestId, input.inputId);
      chatDispatch({
        type: "inputSteerRequested",
        sessionId: input.sessionId,
        inputId: input.inputId,
        requestId,
      });
      setSocketStatus("Steering");
      client.send({
        type: "prompt",
        session_id: input.sessionId,
        prompt: input.prompt,
        delivery: "steer",
        run_id: runId,
        request_id: requestId,
      });
    },
    [chatDispatch, getClient, tracker],
  );

  const cancelInput = useCallback(
    (input: PendingInput) => {
      const client = getClient();
      if (!input.inputId || input.status === "cancelling") {
        return;
      }
      if (!client.isOpen()) {
        setSocketStatus("Backend offline");
        return;
      }
      chatDispatch({
        type: "inputCancelling",
        sessionId: input.sessionId,
        inputId: input.inputId,
      });
      client.send({
        type: "cancel_input",
        session_id: input.sessionId,
        input_id: input.inputId,
      });
    },
    [chatDispatch, getClient],
  );

  const approvePlan = useCallback(
    (message: ChatMessage) => {
      const client = getClient();
      const sessionId = message.session_id;
      const planId = message.plan_id;
      if (
        !sessionId ||
        !planId ||
        !client.isOpen() ||
        tracker.activeRunId(sessionId) ||
        tracker.isPending(sessionId)
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

      const requestId = tracker.requestIdForPlan(planId, () => crypto.randomUUID());
      tracker.markPending(sessionId);
      chatDispatch({
        type: "planStatusChanged",
        sessionId,
        planId,
        status: "approving",
      });
      client.send(
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
      );
    },
    [chatDispatch, getClient],
  );

  const respondToApproval = useCallback(
    (approval: ToolApprovalRequest, decision: ApprovalDecision) => {
      const client = getClient();
      if (!client.isOpen()) {
        setSocketStatus("Backend offline");
        return;
      }
      client.send({
        type: "tool_approval_response",
        session_id: approval.session_id,
        run_id: approval.run_id,
        approval_id: approval.approval_id,
        decision,
      });
    },
    [getClient],
  );

  const cancelRun = useCallback(() => {
    const client = getClient();
    const sessionId = activeSessionIdRef.current;
    const runId = sessionId ? tracker.activeRunId(sessionId) : undefined;
    if (!sessionId || !runId || !client.isOpen()) {
      return;
    }
    setSocketStatus("Cancelling");
    client.send({
      type: "cancel_run",
      session_id: sessionId,
      run_id: runId,
    });
  }, [activeSessionIdRef, getClient]);

  const activeSessionId = activeSessionIdRef.current;
  const isStreaming = Boolean(
    activeSessionId &&
      (activeRunIdBySession[activeSessionId] ||
        tracker.isPending(activeSessionId)),
  );

  return {
    socketStatus,
    setSocketStatus,
    isStreaming,
    activeRunIdBySession,
    isSessionRunning: (sessionId: string | null) =>
      Boolean(
        sessionId &&
          (tracker.activeRunId(sessionId) ||
            tracker.isPending(sessionId)),
      ),
    connectSocket,
    sendPrompt,
    steerInput,
    cancelInput,
    approvePlan,
    respondToApproval,
    cancelRun,
  };
}
