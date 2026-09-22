import { describe, expect, it, vi } from "vitest";
import { SessionRunTracker } from "./sessionRunTracker";

/**
 * Session-scoped run bookkeeping.
 *
 * These rules used to live in three `useRef`s inside the socket hook, so
 * they could only be exercised by rendering React and driving a socket.
 * The behaviours below are the ones that actually matter to the user:
 * a session must not start two Runs, a duplicate approval must not create
 * two execution attempts, and switching sessions must not lose another
 * session's in-flight state.
 */

describe("SessionRunTracker", () => {
  it("starts with no active runs, pending sessions or plan ids", () => {
    const tracker = new SessionRunTracker();

    expect(tracker.snapshot()).toEqual({});
    expect(tracker.isPending("s1")).toBe(false);
    expect(tracker.isBusy("s1")).toBe(false);
  });

  it("tracks the active run per session independently", () => {
    const tracker = new SessionRunTracker();

    tracker.setActiveRun("s1", "run-1");
    tracker.setActiveRun("s2", "run-2");

    expect(tracker.activeRunId("s1")).toBe("run-1");
    expect(tracker.activeRunId("s2")).toBe("run-2");
    expect(tracker.snapshot()).toEqual({ s1: "run-1", s2: "run-2" });
  });

  it("clears a session's run without touching the others", () => {
    const tracker = new SessionRunTracker();
    tracker.setActiveRun("s1", "run-1");
    tracker.setActiveRun("s2", "run-2");

    tracker.setActiveRun("s1");

    expect(tracker.activeRunId("s1")).toBeUndefined();
    expect(tracker.activeRunId("s2")).toBe("run-2");
  });

  it("treats a session with an active run as busy", () => {
    const tracker = new SessionRunTracker();
    tracker.setActiveRun("s1", "run-1");

    expect(tracker.isBusy("s1")).toBe(true);
    expect(tracker.isBusy("s2")).toBe(false);
  });

  it("treats a session with a command in flight as busy", () => {
    const tracker = new SessionRunTracker();

    tracker.markPending("s1");

    expect(tracker.isPending("s1")).toBe(true);
    expect(tracker.isBusy("s1")).toBe(true);
    expect(tracker.snapshot()).toEqual({});
  });

  it("stops reporting busy once the command is acknowledged", () => {
    const tracker = new SessionRunTracker();
    tracker.markPending("s1");

    tracker.clearPending("s1");

    expect(tracker.isBusy("s1")).toBe(false);
  });

  it("notifies subscribers with a snapshot on change", () => {
    const tracker = new SessionRunTracker();
    const listener = vi.fn();
    const unsubscribe = tracker.subscribe(listener);

    // Subscribing delivers the current state immediately.
    expect(listener).toHaveBeenLastCalledWith({});

    tracker.setActiveRun("s1", "run-1");
    expect(listener).toHaveBeenLastCalledWith({ s1: "run-1" });

    tracker.setActiveRun("s1");
    expect(listener).toHaveBeenLastCalledWith({});

    unsubscribe();
    tracker.setActiveRun("s2", "run-2");
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it("does not notify when nothing actually changed", () => {
    const tracker = new SessionRunTracker();
    const listener = vi.fn();
    tracker.subscribe(listener);

    tracker.setActiveRun("s1", "run-1");
    tracker.setActiveRun("s1", "run-1");
    tracker.setActiveRun("s1");

    expect(listener).toHaveBeenCalledTimes(3);
  });

  it("reuses a plan's request id so a retry stays idempotent", () => {
    const tracker = new SessionRunTracker();
    const create = vi.fn(() => "generated-1");

    const first = tracker.requestIdForPlan("plan-1", create);
    const second = tracker.requestIdForPlan("plan-1", create);

    expect(first).toBe("generated-1");
    expect(second).toBe("generated-1");
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("gives different plans different request ids", () => {
    const tracker = new SessionRunTracker();
    let counter = 0;
    const create = () => `id-${++counter}`;

    expect(tracker.requestIdForPlan("plan-1", create)).toBe("id-1");
    expect(tracker.requestIdForPlan("plan-2", create)).toBe("id-2");
  });

  it("releases a plan's request id once the backend has answered", () => {
    const tracker = new SessionRunTracker();
    const create = vi.fn(() => "generated-1");

    tracker.requestIdForPlan("plan-1", create);
    tracker.releasePlan("plan-1");

    expect(tracker.requestIdForPlan("plan-1", create)).toBe("generated-1");
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("reports whether any of the given sessions is busy", () => {
    const tracker = new SessionRunTracker();
    tracker.setActiveRun("s2", "run-2");

    expect(tracker.hasBusySession(["s1", "s2"])).toBe(true);
    expect(tracker.hasBusySession(["s1", "s3"])).toBe(false);
    expect(tracker.hasBusySession([])).toBe(false);
  });

  it("returns a snapshot that cannot mutate internal state", () => {
    const tracker = new SessionRunTracker();
    tracker.setActiveRun("s1", "run-1");

    const snapshot = tracker.snapshot();
    snapshot.s1 = "tampered";
    delete snapshot.s1;

    expect(tracker.activeRunId("s1")).toBe("run-1");
  });

  it("maps a steering request back to the queued input it replaces", () => {
    const tracker = new SessionRunTracker();

    tracker.beginSteer("steer-1", "input-1");

    expect(tracker.takeSteer("steer-1")).toBe("input-1");
  });

  it("consumes a steering request only once", () => {
    const tracker = new SessionRunTracker();
    tracker.beginSteer("steer-1", "input-1");

    tracker.takeSteer("steer-1");

    // A second ack for the same request must not withdraw anything again.
    expect(tracker.takeSteer("steer-1")).toBeUndefined();
  });

  it("has no steering target for an unknown request", () => {
    const tracker = new SessionRunTracker();

    expect(tracker.takeSteer("steer-unknown")).toBeUndefined();
  });

  it("forgets in-flight command guards when the socket is new", () => {
    const tracker = new SessionRunTracker();
    tracker.markPending("s1");
    tracker.markPending("s2");
    tracker.setActiveRun("s1", "run-1");

    tracker.clearPendingSessions();

    expect(tracker.isPending("s1")).toBe(false);
    expect(tracker.isPending("s2")).toBe(false);
    // The active Run is backend truth, not a local guard.
    expect(tracker.activeRunId("s1")).toBe("run-1");
  });
});
