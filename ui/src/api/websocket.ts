import type { SocketPayload } from "../types/socket";

export type AgentSocketHandlers = {
  onOpen(): void;
  onPayload(payload: SocketPayload): void;
  onInvalidPayload(): void;
  onClose(socket: WebSocket): void;
  onError(): void;
};

export function createAgentSocket(url: string, handlers: AgentSocketHandlers): WebSocket {
  const socket = new WebSocket(url);

  socket.addEventListener("open", handlers.onOpen);
  socket.addEventListener("message", (event) => {
    let payload: SocketPayload;
    try {
      payload = JSON.parse(event.data) as SocketPayload;
    } catch {
      handlers.onInvalidPayload();
      return;
    }

    handlers.onPayload(payload);
  });
  socket.addEventListener("close", () => handlers.onClose(socket));
  socket.addEventListener("error", handlers.onError);

  return socket;
}
