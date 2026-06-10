import type { ApiMessage } from "./api";
import type { SendMode } from "./chat";

export type SocketPayload =
  | { type: "ready"; message?: string }
  | { type: "started"; session_id: string; prompt: string; mode?: SendMode }
  | { type: "agent_step"; message?: string; step?: number }
  | {
      type: "context_compressed";
      scope?: "history" | "loop";
      before_chars?: number;
      after_chars?: number;
      summary_chars?: number;
      compressed_messages?: number;
      through_sequence?: number;
    }
  | { type: "tool_call"; tool_call_id?: string; tool?: string; arguments?: string }
  | { type: "tool_result"; tool_call_id?: string; tool?: string; success?: boolean; content?: string }
  | { type: "plan_ready"; session_id: string; plan_id: string; status: "pending"; content: string }
  | { type: "plan_approved"; session_id: string; plan_id: string }
  | { type: "plan_error"; message?: string }
  | { type: "token"; content?: string }
  | { type: "done"; message?: ApiMessage }
  | { type: "error"; message?: string };
