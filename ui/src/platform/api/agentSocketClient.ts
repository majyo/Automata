import type { ApprovalDecision, InputDelivery } from "../../types/chat";
import type { SkillSelection } from "../../types/skills";
import type { SocketPayload } from "../../types/socket";

export const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000];

/** Every frame the UI can send to the backend chat socket. */
export type AgentSocketCommand =
  | { type: "authenticate"; token: string }
  | {
      type: "resume_run";
      session_id: string;
      run_id: string;
      after_sequence: number;
    }
  | {
      type: "prompt";
      session_id: string;
      prompt: string;
      mode?: "plan";
      skills?: SkillSelection[];
      /** "steer" needs `run_id`; "queue" needs only `request_id`. */
      delivery?: InputDelivery;
      run_id?: string;
      request_id?: string;
    }
  | { type: "approve_plan"; session_id: string; plan_id: string; request_id: string }
  | {
      type: "retry_plan";
      session_id: string;
      plan_id: string;
      request_id: string;
      confirm_possible_duplicate_side_effects: true;
    }
  | {
      type: "tool_approval_response";
      session_id: string;
      run_id: string;
      approval_id: string;
      decision: ApprovalDecision;
    }
  | { type: "cancel_run"; session_id: string; run_id: string }
  | { type: "cancel_input"; session_id: string; input_id: string };

export type AgentSocketConnectOptions = {
  url: string;
  apiToken: string;
};

export type AgentSocketClientHandlers = {
  /** A connection attempt just started. */
  onConnecting(): void;
  /** The socket is open and authenticated. */
  onOpen(): void;
  onPayload(payload: SocketPayload): void;
  onInvalidPayload(): void;
  /** The active socket closed. */
  onClosed(): void;
  /** A reconnect delay was armed. */
  onReconnectScheduled(): void;
  onError(): void;
  /** Reads the current connection target for every (re)connect attempt. */
  resolveConnectOptions(): AgentSocketConnectOptions;
};

export type AgentSocketClientOptions = {
  createSocket?: (url: string) => WebSocket;
  reconnectDelaysMs?: number[];
};

export function encodeAgentCommand(command: AgentSocketCommand): string {
  return JSON.stringify(command);
}

export function parseAgentPayload(data: unknown): SocketPayload | null {
  try {
    return JSON.parse(data as string) as SocketPayload;
  } catch {
    return null;
  }
}

/**
 * WebSocket transport client for the chat channel: authentication, connect,
 * reconnect backoff, close and message encode/decode. It owns no chat state and
 * no run cursors; consumers receive lifecycle callbacks and decoded payloads.
 */
export class AgentSocketClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private reconnectEnabled = true;

  private readonly handlers: AgentSocketClientHandlers;
  private readonly createSocket: (url: string) => WebSocket;
  private readonly reconnectDelaysMs: number[];

  constructor(
    handlers: AgentSocketClientHandlers,
    options: AgentSocketClientOptions = {},
  ) {
    this.handlers = handlers;
    this.createSocket = options.createSocket ?? ((url) => new WebSocket(url));
    this.reconnectDelaysMs = options.reconnectDelaysMs ?? RECONNECT_DELAYS_MS;
  }

  connect(options: AgentSocketConnectOptions = this.handlers.resolveConnectOptions()): void {
    this.clearReconnectTimer();
    this.handlers.onConnecting();

    const socket = this.createSocket(options.url);
    this.socket = socket;

    socket.addEventListener("open", () => {
      socket.send(encodeAgentCommand({ type: "authenticate", token: options.apiToken }));
      this.reconnectAttempt = 0;
      this.handlers.onOpen();
    });
    socket.addEventListener("message", (event) => {
      const payload = parseAgentPayload(event.data);
      if (payload === null) {
        this.handlers.onInvalidPayload();
        return;
      }
      this.handlers.onPayload(payload);
    });
    socket.addEventListener("close", () => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.handlers.onClosed();
    });
    socket.addEventListener("error", () => {
      if (this.socket === socket) {
        this.handlers.onError();
      }
    });
  }

  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  send(command: AgentSocketCommand): boolean {
    const socket = this.socket;
    if (socket?.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(encodeAgentCommand(command));
    return true;
  }

  scheduleReconnect(): void {
    if (!this.reconnectEnabled || this.reconnectTimer !== null) {
      return;
    }
    const delay =
      this.reconnectDelaysMs[
        Math.min(this.reconnectAttempt, this.reconnectDelaysMs.length - 1)
      ];
    this.reconnectAttempt += 1;
    this.handlers.onReconnectScheduled();
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  /** Disables further reconnects and closes the active socket. */
  close(): void {
    this.reconnectEnabled = false;
    this.clearReconnectTimer();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer === null) {
      return;
    }
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }
}
