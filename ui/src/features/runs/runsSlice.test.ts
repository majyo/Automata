import { describe, expect, it } from "vitest";
import type { RunClientState } from "../../state/chatTypes";
import { reduceRuns } from "./runsSlice";
import type { RunsSliceState } from "./runsSlice";

const empty: RunsSliceState = { runsById: {}, activeRunIdBySession: {} };

function runState(overrides: Partial<RunClientState>): RunClientState {
  return {
    runId: "run-1",
    sessionId: "session-1",
    status: "running",
    lastSequence: 1,
    isReplaying: false,
    ...overrides,
  };
}

describe("reduceRuns", () => {
  it("activates a discovered non-terminal run", () => {
    const updated = reduceRuns(empty, {
      type: "runDiscovered",
      runId: "run-1",
      sessionId: "session-1",
      status: "queued",
      lastSequence: 0,
    })!;

    expect(updated.runsById["run-1"]).toEqual(
      runState({ status: "queued", lastSequence: 0 }),
    );
    expect(updated.activeRunIdBySession).toEqual({ "session-1": "run-1" });
  });

  it("does not activate a discovered terminal run", () => {
    const updated = reduceRuns(empty, {
      type: "runDiscovered",
      runId: "run-1",
      sessionId: "session-1",
      status: "completed",
      lastSequence: 4,
    })!;

    expect(updated.runsById["run-1"]).toEqual(
      runState({ status: "completed", lastSequence: 4 }),
    );
    expect(updated.activeRunIdBySession).toEqual({});
  });

  it("keeps the highest known sequence and replay flag on rediscovery", () => {
    const state: RunsSliceState = {
      runsById: { "run-1": runState({ lastSequence: 9, isReplaying: true }) },
      activeRunIdBySession: {},
    };

    const updated = reduceRuns(state, {
      type: "runDiscovered",
      runId: "run-1",
      sessionId: "session-1",
      status: "running",
      lastSequence: 3,
    })!;

    expect(updated.runsById["run-1"]).toMatchObject({
      lastSequence: 9,
      isReplaying: true,
    });
  });

  it("starts and advances a run", () => {
    const started = reduceRuns(empty, {
      type: "runStarted",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 1,
    })!;
    expect(started.runsById["run-1"]).toEqual(runState({ lastSequence: 1 }));

    const discovered = reduceRuns(empty, {
      type: "runDiscovered",
      runId: "run-1",
      sessionId: "session-1",
      status: "queued",
      lastSequence: 1,
    })!;
    const advanced = reduceRuns(discovered, {
      type: "runSequenceAdvanced",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 5,
    })!;
    expect(advanced.runsById["run-1"]).toMatchObject({
      status: "queued",
      lastSequence: 5,
    });

    const stale = reduceRuns(advanced, {
      type: "runSequenceAdvanced",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 2,
    })!;
    expect(stale.runsById["run-1"].lastSequence).toBe(5);
  });

  it("creates an unknown run through a sequence advance", () => {
    const updated = reduceRuns(empty, {
      type: "runSequenceAdvanced",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 2,
    })!;

    expect(updated.runsById["run-1"]).toEqual(runState({ lastSequence: 2 }));
  });

  it("only toggles replay for runs it already knows", () => {
    expect(
      reduceRuns(empty, { type: "runReplayChanged", runId: "run-1", replaying: true }),
    ).toBeNull();

    const state: RunsSliceState = {
      runsById: { "run-1": runState({}) },
      activeRunIdBySession: {},
    };
    const updated = reduceRuns(state, {
      type: "runReplayChanged",
      runId: "run-1",
      replaying: true,
    })!;
    expect(updated.runsById["run-1"].isReplaying).toBe(true);
  });

  it("applies an explicit status change", () => {
    const state: RunsSliceState = {
      runsById: { "run-1": runState({ lastSequence: 7 }) },
      activeRunIdBySession: { "session-1": "run-1" },
    };

    const updated = reduceRuns(state, {
      type: "runStatusChanged",
      runId: "run-1",
      sessionId: "session-1",
      status: "cancelling",
    })!;

    expect(updated.runsById["run-1"]).toMatchObject({
      status: "cancelling",
      lastSequence: 7,
    });
    expect(updated.activeRunIdBySession).toEqual({ "session-1": "run-1" });
  });

  it("reflects approval waits and resolutions", () => {
    const waiting = reduceRuns(empty, {
      type: "approvalRequired",
      approval: {
        approval_id: "approval-1",
        run_id: "run-1",
        session_id: "session-1",
        tool_call_id: "call-1",
        tool: "apply_patch",
        risk: "write",
        reason: "writes",
        summary: "patch",
        preview: {},
        options: ["allow_once"],
      },
    })!;
    expect(waiting.runsById["run-1"]).toMatchObject({
      status: "waiting_approval",
      sessionId: "session-1",
    });

    const resumed = reduceRuns(waiting, {
      type: "approvalResolved",
      runId: "run-1",
      approvalId: "approval-1",
    })!;
    expect(resumed.runsById["run-1"].status).toBe("running");

    expect(
      reduceRuns(empty, {
        type: "approvalResolved",
        runId: "run-1",
        approvalId: "approval-1",
      }),
    ).toBeNull();
  });

  it("finishes a run, clears its active slot and keeps the last sequence", () => {
    const state: RunsSliceState = {
      runsById: { "run-1": runState({ lastSequence: 6, isReplaying: true }) },
      activeRunIdBySession: { "session-1": "run-1" },
    };

    const finished = reduceRuns(state, {
      type: "runFinished",
      runId: "run-1",
      sessionId: "session-1",
      status: "completed",
      sequence: 4,
    })!;

    expect(finished.runsById["run-1"]).toEqual(
      runState({ status: "completed", lastSequence: 6, isReplaying: false }),
    );
    expect(finished.activeRunIdBySession).toEqual({});

    const withoutSequence = reduceRuns(empty, {
      type: "runFinished",
      runId: "run-1",
      sessionId: "session-1",
      status: "cancelled",
    })!;
    expect(withoutSequence.runsById["run-1"].lastSequence).toBe(0);
  });

  it("ignores actions owned by other slices", () => {
    expect(
      reduceRuns(empty, {
        type: "tokenReceived",
        messageId: "run-1:agent:0",
        sessionId: "session-1",
        content: "hi",
      }),
    ).toBeNull();
    expect(
      reduceRuns(empty, {
        type: "planStatusChanged",
        sessionId: "session-1",
        planId: "plan-1",
        status: "executing",
      }),
    ).toBeNull();
  });
});
