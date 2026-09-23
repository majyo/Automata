import { describe, expect, it } from "vitest";
import type { SequencedSocketPayload } from "../../types/socket";
import { projectRunEvent } from "./projection";
import type { RunProjectionContext } from "./projection";

const base = {
  session_id: "session-1",
  run_id: "run-1",
  schema_version: 1,
};

const noContext: RunProjectionContext = { agentSegment: 0 };

function runTypes(projection: ReturnType<typeof projectRunEvent>): string[] {
  return projection.actions.map((action) => action.type);
}

describe("projectRunEvent", () => {
  it("always advances the run sequence first", () => {
    const event: SequencedSocketPayload = { type: "agent_step", ...base, seq: 7 };
    const projection = projectRunEvent(event, noContext);

    expect(projection.actions[0]).toEqual({
      type: "runSequenceAdvanced",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 7,
    });
    expect(projection.effects).toEqual([{ kind: "status", status: "Agent step " }]);
  });

  it("projects a started event into run activation and segment reset", () => {
    const event: SequencedSocketPayload = {
      type: "started",
      prompt: "hello",
      ...base,
      seq: 1,
    };
    const projection = projectRunEvent(event, { agentSegment: 3 });

    expect(runTypes(projection)).toEqual(["runSequenceAdvanced", "runStarted"]);
    expect(projection.runtime).toEqual({ agentSegmentReset: true });
    expect(projection.effects).toEqual([
      { kind: "pendingSessionResolved", sessionId: "session-1" },
      { kind: "runActivated", sessionId: "session-1", runId: "run-1" },
      { kind: "status", status: "Streaming" },
    ]);
  });

  it("uses the current agent segment for token message ids", () => {
    const event: SequencedSocketPayload = {
      type: "token",
      content: "hi",
      ...base,
      seq: 4,
    };
    const projection = projectRunEvent(event, { agentSegment: 2 });

    expect(projection.actions[1]).toEqual({
      type: "tokenReceived",
      messageId: "run-1:agent:2",
      sessionId: "session-1",
      content: "hi",
    });
  });

  it("shows a queued prompt as its own message when its Run starts", () => {
    const event: SequencedSocketPayload = {
      type: "started",
      prompt: "then run the tests",
      input_id: "input-1",
      ...base,
      seq: 9,
    };
    const projection = projectRunEvent(event, noContext);

    expect(runTypes(projection)).toEqual([
      "runSequenceAdvanced",
      "runStarted",
      "inputMaterialized",
      "userMessageQueued",
    ]);
    expect(projection.actions[2]).toEqual({
      type: "inputMaterialized",
      sessionId: "session-1",
      inputId: "input-1",
    });
    expect(projection.actions[3]).toEqual({
      type: "userMessageQueued",
      message: {
        id: "run-1:input:input-1",
        session_id: "session-1",
        role: "user",
        text: "then run the tests",
        metadata: { input_id: "input-1", delivery: "queue" },
      },
    });
  });

  it("keeps a prompt that started its own Run out of the input list", () => {
    const event: SequencedSocketPayload = {
      type: "started",
      prompt: "hello",
      ...base,
      seq: 1,
    };
    const projection = projectRunEvent(event, noContext);

    expect(runTypes(projection)).toEqual(["runSequenceAdvanced", "runStarted"]);
  });

  it("inserts a steering message and opens a new agent segment after it", () => {
    const event: SequencedSocketPayload = {
      type: "input_applied",
      input_id: "input-2",
      message_id: "message-9",
      delivery: "steer",
      prompt: "focus on tests",
      step: 2,
      ...base,
      seq: 12,
    };
    const projection = projectRunEvent(event, { agentSegment: 0 });

    expect(runTypes(projection)).toEqual([
      "runSequenceAdvanced",
      "inputMaterialized",
      "userMessageQueued",
    ]);
    expect(projection.actions[2]).toEqual({
      type: "userMessageQueued",
      message: {
        id: "message-9",
        session_id: "session-1",
        role: "user",
        text: "focus on tests",
        metadata: { input_id: "input-2", delivery: "steer" },
      },
    });
    // The continuation must not be appended above the new user message.
    expect(projection.runtime).toEqual({ agentSegmentDelta: 1 });
    expect(projection.effects).toEqual([{ kind: "status", status: "Streaming" }]);
  });

  it("withdraws the follow-ups a failed Run was holding", () => {
    const event: SequencedSocketPayload = {
      type: "error",
      code: "run_failed",
      message: "Agent run failed: RuntimeError",
      cancelled_input_ids: ["input-1", "input-2"],
      ...base,
      seq: 5,
    };
    const projection = projectRunEvent(event, noContext);

    expect(projection.actions[1]).toEqual({
      type: "inputCancelledByRun",
      sessionId: "session-1",
      inputIds: ["input-1", "input-2"],
      reason: "predecessor_failed",
    });
  });

  it("leaves the queue alone when a failure withdrew nothing", () => {
    const event: SequencedSocketPayload = {
      type: "error",
      code: "run_failed",
      message: "Agent run failed: RuntimeError",
      ...base,
      seq: 5,
    };
    const projection = projectRunEvent(event, noContext);

    expect(runTypes(projection)).toEqual([
      "runSequenceAdvanced",
      "streamingFailed",
      "runFinished",
    ]);
  });

  it("advances the agent segment for tool calls and results", () => {
    const toolCall: SequencedSocketPayload = {
      type: "tool_call",
      tool_call_id: "call-1",
      tool: "exec_command",
      arguments: "{}",
      ...base,
      seq: 2,
    };
    const callProjection = projectRunEvent(toolCall, noContext);

    expect(callProjection.runtime).toEqual({ agentSegmentDelta: 1 });
    expect(callProjection.actions[1]).toEqual({
      type: "toolCallStarted",
      sessionId: "session-1",
      payload: toolCall,
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
    });
    expect(callProjection.effects).toEqual([
      { kind: "status", status: "Tool: exec_command" },
    ]);

    const toolResult: SequencedSocketPayload = {
      type: "tool_result",
      tool_call_id: "call-1",
      tool: "exec_command",
      success: true,
      content: "ok",
      ...base,
      seq: 3,
    };
    const resultProjection = projectRunEvent(toolResult, { agentSegment: 1 });

    expect(resultProjection.runtime).toEqual({ agentSegmentDelta: 1 });
    expect(resultProjection.actions[1]).toEqual({
      type: "toolCallCompleted",
      sessionId: "session-1",
      payload: toolResult,
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
    });
    expect(resultProjection.effects).toEqual([
      { kind: "status", status: "Tool complete: exec_command" },
    ]);
  });

  it("falls back to the sequence for missing tool call ids and leaves output deltas out of the segment", () => {
    const toolCall: SequencedSocketPayload = { type: "tool_call", ...base, seq: 9 };
    const callProjection = projectRunEvent(toolCall, noContext);
    expect(callProjection.actions[1]).toMatchObject({
      type: "toolCallStarted",
      messageId: "run-1:tool:tool-9",
      toolCallId: "tool-9",
    });
    expect(callProjection.effects).toEqual([{ kind: "status", status: "Calling tool" }]);

    const output: SequencedSocketPayload = {
      type: "tool_output_delta",
      stream: "stdout",
      content: "line",
      ...base,
      seq: 10,
    };
    const outputProjection = projectRunEvent(output, noContext);
    expect(outputProjection.runtime).toEqual({});
    expect(outputProjection.actions[1]).toMatchObject({
      type: "toolOutputReceived",
      messageId: "run-1:tool:tool-10",
      toolCallId: "tool-10",
    });
  });

  it("formats context compression notes as appended tool messages", () => {
    const event: SequencedSocketPayload = {
      type: "context_compressed",
      scope: "loop",
      compressed_messages: 4,
      ...base,
      seq: 5,
    };
    const projection = projectRunEvent(event, noContext);

    expect(projection.actions[1]).toEqual({
      type: "runEventAppended",
      id: "run-1:context:5",
      sessionId: "session-1",
      text: "上下文已压缩：工具上下文\n已压缩 4 条消息。",
    });
    expect(projection.effects).toEqual([]);
  });

  it("publishes skill events without touching chat state", () => {
    const loaded: SequencedSocketPayload = {
      type: "skills_loaded",
      count: 2,
      enabled_count: 1,
      ...base,
      seq: 2,
    };
    const loadedProjection = projectRunEvent(loaded, noContext);
    expect(runTypes(loadedProjection)).toEqual(["runSequenceAdvanced"]);
    expect(loadedProjection.effects).toEqual([
      { kind: "skillEvent", payload: loaded },
    ]);

    const warning: SequencedSocketPayload = {
      type: "skills_warning",
      message: "skill missing",
      ...base,
      seq: 3,
    };
    const warningProjection = projectRunEvent(warning, noContext);
    expect(warningProjection.effects).toEqual([
      { kind: "skillEvent", payload: warning },
      { kind: "status", status: "skill missing" },
    ]);

    const injected: SequencedSocketPayload = {
      type: "skill_injected",
      name: "demo",
      path: "skills/demo",
      ...base,
      seq: 4,
    };
    expect(projectRunEvent(injected, noContext).effects).toEqual([
      { kind: "skillEvent", payload: injected },
    ]);
  });

  it("projects plan readiness and approval", () => {
    const planReady: SequencedSocketPayload = {
      type: "plan_ready",
      plan_id: "plan-1",
      status: "pending",
      content: "do it",
      ...base,
      seq: 4,
    };
    const readyProjection = projectRunEvent(planReady, noContext);

    expect(readyProjection.runtime).toEqual({ executingPlanId: "plan-1" });
    expect(readyProjection.actions[1]).toEqual({
      type: "planReady",
      messageId: "run-1:plan:plan-1",
      payload: planReady,
    });
    expect(readyProjection.effects).toEqual([{ kind: "status", status: "Plan ready" }]);

    const approved: SequencedSocketPayload = {
      type: "plan_approved",
      plan_id: "plan-1",
      ...base,
      seq: 5,
    };
    const approvedProjection = projectRunEvent(approved, noContext);
    expect(approvedProjection.runtime).toEqual({ executingPlanId: "plan-1" });
    expect(approvedProjection.actions[1]).toEqual({
      type: "planStatusChanged",
      sessionId: "session-1",
      planId: "plan-1",
      status: "executing",
    });
    expect(approvedProjection.effects).toEqual([]);
  });

  it("projects tool approvals", () => {
    const required: SequencedSocketPayload = {
      type: "tool_approval_required",
      approval_id: "approval-1",
      tool_call_id: "call-1",
      tool: "apply_patch",
      risk: "write",
      reason: "writes files",
      summary: "patch",
      preview: {},
      options: ["allow_once", "deny"],
      ...base,
      seq: 6,
    };
    const requiredProjection = projectRunEvent(required, noContext);
    expect(requiredProjection.actions[1]).toEqual({
      type: "approvalRequired",
      approval: required,
    });
    expect(requiredProjection.effects).toEqual([
      { kind: "status", status: "Approval required: apply_patch" },
    ]);

    const resolved: SequencedSocketPayload = {
      type: "tool_approval_resolved",
      approval_id: "approval-1",
      decision: "allow_once",
      ...base,
      seq: 7,
    };
    const resolvedProjection = projectRunEvent(resolved, noContext);
    expect(resolvedProjection.actions[1]).toEqual({
      type: "approvalResolved",
      runId: "run-1",
      approvalId: "approval-1",
    });
    expect(resolvedProjection.effects).toEqual([{ kind: "status", status: "Streaming" }]);
  });

  it("projects cancellation requests", () => {
    const event: SequencedSocketPayload = {
      type: "run_cancel_requested",
      ...base,
      seq: 8,
    };
    const projection = projectRunEvent(event, noContext);

    expect(projection.actions[1]).toEqual({
      type: "runStatusChanged",
      runId: "run-1",
      sessionId: "session-1",
      status: "cancelling",
    });
    expect(projection.effects).toEqual([{ kind: "status", status: "Cancelling" }]);
  });

  it("projects a completed run and finishes the executing plan", () => {
    const event: SequencedSocketPayload = { type: "done", ...base, seq: 12 };
    const projection = projectRunEvent(event, {
      agentSegment: 1,
      executingPlanId: "plan-1",
    });

    expect(runTypes(projection)).toEqual([
      "runSequenceAdvanced",
      "runFinished",
      "planStatusChanged",
    ]);
    expect(projection.actions[1]).toEqual({
      type: "runFinished",
      runId: "run-1",
      sessionId: "session-1",
      status: "completed",
      sequence: 12,
    });
    expect(projection.actions[2]).toEqual({
      type: "planStatusChanged",
      sessionId: "session-1",
      planId: "plan-1",
      status: "executed",
    });
    expect(projection.runtime).toEqual({ terminal: true });
    expect(projection.effects).toEqual([
      { kind: "runDeactivated", sessionId: "session-1" },
      { kind: "status", status: "Ready" },
      { kind: "sessionRefreshRequested", sessionId: "session-1" },
    ]);
  });

  it("projects cancelled and interrupted runs", () => {
    const cancelled: SequencedSocketPayload = { type: "run_cancelled", ...base, seq: 4 };
    const cancelledProjection = projectRunEvent(cancelled, noContext);
    expect(cancelledProjection.actions[1]).toMatchObject({
      type: "runFinished",
      status: "cancelled",
      sequence: 4,
    });
    expect(cancelledProjection.effects).toEqual([
      { kind: "runDeactivated", sessionId: "session-1" },
      { kind: "status", status: "Cancelled" },
      { kind: "sessionRefreshRequested", sessionId: "session-1" },
    ]);

    const interrupted: SequencedSocketPayload = {
      type: "run_interrupted",
      ...base,
      seq: 5,
    };
    const interruptedProjection = projectRunEvent(interrupted, noContext);
    expect(interruptedProjection.actions[1]).toMatchObject({
      type: "runFinished",
      status: "interrupted",
    });
    expect(interruptedProjection.effects).toContainEqual({
      kind: "status",
      status: "Interrupted",
    });
  });

  it("projects a failed run with the backend message", () => {
    const explicit: SequencedSocketPayload = {
      type: "error",
      code: "agent_error",
      message: "boom",
      ...base,
      seq: 6,
    };
    const explicitProjection = projectRunEvent(explicit, {
      agentSegment: 0,
      executingPlanId: "plan-1",
    });

    expect(runTypes(explicitProjection)).toEqual([
      "runSequenceAdvanced",
      "streamingFailed",
      "runFinished",
      "planStatusChanged",
    ]);
    expect(explicitProjection.actions[1]).toEqual({
      type: "streamingFailed",
      messageId: "run-1:error",
      sessionId: "session-1",
      errorText: "boom",
    });
    expect(explicitProjection.actions[2]).toMatchObject({
      type: "runFinished",
      status: "failed",
    });
    expect(explicitProjection.effects).toContainEqual({ kind: "status", status: "boom" });
    expect(explicitProjection.runtime).toEqual({ terminal: true });

    const fallback: SequencedSocketPayload = { type: "error", ...base, seq: 7 };
    const fallbackProjection = projectRunEvent(fallback, noContext);
    expect(fallbackProjection.actions[1]).toMatchObject({
      type: "streamingFailed",
      errorText: "Agent run failed",
    });
    expect(fallbackProjection.effects).toContainEqual({
      kind: "status",
      status: "Agent run failed",
    });
  });
});
