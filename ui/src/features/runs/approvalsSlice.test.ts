import { describe, expect, it } from "vitest";
import type { ToolApprovalRequest } from "../../types/chat";
import { reduceApprovals } from "./approvalsSlice";
import type { ApprovalsSliceState } from "./approvalsSlice";

const empty: ApprovalsSliceState = { approvalsByRun: {} };

function approval(overrides: Partial<ToolApprovalRequest>): ToolApprovalRequest {
  return {
    approval_id: "approval-1",
    run_id: "run-1",
    session_id: "session-1",
    tool_call_id: "call-1",
    tool: "apply_patch",
    risk: "write",
    reason: "writes",
    summary: "patch",
    preview: {},
    options: ["allow_once", "deny"],
    ...overrides,
  };
}

describe("reduceApprovals", () => {
  it("records pending approvals per run", () => {
    const updated = reduceApprovals(empty, {
      type: "approvalRequired",
      approval: approval({}),
    })!;

    expect(updated.approvalsByRun["run-1"]).toEqual([approval({})]);
  });

  it("replaces an approval with the same id instead of duplicating it", () => {
    const state: ApprovalsSliceState = {
      approvalsByRun: { "run-1": [approval({ summary: "old" })] },
    };

    const updated = reduceApprovals(state, {
      type: "approvalRequired",
      approval: approval({ summary: "new" }),
    })!;

    expect(updated.approvalsByRun["run-1"]).toEqual([approval({ summary: "new" })]);
  });

  it("removes a resolved approval but keeps the run entry", () => {
    const state: ApprovalsSliceState = {
      approvalsByRun: {
        "run-1": [approval({}), approval({ approval_id: "approval-2" })],
      },
    };

    const updated = reduceApprovals(state, {
      type: "approvalResolved",
      runId: "run-1",
      approvalId: "approval-1",
    })!;

    expect(updated.approvalsByRun["run-1"]).toEqual([approval({ approval_id: "approval-2" })]);
  });

  it("drops approvals when the run finishes", () => {
    const state: ApprovalsSliceState = {
      approvalsByRun: { "run-1": [approval({})], "run-2": [approval({ run_id: "run-2" })] },
    };

    const updated = reduceApprovals(state, {
      type: "runFinished",
      runId: "run-1",
      sessionId: "session-1",
      status: "completed",
    })!;

    expect(updated.approvalsByRun["run-1"]).toBeUndefined();
    expect(updated.approvalsByRun["run-2"]).toHaveLength(1);
  });

  it("ignores actions owned by other slices", () => {
    expect(
      reduceApprovals(empty, {
        type: "runStarted",
        runId: "run-1",
        sessionId: "session-1",
        sequence: 1,
      }),
    ).toBeNull();
    expect(
      reduceApprovals(empty, {
        type: "tokenReceived",
        messageId: "run-1:agent:0",
        sessionId: "session-1",
        content: "hi",
      }),
    ).toBeNull();
  });
});
