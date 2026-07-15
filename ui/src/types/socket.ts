import type { ApiMessage } from "./api";
import type { ApprovalDecision, SendMode } from "./chat";

export type SocketPayload =
  | { type: "ready"; message?: string }
  | { type: "started"; run_id: string; session_id: string; prompt: string; mode?: SendMode }
  | { type: "agent_step"; run_id?: string; message?: string; step?: number }
  | {
      type: "context_compressed";
      scope?: "history" | "loop";
      before_chars?: number;
      after_chars?: number;
      summary_chars?: number;
      compressed_messages?: number;
      through_sequence?: number;
    }
  | { type: "tool_call"; run_id?: string; tool_call_id?: string; tool?: string; arguments?: string }
  | { type: "tool_result"; run_id?: string; tool_call_id?: string; tool?: string; success?: boolean; content?: string }
  | { type: "plan_ready"; run_id: string; session_id: string; plan_id: string; status: "pending"; content: string }
  | { type: "plan_approved"; run_id: string; session_id: string; plan_id: string }
  | { type: "plan_error"; run_id?: string; message?: string }
  | { type: "token"; run_id?: string; content?: string }
  | { type: "done"; run_id: string; message?: ApiMessage }
  | { type: "error"; run_id?: string; message?: string }
  | {
      type: "tool_approval_required";
      run_id: string;
      approval_id: string;
      tool_call_id: string;
      tool: string;
      risk: "read" | "write" | "command" | "destructive" | "external";
      reason: string;
      summary: string;
      preview: Record<string, unknown>;
      options: ApprovalDecision[];
    }
  | { type: "tool_approval_resolved"; run_id: string; approval_id: string; decision: ApprovalDecision }
  | { type: "approval_error"; run_id?: string; code?: string; message?: string }
  | { type: "run_cancel_requested"; run_id: string; session_id: string }
  | { type: "run_cancelled"; run_id: string; session_id: string; message?: string }
  | { type: "run_error"; run_id?: string; code?: string; message?: string };
