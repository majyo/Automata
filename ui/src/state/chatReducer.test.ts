import { describe, expect, it } from "vitest";
import type { ToolApprovalRequest } from "../types/chat";
import {
  chatReducer,
  initialChatState,
  selectActiveRun,
  selectMessages,
  selectSessionApprovals,
} from "./chatReducer";
import type { ChatState } from "./chatReducer";

describe("chatReducer tool output", () => {
  it("appends stdout and stderr to the matching running tool card", () => {
    const started = chatReducer(initialChatState, {
      type: "toolCallStarted",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: {
        type: "tool_call",
        session_id: "session-1",
        run_id: "run-1",
        seq: 1,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        arguments: '{"cmd":"echo hello"}',
      },
    });

    const withStdout = chatReducer(started, {
      type: "toolOutputReceived",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: {
        type: "tool_output_delta",
        session_id: "session-1",
        run_id: "run-1",
        seq: 2,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        stream: "stdout",
        content: "hello\n",
      },
    });
    const withStderr = chatReducer(withStdout, {
      type: "toolOutputReceived",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: {
        type: "tool_output_delta",
        session_id: "session-1",
        run_id: "run-1",
        seq: 3,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        stream: "stderr",
        content: "warning\n",
        truncated: true,
      },
    });

    expect(withStderr.messagesBySession["session-1"][0].metadata?.live_output).toEqual({
      stdout: "hello\n",
      stderr: "warning\n",
      truncated: true,
    });
  });

  it("keeps the final tool result authoritative", () => {
    const liveOnly = chatReducer(initialChatState, {
      type: "toolOutputReceived",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: {
        type: "tool_output_delta",
        session_id: "session-1",
        run_id: "run-1",
        seq: 1,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        stream: "stdout",
        content: "partial",
      },
    });

    const completed = chatReducer(liveOnly, {
      type: "toolCallCompleted",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: {
        type: "tool_result",
        session_id: "session-1",
        run_id: "run-1",
        seq: 2,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        success: true,
        content: '{"stdout":"complete"}',
      },
    });

    expect(completed.messagesBySession["session-1"][0].metadata?.result).toEqual({
      success: true,
      content: '{"stdout":"complete"}',
    });
  });
});

function approval(): ToolApprovalRequest {
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
  };
}

describe("chatReducer slice composition", () => {
  it("applies one coordinated update for an approval event", () => {
    const state = chatReducer(initialChatState, {
      type: "runStarted",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 1,
    });

    const withApproval = chatReducer(state, {
      type: "approvalRequired",
      approval: approval(),
    });

    expect(withApproval.runsById["run-1"].status).toBe("waiting_approval");
    expect(withApproval.activeRunIdBySession["session-1"]).toBe("run-1");
    expect(withApproval.approvalsByRun["run-1"]).toEqual([approval()]);

    const resolved = chatReducer(withApproval, {
      type: "approvalResolved",
      runId: "run-1",
      approvalId: "approval-1",
    });

    expect(resolved.runsById["run-1"].status).toBe("running");
    expect(resolved.approvalsByRun["run-1"]).toEqual([]);
  });

  it("clears the active run and approvals together when a run finishes", () => {
    const waiting = chatReducer(
      chatReducer(initialChatState, {
        type: "runStarted",
        runId: "run-1",
        sessionId: "session-1",
        sequence: 1,
      }),
      { type: "approvalRequired", approval: approval() },
    );

    const finished = chatReducer(waiting, {
      type: "runFinished",
      runId: "run-1",
      sessionId: "session-1",
      status: "completed",
      sequence: 4,
    });

    expect(finished.activeRunIdBySession["session-1"]).toBeUndefined();
    expect(finished.approvalsByRun["run-1"]).toBeUndefined();
    expect(finished.runsById["run-1"]).toMatchObject({
      status: "completed",
      lastSequence: 4,
      isReplaying: false,
    });
  });

  it("keeps unrelated slices referentially stable", () => {
    const messagesState = chatReducer(initialChatState, {
      type: "userMessageQueued",
      message: { id: "message-1", session_id: "session-1", role: "user", text: "hi" },
    });

    const withRun = chatReducer(messagesState, {
      type: "runStarted",
      runId: "run-1",
      sessionId: "session-1",
      sequence: 1,
    });

    expect(withRun.messagesBySession).toBe(messagesState.messagesBySession);
    expect(withRun.runsById).not.toBe(messagesState.runsById);
  });

  it("returns the previous state for actions no slice handles", () => {
    const state: ChatState = chatReducer(initialChatState, {
      type: "tokenReceived",
      messageId: "run-1:agent:0",
      sessionId: "session-1",
      content: "",
    });

    expect(state).toBe(initialChatState);
  });

  it("still exposes the public selectors", () => {
    const state = chatReducer(
      chatReducer(initialChatState, {
        type: "runStarted",
        runId: "run-1",
        sessionId: "session-1",
        sequence: 3,
      }),
      { type: "approvalRequired", approval: approval() },
    );
    const withMessage = chatReducer(state, {
      type: "userMessageQueued",
      message: { id: "message-1", session_id: "session-1", role: "user", text: "hi" },
    });

    expect(selectMessages(withMessage, "session-1")).toHaveLength(1);
    expect(selectMessages(withMessage, null)).toEqual([]);
    expect(selectActiveRun(withMessage, "session-1")).toMatchObject({
      runId: "run-1",
      status: "waiting_approval",
    });
    expect(selectActiveRun(withMessage, null)).toBeNull();
    expect(selectSessionApprovals(withMessage, "session-1")).toEqual([approval()]);
  });
});
