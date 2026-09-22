import type { ChatAction } from "../../state/chatTypes";
import type { PersistedRunStatus } from "../../types/chat";
import type { SequencedSocketPayload, SkillSocketPayload } from "../../types/socket";
import { formatContextCompressed } from "../../utils/format";

type TerminalRunStatus = Extract<
  PersistedRunStatus,
  "completed" | "failed" | "cancelled" | "interrupted"
>;

/**
 * Effects the React adapter applies when it consumes a projection. They are
 * described as data so the projection itself stays pure: no React, no network,
 * no timers.
 */
export type RunProjectionEffect =
  | { kind: "status"; status: string }
  | { kind: "skillEvent"; payload: SkillSocketPayload }
  | { kind: "pendingSessionResolved"; sessionId: string }
  | { kind: "runActivated"; sessionId: string; runId: string }
  | { kind: "runDeactivated"; sessionId: string }
  | { kind: "sessionRefreshRequested"; sessionId: string };

/**
 * Per-run bookkeeping owned by the RunStreamController. The projection reports
 * the update instead of mutating anything.
 */
export type RunRuntimeEffect = {
  agentSegmentReset?: boolean;
  agentSegmentDelta?: number;
  executingPlanId?: string;
  terminal?: boolean;
};

export type RunProjectionContext = {
  agentSegment: number;
  executingPlanId?: string;
};

export type RunProjection = {
  event: SequencedSocketPayload;
  actions: ChatAction[];
  effects: RunProjectionEffect[];
  runtime: RunRuntimeEffect;
};

/**
 * Projects one already-accepted sequenced run event (deduplication and gap
 * recovery happen before this call) into chat actions, UI effects and run
 * cursor updates. Behaviour mirrors the previous inline switch in
 * useAgentSocket exactly, including action ordering.
 */
