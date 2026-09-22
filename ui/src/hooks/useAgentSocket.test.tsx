import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentSocket } from "./useAgentSocket";
import type { ApiRuntimeConfig } from "../types/api";
import type { PendingInput } from "../types/chat";
import type { SocketPayload } from "../types/socket";

type Listener = (event: { data?: string }) => void;

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];

  readyState = 0;
  sent: string[] = [];
  private listeners: Record<string, Listener[]> = {};

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.emit("open", {});
  }

  /** Deliver one backend frame to the hook. */
  receive(payload: SocketPayload | Record<string, unknown>): void {
    this.emit("message", { data: JSON.stringify(payload) });
  }

  /** Every frame the UI sent, decoded, excluding the auth handshake. */
  commands(): Record<string, unknown>[] {
    return this.sent
      .map((raw) => JSON.parse(raw) as Record<string, unknown>)
      .filter((command) => command.type !== "authenticate");
  }

  private emit(type: string, event: { data?: string }): void {
    for (const listener of this.listeners[type] ?? []) {
      listener(event);
    }
  }
}

const config: ApiRuntimeConfig = {
  httpBaseUrl: "http://localhost",
  wsChatUrl: "ws://localhost/ws/chat",
  defaultWorkingDirectory: "D:/repo",
  apiToken: "test",
};

function queuedInput(overrides: Partial<PendingInput> = {}): PendingInput {
  return {
    requestId: "queue-request-1",
    sessionId: "session-1",
    prompt: "then run the tests",
    delivery: "queue",
    status: "pending",
    inputId: "input-1",
    ...overrides,
  };
}

function renderSocket() {
  const chatDispatch = vi.fn();
  const apiConfigRef = { current: config };
  const activeSessionIdRef = { current: "session-1" };
  const hook = renderHook(() =>
    useAgentSocket({
      apiConfigRef,
      activeSessionIdRef,
      chatDispatch,
      ensureActiveSession: vi.fn(async () => "session-1"),
      refreshSessionList: vi.fn(async () => []),
      reloadSessionMessages: vi.fn(async () => []),
    }),
  );

  act(() => hook.result.current.connectSocket(config));
  const socket = FakeSocket.instances[FakeSocket.instances.length - 1];
  act(() => socket.open());
  return { ...hook, socket, chatDispatch };
}

