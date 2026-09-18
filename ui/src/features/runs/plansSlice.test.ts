import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../../types/chat";
import { reducePlans } from "./plansSlice";
import type { PlansSliceState } from "./plansSlice";

const empty: PlansSliceState = { messagesBySession: {} };

function planMessage(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: "run-1:plan:plan-1",
    session_id: "session-1",
    role: "agent",
    text: "do it",
    kind: "plan",
    plan_id: "plan-1",
    plan_status: "pending",
    ...overrides,
  };
}

describe("reducePlans", () => {
  it("creates a plan card from plan_ready", () => {
    const updated = reducePlans(empty, {
      type: "planReady",
      messageId: "run-1:plan:plan-1",
      payload: {
        type: "plan_ready",
        session_id: "session-1",
        run_id: "run-1",
        seq: 2,
        schema_version: 1,
        plan_id: "plan-1",
        status: "pending",
        content: "do it",
      },
    })!;

    expect(updated.messagesBySession["session-1"]).toEqual([
      {
        id: "run-1:plan:plan-1",
        session_id: "session-1",
        role: "agent",
        text: "do it",
        kind: "plan",
        plan_id: "plan-1",
        plan_status: "pending",
      },
    ]);
  });

  it("replaces an existing plan card without duplicating it", () => {
    const state: PlansSliceState = {
      messagesBySession: {
        "session-1": [planMessage({ text: "stale", plan_status: "approving" })],
      },
    };

    const updated = reducePlans(state, {
      type: "planReady",
      messageId: "run-1:plan:plan-1",
      payload: {
        type: "plan_ready",
        session_id: "session-1",
        run_id: "run-1",
        seq: 3,
        schema_version: 1,
        plan_id: "plan-1",
        status: "pending",
        content: "do it",
      },
    })!;

    expect(updated.messagesBySession["session-1"]).toEqual([
      planMessage({ text: "do it" }),
    ]);
  });

  it("applies status changes to every message of that plan", () => {
    const state: PlansSliceState = {
      messagesBySession: {
        "session-1": [
          planMessage({}),
          planMessage({ id: "other", plan_id: "plan-2" }),
        ],
      },
    };

    const updated = reducePlans(state, {
      type: "planStatusChanged",
      sessionId: "session-1",
      planId: "plan-1",
      status: "executing",
    })!;

    expect(updated.messagesBySession["session-1"][0].plan_status).toBe("executing");
    expect(updated.messagesBySession["session-1"][1].plan_status).toBe("pending");
  });

  it("marks the addressed plan as failed", () => {
    const state: PlansSliceState = {
      messagesBySession: {
        "session-1": [
          planMessage({ plan_status: "executing" }),
          planMessage({ id: "other", plan_id: "plan-2", plan_status: "executing" }),
        ],
      },
    };

    const updated = reducePlans(state, {
      type: "currentPlanError",
      sessionId: "session-1",
      planId: "plan-1",
    })!;

    expect(updated.messagesBySession["session-1"][0].plan_status).toBe("failed");
    expect(updated.messagesBySession["session-1"][1].plan_status).toBe("executing");
  });

  it("falls back to the active plan when no plan id is given", () => {
    const state: PlansSliceState = {
      messagesBySession: {
        "session-1": [
          planMessage({ plan_status: "executing" }),
          planMessage({ id: "failed", plan_id: "plan-2", plan_status: "failed" }),
          planMessage({ id: "done", plan_id: "plan-3", plan_status: "executed" }),
        ],
      },
    };

    const updated = reducePlans(state, {
      type: "currentPlanError",
      sessionId: "session-1",
      planId: null,
    })!;

    expect(updated.messagesBySession["session-1"][0].plan_status).toBe("failed");
    expect(updated.messagesBySession["session-1"][1].plan_status).toBe("failed");
    expect(updated.messagesBySession["session-1"][2].plan_status).toBe("executed");
  });

  it("ignores actions owned by other slices", () => {
    expect(
      reducePlans(empty, {
        type: "tokenReceived",
        messageId: "run-1:agent:0",
        sessionId: "session-1",
        content: "hi",
      }),
    ).toBeNull();
    expect(
      reducePlans(empty, {
        type: "runStarted",
        runId: "run-1",
        sessionId: "session-1",
        sequence: 1,
      }),
    ).toBeNull();
  });
});
