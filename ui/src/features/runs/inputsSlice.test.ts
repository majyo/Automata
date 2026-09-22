import { describe, expect, it } from "vitest";
import { reduceInputs } from "./inputsSlice";
import type { InputsSliceState } from "./inputsSlice";
import type { PendingInput } from "../../types/chat";

const empty: InputsSliceState = { inputsBySession: {} };

function entry(overrides: Partial<PendingInput> = {}): PendingInput {
  return {
    requestId: "request-1",
    sessionId: "session-1",
    prompt: "then run the tests",
    delivery: "queue",
    status: "pending",
    inputId: "input-1",
    ...overrides,
  };
}

function state(inputs: PendingInput[]): InputsSliceState {
  return { inputsBySession: { "session-1": inputs } };
}

describe("reduceInputs", () => {
  it("records a queued prompt before the backend acknowledges it", () => {
    const updated = reduceInputs(empty, {
      type: "inputSubmitted",
      sessionId: "session-1",
      requestId: "request-1",
      prompt: "then run the tests",
      delivery: "queue",
      runId: "run-1",
    })!;

    expect(updated.inputsBySession["session-1"]).toEqual([
      {
        requestId: "request-1",
        sessionId: "session-1",
        prompt: "then run the tests",
        delivery: "queue",
        status: "sending",
        runId: "run-1",
      },
    ]);
  });

  it("settles the entry with the id, position and Run from input_accepted", () => {
    const updated = reduceInputs(
      state([entry({ status: "sending", inputId: undefined })]),
      {
        type: "inputAccepted",
        sessionId: "session-1",
        requestId: "request-1",
        inputId: "input-1",
        position: 4,
        runId: "run-1",
      },
    )!;

    expect(updated.inputsBySession["session-1"][0]).toMatchObject({
      status: "pending",
      inputId: "input-1",
      position: 4,
      runId: "run-1",
    });
  });

  it("ignores an acknowledgement for an input it does not track", () => {
    const updated = reduceInputs(state([entry()]), {
      type: "inputAccepted",
      sessionId: "session-1",
      requestId: "unknown-request",
      inputId: "input-9",
    });

    expect(updated).toBeNull();
  });

  it("marks an entry as cancelling without dropping it before the ack", () => {
    const updated = reduceInputs(state([entry()]), {
      type: "inputCancelling",
      sessionId: "session-1",
      inputId: "input-1",
    })!;

    expect(updated.inputsBySession["session-1"][0].status).toBe("cancelling");
  });

  it("keeps a withdrawn message with its text so it can be queued again", () => {
    const updated = reduceInputs(
      state([entry(), entry({ requestId: "r2", inputId: "input-2" })]),
      {
        type: "inputCancelledByRun",
        sessionId: "session-1",
        inputIds: ["input-1"],
        reason: "predecessor_failed",
      },
    )!;

    expect(updated.inputsBySession["session-1"]).toEqual([
      expect.objectContaining({
        inputId: "input-1",
        prompt: "then run the tests",
        status: "cancelled",
        cancelReason: "predecessor_failed",
      }),
      expect.objectContaining({ inputId: "input-2", status: "pending" }),
    ]);
  });

  it("returns a withdrawn entry to waiting under a fresh request id", () => {
    const withdrawn = reduceInputs(state([entry()]), {
      type: "inputCancelledByRun",
      sessionId: "session-1",
      inputIds: ["input-1"],
      reason: "predecessor_failed",
    })!;
    const requeued = reduceInputs(withdrawn, {
      type: "inputRequeued",
      sessionId: "session-1",
      requestId: "request-1",
      nextRequestId: "request-2",
    })!;

    expect(requeued.inputsBySession["session-1"]).toEqual([
      {
        requestId: "request-2",
        sessionId: "session-1",
        prompt: "then run the tests",
        delivery: "queue",
        status: "sending",
        inputId: undefined,
        position: null,
        runId: null,
        cancelReason: undefined,
      },
    ]);
  });

  it("drops an entry once the backend confirms the withdrawal", () => {
    const updated = reduceInputs(state([entry()]), {
      type: "inputCancelled",
      sessionId: "session-1",
      inputId: "input-1",
    })!;

    expect(updated.inputsBySession["session-1"]).toEqual([]);
  });

  it("drops a queued entry when its Run starts", () => {
    const updated = reduceInputs(state([entry()]), {
      type: "inputMaterialized",
      sessionId: "session-1",
      inputId: "input-1",
    })!;

    expect(updated.inputsBySession["session-1"]).toEqual([]);
  });

  it("tracks a steering attempt so the queued copy is not withdrawn twice", () => {
    const steering = reduceInputs(state([entry()]), {
      type: "inputSteerRequested",
      sessionId: "session-1",
      inputId: "input-1",
      requestId: "steer-1",
    })!;

    expect(steering.inputsBySession["session-1"][0].steerRequestId).toBe("steer-1");

    const refused = reduceInputs(steering, {
      type: "inputSteerFailed",
      sessionId: "session-1",
      requestId: "steer-1",
    })!;

    expect(refused.inputsBySession["session-1"][0].steerRequestId).toBeUndefined();
    expect(refused.inputsBySession["session-1"][0].inputId).toBe("input-1");
  });

  it("removes a rejected input by request id or input id", () => {
    const byRequest = reduceInputs(state([entry()]), {
      type: "inputFailed",
      sessionId: "session-1",
      requestId: "request-1",
    })!;
    expect(byRequest.inputsBySession["session-1"]).toEqual([]);

    const byInput = reduceInputs(state([entry()]), {
      type: "inputFailed",
      sessionId: "session-1",
      inputId: "input-1",
    })!;
    expect(byInput.inputsBySession["session-1"]).toEqual([]);
  });

  it("clears the queue when the session is deleted", () => {
    const updated = reduceInputs(state([entry(), entry({ requestId: "request-2" })]), {
      type: "sessionMessagesCleared",
      sessionId: "session-1",
    })!;

    expect(updated.inputsBySession["session-1"]).toBeUndefined();
  });

  it("drops a waiting input once its message is persisted", () => {
    const updated = reduceInputs(state([entry(), entry({ requestId: "request-2", inputId: "input-2" })]), {
      type: "messagesLoaded",
      sessionId: "session-1",
      messages: [
        {
          id: "message-1",
          session_id: "session-1",
          role: "user",
          text: "then run the tests",
          metadata: { input_id: "input-1", delivery: "queue" },
        },
      ],
    })!;

    expect(updated.inputsBySession["session-1"]).toEqual([
      expect.objectContaining({ inputId: "input-2" }),
    ]);
  });

  it("ignores loaded messages that carry no input metadata", () => {
    const updated = reduceInputs(state([entry()]), {
      type: "messagesLoaded",
      sessionId: "session-1",
      messages: [
        {
          id: "message-1",
          session_id: "session-1",
          role: "user",
          text: "start",
        },
      ],
    });

    expect(updated).toBeNull();
  });

  it("ignores actions owned by other slices", () => {
    expect(
      reduceInputs(empty, {
        type: "tokenReceived",
        messageId: "run-1:agent:0",
        sessionId: "session-1",
        content: "hi",
      }),
    ).toBeNull();
    expect(
      reduceInputs(empty, {
        type: "runStarted",
        runId: "run-1",
        sessionId: "session-1",
        sequence: 1,
      }),
    ).toBeNull();
  });
});
