import { describe, expect, it } from "vitest";
import type { ChatAction, RunClientState } from "../../state/chatTypes";
import type { ChatMessage, PersistedRunStatus } from "../../types/chat";
import { reduceMessages } from "./messagesSlice";
import type { MessagesSliceState } from "./messagesSlice";

const empty: MessagesSliceState = { messagesBySession: {} };

function runState(runId: string, status: PersistedRunStatus): RunClientState {
  return {
    runId,
    sessionId: "session-1",
    status,
    lastSequence: 1,
    isReplaying: false,
  };
}

function message(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: "message-1",
    session_id: "session-1",
    role: "user",
    text: "hello",
    ...overrides,
  };
}

describe("reduceMessages", () => {
  it("appends stdout and stderr to the matching running tool card", () => {
    const started = reduceMessages(empty, {
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
    })!;

    const withStdout = reduceMessages(started, {
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
    })!;
    const withStderr = reduceMessages(withStdout, {
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
    })!;

    expect(withStderr.messagesBySession["session-1"][0].metadata?.live_output).toEqual({
      stdout: "hello\n",
      stderr: "warning\n",
      truncated: true,
    });
  });

  it("keeps the final tool result authoritative", () => {
    const liveOnly = reduceMessages(empty, {
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
    })!;

    const completed = reduceMessages(liveOnly, {
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
    })!;

    expect(completed.messagesBySession["session-1"][0].metadata?.result).toEqual({
      success: true,
      content: '{"stdout":"complete"}',
    });
  });

  it("keeps what a live run is streaming when the history reloads", () => {
    const state: MessagesSliceState = {
      messagesBySession: {
        "session-1": [
          message({ id: "run-1:agent:0", role: "agent", text: "streaming" }),
          message({ id: "run-2:agent:0", role: "agent", text: "finished" }),
          message({ id: "orphan:agent:0", role: "agent", text: "unknown run" }),
          message({ id: "optimistic-user", text: "uuid id" }),
          message({ id: "persisted-1", text: "kept", sequence: 3 }),
        ],
      },
      runsById: {
        "run-1": runState("run-1", "running"),
        "run-2": runState("run-2", "completed"),
      },
    };

    const persisted = message({ id: "persisted-1", text: "kept", sequence: 3 });
    const updated = reduceMessages(state, {
      type: "messagesLoaded",
      sessionId: "session-1",
      messages: [persisted],
    })!;

    expect(updated.messagesBySession["session-1"].map((item) => item.id)).toEqual([
      "persisted-1",
      "run-1:agent:0",
    ]);
  });

  it("drops the streamed messages of runs that already finished", () => {
    const state: MessagesSliceState = {
      messagesBySession: {
        "session-1": [message({ id: "run-1:agent:0", role: "agent", text: "streamed" })],
      },
      runsById: { "run-1": runState("run-1", "cancelled") },
    };

    const updated = reduceMessages(state, {
      type: "messagesLoaded",
      sessionId: "session-1",
      messages: [],
    })!;

    expect(updated.messagesBySession["session-1"]).toEqual([]);
  });

  it("does not show a queued prompt twice once its message is persisted", () => {
    const queued: ChatMessage = message({
      id: "run-2:input:input-1",
      text: "second",
      metadata: { input_id: "input-1", delivery: "queue" },
    });
    const state: MessagesSliceState = {
      messagesBySession: {
        "session-1": [queued, message({ id: "run-2:agent:0", role: "agent", text: "part" })],
      },
      runsById: { "run-2": runState("run-2", "running") },
    };

    const updated = reduceMessages(state, {
      type: "messagesLoaded",
      sessionId: "session-1",
      messages: [
        message({ id: "message-user-2", text: "second", sequence: 3, metadata: { input_id: "input-1" } }),
      ],
    })!;

    expect(updated.messagesBySession["session-1"].map((item) => item.id)).toEqual([
      "message-user-2",
      "run-2:agent:0",
    ]);
  });

  it("clears a single session without touching the others", () => {
    const state: MessagesSliceState = {
      messagesBySession: {
        "session-1": [message({})],
        "session-2": [message({ id: "message-2", session_id: "session-2" })],
      },
    };

    const updated = reduceMessages(state, {
      type: "sessionMessagesCleared",
      sessionId: "session-1",
    })!;

    expect(updated.messagesBySession["session-1"]).toBeUndefined();
    expect(updated.messagesBySession["session-2"]).toHaveLength(1);
  });

  it("queues messages without a session and ignores empty token chunks", () => {
    expect(
      reduceMessages(empty, {
        type: "userMessageQueued",
        message: message({ session_id: undefined }),
      }),
    ).toBeNull();
    expect(
      reduceMessages(empty, {
        type: "tokenReceived",
        messageId: "run-1:agent:0",
        sessionId: "session-1",
        content: "",
      }),
    ).toBeNull();
  });

  it("creates then extends the streaming agent message", () => {
    const first = reduceMessages(empty, {
      type: "tokenReceived",
      messageId: "run-1:agent:0",
      sessionId: "session-1",
      content: "Hel",
    })!;
    const second = reduceMessages(first, {
      type: "tokenReceived",
      messageId: "run-1:agent:0",
      sessionId: "session-1",
      content: "lo",
    })!;

    expect(second.messagesBySession["session-1"]).toEqual([
      {
        id: "run-1:agent:0",
        session_id: "session-1",
        role: "agent",
        text: "Hello",
      },
    ]);
  });

  it("appends run event notes as tool messages", () => {
    const updated = reduceMessages(empty, {
      type: "runEventAppended",
      sessionId: "session-1",
      id: "run-1:context:3",
      text: "Context compressed",
    })!;

    expect(updated.messagesBySession["session-1"]).toEqual([
      {
        id: "run-1:context:3",
        session_id: "session-1",
        role: "tool",
        text: "Context compressed",
      },
    ]);
  });

  it("writes streaming failures into an empty agent message", () => {
    const failed = reduceMessages(empty, {
      type: "streamingFailed",
      messageId: "run-1:error",
      sessionId: "session-1",
      errorText: "Agent run failed",
    })!;

    expect(failed.messagesBySession["session-1"]).toEqual([
      {
        id: "run-1:error",
        session_id: "session-1",
        role: "agent",
        text: "Agent run failed",
      },
    ]);

    const blank: MessagesSliceState = {
      messagesBySession: {
        "session-1": [message({ id: "run-1:error", role: "agent", text: "   " })],
      },
    };
    const replaced = reduceMessages(blank, {
      type: "streamingFailed",
      messageId: "run-1:error",
      sessionId: "session-1",
      errorText: "boom",
    })!;
    expect(replaced.messagesBySession["session-1"][0].text).toBe("boom");

    const streamed: MessagesSliceState = {
      messagesBySession: {
        "session-1": [message({ id: "run-1:error", role: "agent", text: "partial" })],
      },
    };
    const kept = reduceMessages(streamed, {
      type: "streamingFailed",
      messageId: "run-1:error",
      sessionId: "session-1",
      errorText: "boom",
    })!;
    expect(kept.messagesBySession["session-1"][0].text).toBe("partial");

    expect(
      reduceMessages(empty, {
        type: "streamingFailed",
        messageId: null,
        sessionId: "session-1",
        errorText: "boom",
      }),
    ).toBeNull();
  });

  it("truncates oversized live output and keeps both ends", () => {
    const payload = (content: string) =>
      ({
        type: "tool_output_delta",
        session_id: "session-1",
        run_id: "run-1",
        seq: 1,
        schema_version: 1,
        tool_call_id: "call-1",
        tool: "exec_command",
        stream: "stdout",
        content,
      }) as const;

    const huge = `${"h".repeat(70 * 1024)}TAIL`;
    const updated = reduceMessages(empty, {
      type: "toolOutputReceived",
      sessionId: "session-1",
      messageId: "run-1:tool:call-1",
      toolCallId: "call-1",
      payload: payload(huge),
    })!;

    const liveOutput = updated.messagesBySession["session-1"][0].metadata?.live_output;
    expect(liveOutput?.stdout).toContain("... live output truncated ...");
    expect(liveOutput?.stdout.endsWith("TAIL")).toBe(true);
    expect(liveOutput?.stdout.length).toBe(64 * 1024);
  });

  it("ignores actions owned by other slices", () => {
    const actions: ChatAction[] = [
      { type: "planStatusChanged", sessionId: "session-1", planId: "plan-1", status: "executing" },
      { type: "runStarted", runId: "run-1", sessionId: "session-1", sequence: 1 },
      { type: "runFinished", runId: "run-1", sessionId: "session-1", status: "completed" },
    ];

    for (const action of actions) {
      expect(reduceMessages(empty, action)).toBeNull();
    }
  });
});
