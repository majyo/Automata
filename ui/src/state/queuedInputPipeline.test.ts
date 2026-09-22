import { describe, expect, it } from "vitest";
import { RunStreamController } from "../features/runs/runStreamController";
import type { ChatMessage } from "../types/chat";
import type { SequencedSocketPayload } from "../types/socket";
import { chatReducer, initialChatState, selectMessages } from "./chatReducer";
import type { ChatState } from "./chatReducer";

/**
 * The transcript a real session produces when a prompt is queued while a Run
 * is active. The frame order below is what the backend actually emits
 * (`api/tests/test_agent_input_delivery.py` and a live trace agree on it):
 *
 *   done(run-1)  ->  [queued input materializes: user message persisted]
 *                ->  started(run-2, input_id)
 *
 * and the client reloads the session as soon as it sees `done`, so the reload
 * response often already contains the queued user message. Both orders of the
 * reload and the `started` frame must leave exactly one copy of it.
 */
const base = { session_id: "session-1", schema_version: 1 };

const FIRST_USER: ChatMessage = {
  id: "message-user-1",
  session_id: "session-1",
  role: "user",
  text: "first",
  sequence: 1,
};
const FIRST_ANSWER: ChatMessage = {
  id: "message-agent-1",
  session_id: "session-1",
  role: "agent",
  text: "first answer",
  sequence: 2,
};
const SECOND_USER: ChatMessage = {
  id: "message-user-2",
  session_id: "session-1",
  role: "user",
  text: "second",
  sequence: 3,
  metadata: { input_id: "input-1", delivery: "queue" },
};
const SECOND_ANSWER: ChatMessage = {
  id: "message-agent-2",
  session_id: "session-1",
  role: "agent",
  text: "second answer",
  sequence: 4,
};

function createPipeline() {
  let state: ChatState = initialChatState;

  const controller = new RunStreamController({
    onRunEvent: (projection) => {
      for (const action of projection.actions) {
        state = chatReducer(state, action);
      }
    },
    onResumeRequested: () => undefined,
  });

  return {
    accept: (event: SequencedSocketPayload) => controller.acceptEvent(event),
    dispatch: (action: Parameters<typeof chatReducer>[1]) => {
      state = chatReducer(state, action);
    },
    /** The session load the hook issues at boot, on a switch, or on `done`. */
    load: (messages: ChatMessage[]) => {
      state = chatReducer(state, {
        type: "messagesLoaded",
        sessionId: "session-1",
        messages,
      });
    },
    /** What the user submitted while Run 1 was streaming. */
    submitQueued: () => {
      state = chatReducer(state, {
        type: "inputSubmitted",
        sessionId: "session-1",
        requestId: "queue-request-1",
        prompt: "second",
        delivery: "queue",
        runId: "run-1",
      });
      state = chatReducer(state, {
        type: "inputAccepted",
        sessionId: "session-1",
        requestId: "queue-request-1",
        inputId: "input-1",
        position: 1,
      });
    },
    state: () => state,
    texts: (role?: ChatMessage["role"]) =>
      selectMessages(state, "session-1")
        .filter((message) => (role ? message.role === role : true))
        .map((message) => message.text),
  };
}

function startFirstRun(pipeline: ReturnType<typeof createPipeline>) {
  pipeline.load([FIRST_USER]);
  pipeline.accept({ type: "started", prompt: "first", run_id: "run-1", ...base, seq: 1 });
  pipeline.accept({ type: "token", content: "first answer", run_id: "run-1", ...base, seq: 2 });
}

function queueWhileFirstRunStreams(pipeline: ReturnType<typeof createPipeline>) {
  pipeline.submitQueued();
}

function finishFirstRun(pipeline: ReturnType<typeof createPipeline>) {
  pipeline.accept({ type: "done", run_id: "run-1", ...base, seq: 3 });
}

function startSecondRun(pipeline: ReturnType<typeof createPipeline>) {
  pipeline.accept({
    type: "started",
    prompt: "second",
    input_id: "input-1",
    run_id: "run-2",
    ...base,
    seq: 1,
  });
}

describe("queued input pipeline", () => {
  it("keeps the queued prompt out of the transcript while its Run streams", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);

    expect(pipeline.state().inputsBySession["session-1"]).toHaveLength(1);
    expect(pipeline.texts("user")).toEqual(["first"]);
  });

  it("keeps a waiting message across a session load while its Run streams", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);

    // A session switch reloads the history while Run 1 is still streaming.
    pipeline.load([FIRST_USER]);

    expect(pipeline.state().inputsBySession["session-1"]).toHaveLength(1);
    expect(pipeline.texts()).toEqual(["first", "first answer"]);
  });

  it("shows the queued prompt once when the reload wins the race", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);
    finishFirstRun(pipeline);

    // The reload response already contains the materialized queued message.
    pipeline.load([FIRST_USER, FIRST_ANSWER, SECOND_USER]);
    expect(pipeline.texts("user")).toEqual(["first", "second"]);
    expect(pipeline.state().inputsBySession["session-1"]).toEqual([]);

    // ...and the Run that materialized it starts right afterwards.
    startSecondRun(pipeline);

    expect(pipeline.texts("user")).toEqual(["first", "second"]);
    expect(pipeline.texts("agent")).toEqual(["first answer"]);
  });

  it("shows the queued prompt once when the Run start wins the race", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);
    finishFirstRun(pipeline);

    startSecondRun(pipeline);
    expect(pipeline.texts("user")).toEqual(["first", "second"]);

    pipeline.load([FIRST_USER, FIRST_ANSWER, SECOND_USER]);
    expect(pipeline.texts("user")).toEqual(["first", "second"]);
  });

  it("does not drop what the successor Run is streaming", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);
    finishFirstRun(pipeline);
    startSecondRun(pipeline);
    pipeline.accept({
      type: "token",
      content: "second ans",
      run_id: "run-2",
      ...base,
      seq: 2,
    });

    // The reload for Run 1 lands while Run 2 is still streaming.
    pipeline.load([FIRST_USER, FIRST_ANSWER, SECOND_USER]);

    expect(pipeline.texts("agent")).toEqual(["first answer", "second ans"]);

    pipeline.accept({
      type: "token",
      content: "wer",
      run_id: "run-2",
      ...base,
      seq: 3,
    });
    pipeline.accept({ type: "done", run_id: "run-2", ...base, seq: 4 });
    pipeline.load([FIRST_USER, FIRST_ANSWER, SECOND_USER, SECOND_ANSWER]);

    expect(pipeline.texts()).toEqual([
      "first",
      "first answer",
      "second",
      "second answer",
    ]);
  });

  it("keeps exactly one copy across a session switch during the queued Run", () => {
    const pipeline = createPipeline();
    startFirstRun(pipeline);
    queueWhileFirstRunStreams(pipeline);
    finishFirstRun(pipeline);
    startSecondRun(pipeline);
    pipeline.accept({
      type: "token",
      content: "second ans",
      run_id: "run-2",
      ...base,
      seq: 2,
    });

    pipeline.load([FIRST_USER, FIRST_ANSWER, SECOND_USER]);

    expect(pipeline.texts("user")).toEqual(["first", "second"]);
    expect(pipeline.texts("agent")).toEqual(["first answer", "second ans"]);
  });
});
