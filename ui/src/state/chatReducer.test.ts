import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState } from "./chatReducer";

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
