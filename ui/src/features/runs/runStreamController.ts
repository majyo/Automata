import type { SequencedSocketPayload } from "../../types/socket";
import { projectRunEvent } from "./projection";
import type { RunProjection, RunRuntimeEffect } from "./projection";

export type RunRuntime = {
  runId: string;
  sessionId: string;
  lastSequence: number;
  agentSegment: number;
  executingPlanId?: string;
  replaying: boolean;
  terminal: boolean;
};

export type RunResumeRequest = {
  runId: string;
  sessionId: string;
  afterSequence: number;
};

export type RunStreamControllerHandlers = {
  /** A gap-free, non-duplicate event was accepted and projected. */
  onRunEvent(projection: RunProjection, runtime: RunRuntime): void;
  /** The stream found a gap (or replay was requested) and needs a re-fetch. */
  onResumeRequested(request: RunResumeRequest): void;
};

/**
 * Owns per-run stream cursors: last accepted sequence, agent segment, replay
 * state, executing plan and terminal detection. It never touches React or the
 * socket; consumers receive projections and resume requests through callbacks.
 */
export class RunStreamController {
  private readonly runtimes: Record<string, RunRuntime> = {};
  private readonly handlers: RunStreamControllerHandlers;

  constructor(handlers: RunStreamControllerHandlers) {
    this.handlers = handlers;
  }

  runtimeFor(runId: string, sessionId: string): RunRuntime {
    const current = this.runtimes[runId];
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
    this.runtimes[runId] = runtime;
    return runtime;
  }

  runtimeIfKnown(runId: string): RunRuntime | undefined {
    return this.runtimes[runId];
  }

  /**
   * Accepts a sequenced run event. Duplicates are dropped, gaps trigger a
   * resume request and accepted events are projected through onRunEvent.
   * Returns the projection when the event advanced the cursor.
   */
  acceptEvent(event: SequencedSocketPayload): RunProjection | null {
    const runtime = this.runtimeFor(event.run_id, event.session_id);
    if (event.seq <= runtime.lastSequence) {
      return null;
    }
    if (event.seq > runtime.lastSequence + 1) {
      this.requestResume(event.run_id, event.session_id, runtime.lastSequence);
      return null;
    }

    runtime.lastSequence = event.seq;
    const projection = projectRunEvent(event, {
      agentSegment: runtime.agentSegment,
      executingPlanId: runtime.executingPlanId,
    });
    applyRunRuntimeEffect(runtime, projection.runtime);

    this.handlers.onRunEvent(projection, runtime);
    return projection;
  }

  requestResume(
    runId: string,
    sessionId: string,
    afterSequence: number,
  ): RunRuntime {
    const runtime = this.runtimeFor(runId, sessionId);
    runtime.replaying = true;
    this.handlers.onResumeRequested({ runId, sessionId, afterSequence });
    return runtime;
  }

  setReplaying(runId: string, sessionId: string, replaying: boolean): RunRuntime {
    const runtime = this.runtimeFor(runId, sessionId);
    runtime.replaying = replaying;
    return runtime;
  }

  completeReplay(
    runId: string,
    sessionId: string,
    lastSequence: number,
  ): RunRuntime {
    const runtime = this.runtimeFor(runId, sessionId);
    runtime.replaying = false;
    runtime.lastSequence = Math.max(runtime.lastSequence, lastSequence);
    return runtime;
  }

  setExecutingPlanId(
    runId: string,
    sessionId: string,
    planId: string,
  ): RunRuntime {
    const runtime = this.runtimeFor(runId, sessionId);
    runtime.executingPlanId = planId;
    return runtime;
  }
}

export function applyRunRuntimeEffect(
  runtime: RunRuntime,
  effect: RunRuntimeEffect,
): void {
  if (effect.agentSegmentReset) {
    runtime.agentSegment = 0;
  }
  if (typeof effect.agentSegmentDelta === "number") {
    runtime.agentSegment += effect.agentSegmentDelta;
  }
  if (effect.executingPlanId !== undefined) {
    runtime.executingPlanId = effect.executingPlanId;
  }
  if (effect.terminal) {
    runtime.terminal = true;
  }
}