export function projectRunEvent(
  event: SequencedSocketPayload,
  context: RunProjectionContext,
): RunProjection {
  const runId = event.run_id;
  const sessionId = event.session_id;
  const seq = event.seq;

  const actions: ChatAction[] = [
    {
      type: "runSequenceAdvanced",
      runId,
      sessionId,
      sequence: seq,
    },
  ];
  const effects: RunProjectionEffect[] = [];
  const runtime: RunRuntimeEffect = {};

  switch (event.type) {
    case "started": {
      runtime.agentSegmentReset = true;
      effects.push({ kind: "pendingSessionResolved", sessionId });
      effects.push({ kind: "runActivated", sessionId, runId });
      actions.push({ type: "runStarted", runId, sessionId, sequence: seq });
      effects.push({ kind: "status", status: "Streaming" });
      if (event.input_id) {
        // A queued input became its own Run. The backend persisted the user
        // message before starting the Run, so showing it here keeps the
        // visible order identical to the durable one.
        actions.push({
          type: "inputMaterialized",
          sessionId,
          inputId: event.input_id,
        });
        actions.push({
          type: "userMessageQueued",
          message: {
            id: `${runId}:input:${event.input_id}`,
            session_id: sessionId,
            role: "user",
            text: event.prompt,
          },
        });
      }
      break;
    }

    case "input_applied": {
      // Steering is applied between two assistant segments. The backend
      // flushes the preceding segment before this event, so opening a new
      // segment here keeps the continuation below the new user message.
      runtime.agentSegmentDelta = 1;
      actions.push({
        type: "inputMaterialized",
        sessionId,
        inputId: event.input_id,
      });
      actions.push({
        type: "userMessageQueued",
        message: {
          id: event.message_id,
          session_id: sessionId,
          role: "user",
          text: event.prompt,
        },
      });
      effects.push({ kind: "status", status: "Streaming" });
      break;
    }

    case "agent_step": {
      effects.push({
        kind: "status",
        status:
          typeof event.message === "string"
            ? event.message
            : `Agent step ${event.step ?? ""}`,
      });
      break;
    }

    case "context_compressed": {
      actions.push({
        type: "runEventAppended",
        id: `${runId}:context:${seq}`,
        sessionId,
        text: formatContextCompressed(event),
      });
      break;
    }

    case "skills_loaded":
    case "skill_injected": {
      effects.push({ kind: "skillEvent", payload: event });
      break;
    }

    case "skills_warning": {
      effects.push({ kind: "skillEvent", payload: event });
      effects.push({ kind: "status", status: event.message });
      break;
    }

    case "tool_call": {
      runtime.agentSegmentDelta = 1;
      const toolCallId = event.tool_call_id || `tool-${seq}`;
      actions.push({
        type: "toolCallStarted",
        sessionId,
        payload: event,
        messageId: `${runId}:tool:${toolCallId}`,
        toolCallId,
      });
      effects.push({
        kind: "status",
        status: event.tool ? `Tool: ${event.tool}` : "Calling tool",
      });
      break;
    }

    case "tool_result": {
      runtime.agentSegmentDelta = 1;
      const toolCallId = event.tool_call_id || `tool-${seq}`;
      actions.push({
        type: "toolCallCompleted",
        sessionId,
        payload: event,
        messageId: `${runId}:tool:${toolCallId}`,
        toolCallId,
      });
      effects.push({
        kind: "status",
        status: event.tool ? `Tool complete: ${event.tool}` : "Tool complete",
      });
      break;
    }

    case "tool_output_delta": {
      const toolCallId = event.tool_call_id || `tool-${seq}`;
      actions.push({
        type: "toolOutputReceived",
        sessionId,
        payload: event,
        messageId: `${runId}:tool:${toolCallId}`,
        toolCallId,
      });
      break;
    }

    case "token": {
      actions.push({
        type: "tokenReceived",
        messageId: `${runId}:agent:${context.agentSegment}`,
        sessionId,
        content: event.content ?? "",
      });
      break;
    }

    case "plan_ready": {
      runtime.executingPlanId = event.plan_id;
      actions.push({
        type: "planReady",
        messageId: `${runId}:plan:${event.plan_id}`,
        payload: event,
      });
      effects.push({ kind: "status", status: "Plan ready" });
      break;
    }

    case "plan_approved": {
      runtime.executingPlanId = event.plan_id;
      actions.push({
        type: "planStatusChanged",
        sessionId,
        planId: event.plan_id,
        status: "executing",
      });
      break;
    }

    case "tool_approval_required": {
      actions.push({ type: "approvalRequired", approval: event });
      effects.push({ kind: "status", status: `Approval required: ${event.tool}` });
      break;
    }

    case "tool_approval_resolved": {
      actions.push({
        type: "approvalResolved",
        runId,
        approvalId: event.approval_id,
      });
      effects.push({ kind: "status", status: "Streaming" });
      break;
    }

    case "run_cancel_requested": {
      actions.push({
        type: "runStatusChanged",
        runId,
        sessionId,
        status: "cancelling",
      });
      effects.push({ kind: "status", status: "Cancelling" });
      break;
    }

    case "done": {
      runtime.terminal = true;
      actions.push({
        type: "runFinished",
        runId,
        sessionId,
        status: "completed",
        sequence: seq,
      });
      pushPlanOutcome(actions, sessionId, context.executingPlanId, "executed");
      effects.push({ kind: "runDeactivated", sessionId });
      effects.push({ kind: "status", status: "Ready" });
      effects.push({ kind: "sessionRefreshRequested", sessionId });
      break;
    }

    case "run_cancelled":
    case "run_interrupted": {
      runtime.terminal = true;
      const status: TerminalRunStatus =
        event.type === "run_cancelled" ? "cancelled" : "interrupted";
      actions.push({
        type: "runFinished",
        runId,
        sessionId,
        status,
        sequence: seq,
      });
      pushPlanOutcome(actions, sessionId, context.executingPlanId, "failed");
      effects.push({ kind: "runDeactivated", sessionId });
      effects.push({
        kind: "status",
        status: status === "cancelled" ? "Cancelled" : "Interrupted",
      });
      effects.push({ kind: "sessionRefreshRequested", sessionId });
      break;
    }

    case "error": {
      runtime.terminal = true;
      const message = event.message ?? "Agent run failed";
      actions.push({
        type: "streamingFailed",
        messageId: `${runId}:error`,
        sessionId,
        errorText: message,
      });
      actions.push({
        type: "runFinished",
        runId,
        sessionId,
        status: "failed",
        sequence: seq,
      });
      pushPlanOutcome(actions, sessionId, context.executingPlanId, "failed");
      effects.push({ kind: "runDeactivated", sessionId });
      effects.push({ kind: "status", status: message });
      effects.push({ kind: "sessionRefreshRequested", sessionId });
      break;
    }

    default: {
      break;
    }
  }

  return { event, actions, effects, runtime };
}

function pushPlanOutcome(
  actions: ChatAction[],
  sessionId: string,
  planId: string | undefined,
  status: "executed" | "failed",
): void {
  if (!planId) {
    return;
  }
  actions.push({
    type: "planStatusChanged",
    sessionId,
    planId,
    status,
  });
}