/** A `started` frame is what marks the session as having an active Run. */
function startRun(socket: FakeSocket, runId = "run-1"): void {
  act(() =>
    socket.receive({
      type: "started",
      session_id: "session-1",
      run_id: runId,
      seq: 1,
      schema_version: 1,
      prompt: "start",
    }),
  );
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  let counter = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `uuid-${(counter += 1)}` });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAgentSocket input delivery", () => {
  it("starts a Run when the session is idle", async () => {
    const { result, socket, chatDispatch } = renderSocket();

    await act(async () => {
      await result.current.sendPrompt("fix the bug", "execute", []);
    });

    expect(socket.commands()).toEqual([
      {
        type: "prompt",
        session_id: "session-1",
        prompt: "fix the bug",
      },
    ]);
    expect(chatDispatch).toHaveBeenCalledWith(
      expect.objectContaining({ type: "userMessageQueued" }),
    );
    expect(chatDispatch).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "inputSubmitted" }),
    );
  });

  it("queues a prompt instead of racing an active Run", async () => {
    const { result, socket, chatDispatch } = renderSocket();
    startRun(socket);
    chatDispatch.mockClear();

    await act(async () => {
      await result.current.sendPrompt("then run the tests", "plan", []);
    });

    expect(socket.commands()).toEqual([
      {
        type: "prompt",
        session_id: "session-1",
        prompt: "then run the tests",
        mode: "plan",
        delivery: "queue",
        request_id: expect.any(String),
      },
    ]);
    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputSubmitted",
      sessionId: "session-1",
      requestId: expect.any(String),
      prompt: "then run the tests",
      delivery: "queue",
      runId: "run-1",
    });
    // A queued message stays out of the transcript until its Run starts.
    expect(chatDispatch).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "userMessageQueued" }),
    );
  });

  it("settles a queued input when the backend acknowledges it", () => {
    const { socket, chatDispatch } = renderSocket();
    startRun(socket);
    chatDispatch.mockClear();

    act(() =>
      socket.receive({
        type: "input_accepted",
        session_id: "session-1",
        request_id: "queue-request-1",
        input_id: "input-1",
        delivery: "queue",
        status: "pending",
        position: 12,
        idempotent: false,
      }),
    );

    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputAccepted",
      sessionId: "session-1",
      requestId: "queue-request-1",
      inputId: "input-1",
      position: 12,
      runId: null,
    });
    expect(socket.commands()).toEqual([]);
  });

  it("steers a queued message and withdraws the queued copy on the ack", () => {
    const { result, socket, chatDispatch } = renderSocket();
    startRun(socket);
    chatDispatch.mockClear();

    act(() => result.current.steerInput(queuedInput()));

    const steer = socket.commands()[0];
    expect(steer).toEqual({
      type: "prompt",
      session_id: "session-1",
      prompt: "then run the tests",
      delivery: "steer",
      run_id: "run-1",
      request_id: "uuid-1",
    });

    act(() =>
      socket.receive({
        type: "input_accepted",
        session_id: "session-1",
        request_id: "uuid-1",
        input_id: "input-2",
        delivery: "steer",
        status: "pending",
        idempotent: false,
      }),
    );

    expect(socket.commands()[1]).toEqual({
      type: "cancel_input",
      session_id: "session-1",
      input_id: "input-1",
    });
    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputCancelling",
      sessionId: "session-1",
      inputId: "input-1",
    });
  });

  it("keeps the queued copy when the Run refuses steering", () => {
    const { result, socket, chatDispatch } = renderSocket();
    startRun(socket);
    chatDispatch.mockClear();

    act(() => result.current.steerInput(queuedInput()));

    act(() =>
      socket.receive({
        type: "run_error",
        code: "run_not_steerable",
        session_id: "session-1",
        run_id: "run-1",
        request_id: "uuid-1",
        message: "Run is no longer accepting steering input.",
      }),
    );

    expect(socket.commands()).toHaveLength(1);
    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputSteerFailed",
      sessionId: "session-1",
      requestId: "uuid-1",
    });
  });

  it("does not steer when the session has no active Run", () => {
    const { result, socket } = renderSocket();

    act(() => result.current.steerInput(queuedInput()));

    expect(socket.commands()).toEqual([]);
  });

  it("withdraws a queued message and reports the withdrawal", () => {
    const { result, socket, chatDispatch } = renderSocket();
    const input = queuedInput();
    chatDispatch.mockClear();

    act(() => result.current.cancelInput(input));

    expect(socket.commands()).toEqual([
      { type: "cancel_input", session_id: "session-1", input_id: "input-1" },
    ]);
    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputCancelling",
      sessionId: "session-1",
      inputId: "input-1",
    });

    act(() =>
      socket.receive({
        type: "input_cancelled",
        session_id: "session-1",
        input_id: "input-1",
      }),
    );
    expect(chatDispatch).toHaveBeenCalledWith({
      type: "inputCancelled",
      sessionId: "session-1",
      inputId: "input-1",
    });
  });
});

describe("useAgentSocket busy guard", () => {
  it("stops queueing after the backend refuses a frame outright", async () => {
    const { result, socket } = renderSocket();

    await act(async () => {
      await result.current.sendPrompt("first", "execute", []);
    });

    act(() => socket.receive({ type: "error", message: "Session not found" }));

    await act(async () => {
      await result.current.sendPrompt("second", "execute", []);
    });

    // Without dropping the guard the second prompt would have been queued
    // behind a Run that never started.
    expect(socket.commands()[1]).toEqual({
      type: "prompt",
      session_id: "session-1",
      prompt: "second",
    });
  });

  it("forgets a Run the backend no longer reports as active", async () => {
    const { result, socket } = renderSocket();
    startRun(socket);

    act(() =>
      socket.receive({
        type: "ready",
        message: "Automata agent is ready.",
        active_runs: [],
      }),
    );

    await act(async () => {
      await result.current.sendPrompt("after reconnect", "execute", []);
    });

    expect(socket.commands()[socket.commands().length - 1]).toEqual({
      type: "prompt",
      session_id: "session-1",
      prompt: "after reconnect",
    });
  });
});
