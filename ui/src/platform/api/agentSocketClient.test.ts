import { afterEach, describe, expect, it, vi } from "vitest";
import type { SocketPayload } from "../../types/socket";
import {
  AgentSocketClient,
  encodeAgentCommand,
  parseAgentPayload,
} from "./agentSocketClient";
import type {
  AgentSocketClientHandlers,
  AgentSocketConnectOptions,
} from "./agentSocketClient";

class FakeSocket {
  readonly url: string;
  readyState = 0;
  sent: string[] = [];
  closed = false;

  private readonly listeners = new Map<string, Array<(event: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
    this.emit("close", {});
  }

  open(): void {
    this.readyState = 1;
    this.emit("open", {});
  }

  message(data: unknown): void {
    this.emit("message", { data });
  }

  error(): void {
    this.emit("error", {});
  }

  private emit(type: string, event: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function createHarness(initial: AgentSocketConnectOptions = {
  url: "ws://127.0.0.1:8765/ws/chat",
  apiToken: "token-1",
}) {
  const sockets: FakeSocket[] = [];
  const events: string[] = [];
  const payloads: SocketPayload[] = [];
  let connectOptions = initial;

  const handlers: AgentSocketClientHandlers = {
    onConnecting: () => events.push("connecting"),
    onOpen: () => events.push("open"),
    onPayload: (payload) => payloads.push(payload),
    onInvalidPayload: () => events.push("invalid"),
    onClosed: () => events.push("closed"),
    onReconnectScheduled: () => events.push("reconnectScheduled"),
    onError: () => events.push("error"),
    resolveConnectOptions: () => connectOptions,
  };

  const client = new AgentSocketClient(handlers, {
    createSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    reconnectDelaysMs: [10, 20],
  });

  return {
    client,
    sockets,
    events,
    payloads,
    setConnectOptions: (options: AgentSocketConnectOptions) => {
      connectOptions = options;
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("AgentSocketClient", () => {
  it("authenticates with the api token when the socket opens", () => {
    const harness = createHarness();

    harness.client.connect();

    expect(harness.events).toEqual(["connecting"]);
    expect(harness.sockets).toHaveLength(1);
    expect(harness.sockets[0].url).toBe("ws://127.0.0.1:8765/ws/chat");

    harness.sockets[0].open();

    expect(harness.events).toEqual(["connecting", "open"]);
    expect(harness.sockets[0].sent).toEqual([
      JSON.stringify({ type: "authenticate", token: "token-1" }),
    ]);
  });

  it("decodes payloads and reports invalid frames", () => {
    const harness = createHarness();
    harness.client.connect();
    harness.sockets[0].open();

    harness.sockets[0].message(JSON.stringify({ type: "ready", message: "Ready" }));
    harness.sockets[0].message("not json");

    expect(harness.payloads).toEqual([{ type: "ready", message: "Ready" }]);
    expect(harness.events).toContain("invalid");
  });

  it("reports errors only for the active socket", () => {
    const harness = createHarness();
    harness.client.connect();
    const stale = harness.sockets[0];

    harness.client.connect();
    const active = harness.sockets[1];

    stale.error();
    expect(harness.events).not.toContain("error");

    active.error();
    expect(harness.events).toContain("error");
  });

  it("ignores the close of a replaced socket", () => {
    const harness = createHarness();
    harness.client.connect();
    const stale = harness.sockets[0];

    harness.client.connect();
    const active = harness.sockets[1];

    stale.close();
    expect(harness.events).not.toContain("closed");
    expect(active.closed).toBe(false);

    active.close();
    expect(harness.events).toContain("closed");
  });

  it("only sends while the socket is open", () => {
    const harness = createHarness();
    harness.client.connect();

    expect(harness.client.isOpen()).toBe(false);
    expect(
      harness.client.send({ type: "cancel_run", session_id: "session-1", run_id: "run-1" }),
    ).toBe(false);

    harness.sockets[0].open();

    expect(harness.client.isOpen()).toBe(true);
    expect(
      harness.client.send({ type: "cancel_run", session_id: "session-1", run_id: "run-1" }),
    ).toBe(true);
    expect(JSON.parse(harness.sockets[0].sent[1])).toEqual({
      type: "cancel_run",
      session_id: "session-1",
      run_id: "run-1",
    });
  });

  it("reconnects with the latest options and the backoff ladder", () => {
    vi.useFakeTimers();
    const harness = createHarness();

    harness.client.connect();
    harness.sockets[0].open();
    harness.setConnectOptions({ url: "ws://other/ws/chat", apiToken: "token-2" });

    harness.client.scheduleReconnect();
    harness.client.scheduleReconnect();

    expect(harness.sockets).toHaveLength(1);
    expect(harness.events).toContain("reconnectScheduled");

    vi.advanceTimersByTime(10);

    expect(harness.sockets).toHaveLength(2);
    expect(harness.sockets[1].url).toBe("ws://other/ws/chat");

    harness.sockets[1].open();
    expect(JSON.parse(harness.sockets[1].sent[0])).toEqual({
      type: "authenticate",
      token: "token-2",
    });
  });

  it("stops reconnecting after close", () => {
    vi.useFakeTimers();
    const harness = createHarness();

    harness.client.connect();
    harness.client.close();

    expect(harness.sockets[0].closed).toBe(true);
    expect(harness.events).not.toContain("closed");

    harness.client.scheduleReconnect();
    vi.advanceTimersByTime(1_000);

    expect(harness.sockets).toHaveLength(1);
  });
});

describe("agent socket codecs", () => {
  it("serializes commands as JSON", () => {
    expect(
      encodeAgentCommand({
        type: "prompt",
        session_id: "session-1",
        prompt: "hi",
        mode: "plan",
      }),
    ).toBe('{"type":"prompt","session_id":"session-1","prompt":"hi","mode":"plan"}');
  });

  it("returns null for undecodable frames", () => {
    expect(parseAgentPayload('{"type":"ready"}')).toEqual({ type: "ready" });
    expect(parseAgentPayload("{")).toBeNull();
  });
});
