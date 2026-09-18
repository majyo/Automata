/**
 * Session-scoped run bookkeeping for the agent socket.
 *
 * Three related facts live here, all keyed by session and all belonging to
 * the connection/feature layer rather than to any React component:
 *
 * - which Run is active per session, so a second prompt is refused locally
 *   instead of racing the backend's one-active-Run-per-session rule;
 * - which sessions have a command in flight, so a plan approval or a prompt
 *   cannot be sent twice while the first is still being acknowledged;
 * - the request id chosen for a plan, so a retry after a lost reply is
 *   idempotent on the backend.
 *
 * Keeping this out of `useRef` makes the rules testable without rendering
 * React, and means a session switch no longer risks losing another
 * session's in-flight state.
 */

export type RunTrackerListener = (
  activeRunIdBySession: Record<string, string>,
) => void;

export class SessionRunTracker {
  private activeRunIds: Record<string, string> = {};
  private pendingSessions = new Set<string>();
  private planRequestIds: Record<string, string> = {};
  private listeners = new Set<RunTrackerListener>();

  /** Subscribe to active-Run changes; returns an unsubscribe function. */
  subscribe(listener: RunTrackerListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** A plain copy of the active Run map, safe to render or store. */
  snapshot(): Record<string, string> {
    return { ...this.activeRunIds };
  }

  activeRunId(sessionId: string): string | undefined {
    return this.activeRunIds[sessionId];
  }

  /** Mark a session as having a Run in flight, or clear it. */
  setActiveRun(sessionId: string, runId?: string): void {
    if (runId) {
      if (this.activeRunIds[sessionId] === runId) return;
      this.activeRunIds = { ...this.activeRunIds, [sessionId]: runId };
    } else {
      if (!(sessionId in this.activeRunIds)) return;
      const next = { ...this.activeRunIds };
      delete next[sessionId];
      this.activeRunIds = next;
    }
    this.emit();
  }

  /** Record that a command for this session is awaiting acknowledgement. */
  markPending(sessionId: string): void {
    this.pendingSessions.add(sessionId);
  }

  clearPending(sessionId: string): void {
    this.pendingSessions.delete(sessionId);
  }

  isPending(sessionId: string): boolean {
    return this.pendingSessions.has(sessionId);
  }

  /**
   * Whether a new command for this session must be refused.
   *
   * A session with an active Run, or one whose previous command has not
   * been acknowledged, is busy.
   */
  isBusy(sessionId: string): boolean {
    return Boolean(this.activeRunIds[sessionId]) || this.isPending(sessionId);
  }

  /** Any session currently busy, for a coarse "is anything running" check. */
  hasBusySession(sessionIds: string[]): boolean {
    return sessionIds.some((sessionId) => this.isBusy(sessionId));
  }

  /**
   * The request id to use for a plan approval.
   *
   * A plan gets exactly one id for the lifetime of the tracker, so an
   * accepted retry is recognised as the same request rather than creating a
   * second execution attempt.
   */
  requestIdForPlan(planId: string, create: () => string): string {
    const existing = this.planRequestIds[planId];
    if (existing) return existing;
    const created = create();
    this.planRequestIds[planId] = created;
    return created;
  }

  /** Drop a plan's request id once the backend has answered for it. */
  releasePlan(planId: string): void {
    delete this.planRequestIds[planId];
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) {
      listener(snapshot);
    }
  }
}
