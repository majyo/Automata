import { describe, expect, it } from "vitest";
import type { SequencedSocketPayload } from "../../types/socket";
import { RunStreamController } from "./runStreamController";
import type { RunResumeRequest } from "./runStreamController";
import type { RunProjection } from "./projection";

const base = {
  session_id: "session-1",
  run_id: "run-1",
  schema_version: 1,
};

function createController() {
  const projections: RunProjection[] = [];
  const resumes: RunResumeRequest[] = [];
  const controller = new RunStreamController({
    onRunEvent: (projection) => projections.push(projection),
    onResumeRequested: (request) => resumes.push(request),
  });
  return { controller, projections, resumes };
}

describe("RunStreamController", () => {
  it("accepts sequential events and advances the cursor", () => {
    const { controller, projections } = createController();

    const first: SequencedSocketPayload = { type: "started", prompt: "hi", ...base, seq: 1 };
    const second: SequencedSocketPayload = { type: "token", content: "a", ...base, seq: 2 };

    expect(controller.acceptEvent(first)).not.toBeNull();
    expect(controller.acceptEvent(second)).not.toBeNull();

    expect(projections).toHaveLength(2);
    expect(controller.runtimeFor("run-1", "session-1").lastSequence).toBe(2);
  });

  it("drops duplicate and out-of-order events", () => {
    const { controller, projections } = createController();
    const first: SequencedSocketPayload = { type: "token", content: "a", ...base, seq: 1 };
    controller.acceptEvent(first);

    const duplicate: SequencedSocketPayload = { type: "token", content: "b", ...base, seq: 1 };
    const stale: SequencedSocketPayload = { type: "token", content: "c", ...base, seq: 0 };

    expect(controller.acceptEvent(duplicate)).toBeNull();
    expect(controller.acceptEvent(stale)).toBeNull();
    expect(projections).toHaveLength(1);
    expect(controller.runtimeFor("run-1", "session-1").lastSequence).toBe(1);
  });

  it("requests a resume when a gap is detected", () => {
    const { controller, projections, resumes } = createController();
    controller.acceptEvent({ type: "token", content: "a", ...base, seq: 1 });

    const gap: SequencedSocketPayload = { type: "token", content: "c", ...base, seq: 3 };
    expect(controller.acceptEvent(gap)).toBeNull();

    expect(projections).toHaveLength(1);
    expect(resumes).toEqual([
      { runId: "run-1", sessionId: "session-1", afterSequence: 1 },
    ]);
    const runtime = controller.runtimeFor("run-1", "session-1");
    expect(runtime.replaying).toBe(true);
    expect(runtime.lastSequence).toBe(1);
  });

  it("tracks the agent segment across tool calls so tokens keep their message id", () => {
    const { controller, projections } = createController();
    controller.acceptEvent({ type: "started", prompt: "hi", ...base, seq: 1 });
    controller.acceptEvent({
      type: "tool_call",
      tool_call_id: "call-1",
      tool: "exec_command",
      ...base,
      seq: 2,
    });
    controller.acceptEvent({ type: "token", content: "after tool", ...base, seq: 3 });

    const tokenAction = projections[2].actions[1];
    expect(tokenAction).toEqual({
      type: "tokenReceived",
      messageId: "run-1:agent:1",
      sessionId: "session-1",
      content: "after tool",
    });
  });

  it("resets the agent segment when a run starts", () => {
    const { controller } = createController();
    const runtime = controller.runtimeFor("run-1", "session-1");
    runtime.agentSegment = 4;

    controller.acceptEvent({ type: "started", prompt: "hi", ...base, seq: 1 });

    expect(runtime.agentSegment).toBe(0);
  });

  it("remembers the executing plan and marks terminal runs", () => {
    const { controller, projections } = createController();
    controller.acceptEvent({
      type: "plan_ready",
      plan_id: "plan-1",
      status: "pending",
      content: "do it",
      ...base,
      seq: 1,
    });
    controller.acceptEvent({ type: "done", ...base, seq: 2 });

    const runtime = controller.runtimeFor("run-1", "session-1");
    expect(runtime.executingPlanId).toBe("plan-1");
    expect(runtime.terminal).toBe(true);
    expect(projections[1].actions).toContainEqual({
      type: "planStatusChanged",
      sessionId: "session-1",
      planId: "plan-1",
      status: "executed",
    });
  });

  it("marks failed runs terminal", () => {
    const { controller } = createController();
    controller.acceptEvent({ type: "error", ...base, seq: 1 });

    expect(controller.runtimeFor("run-1", "session-1").terminal).toBe(true);
  });

  it("keeps replay bookkeeping monotonic", () => {
    const { controller } = createController();
    const runtime = controller.runtimeFor("run-1", "session-1");
    runtime.lastSequence = 5;

    controller.setReplaying("run-1", "session-1", true);
    expect(runtime.replaying).toBe(true);

    controller.completeReplay("run-1", "session-1", 3);
    expect(runtime.replaying).toBe(false);
    expect(runtime.lastSequence).toBe(5);

    controller.completeReplay("run-1", "session-1", 9);
    expect(runtime.lastSequence).toBe(9);
  });

  it("creates runtimes on demand for discovered runs", () => {
    const { controller } = createController();

    expect(controller.runtimeIfKnown("run-2")).toBeUndefined();
    const runtime = controller.runtimeFor("run-2", "session-2");
    expect(runtime).toEqual({
      runId: "run-2",
      sessionId: "session-2",
      lastSequence: 0,
      agentSegment: 0,
      replaying: false,
      terminal: false,
    });
    expect(controller.runtimeIfKnown("run-2")).toBe(runtime);
  });
});
