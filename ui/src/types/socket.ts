import type { ApiMessage, ApiRun } from "./api";
import type { ApprovalDecision, PersistedRunStatus, SendMode } from "./chat";

type SequencedRunEvent = {
  session_id: string;
  run_id: string;
  seq: number;
  schema_version: number;
};

export type SocketPayload =
  | { type: "ready"; message?: string; active_runs?: ApiRun[] }
  | ({ type: "started"; prompt: string; mode?: SendMode } & SequencedRunEvent)
  | ({ type: "agent_step"; message?: string; step?: number } & SequencedRunEvent)
  | ({
      type: "context_compressed";
      scope?: "history" | "loop";
      before_chars?: number;
      after_chars?: number;
      summary_chars?: number;
      compressed_messages?: number;
      through_sequence?: number;
    } & SequencedRunEvent)
  | ({
      type: "tool_call";
      tool_call_id?: string;
      tool?: string;
      arguments?: string;
      message_id?: string;
    } & SequencedRunEvent)
  | ({
      type: "tool_output_delta";
      tool_call_id?: string;
      tool?: string;
      stream: "stdout" | "stderr";
      content: string;
      truncated?: boolean;
    } & SequencedRunEvent)
  | ({
      type: "tool_result";
      tool_call_id?: string;
      tool?: string;
      success?: boolean;
      content?: string;
      message_id?: string;
      content_truncated?: boolean;
    } & SequencedRunEvent)
  | ({
      type: "skills_loaded";
      count: number;
      enabled_count: number;
    } & SequencedRunEvent)
  | ({
      type: "skills_warning";
      message: string;
    } & SequencedRunEvent)
  | ({
      type: "skill_injected";
      name: string;
      path: string;
    } & SequencedRunEvent)
  | ({
      type: "plan_ready";
      plan_id: string;
      status: "pending";
      content: string;
    } & SequencedRunEvent)
  | ({ type: "plan_approved"; plan_id: string } & SequencedRunEvent)
  | ({ type: "token"; content?: string } & SequencedRunEvent)
  | ({ type: "done"; message?: ApiMessage } & SequencedRunEvent)
  | ({
      type: "error";
      code?: string;
      message?: string;
    } & SequencedRunEvent)
  | ({
      type: "tool_approval_required";
      approval_id: string;
      tool_call_id: string;
      tool: string;
      risk: "read" | "write" | "command" | "destructive" | "external";
      reason: string;
      summary: string;
      preview: Record<string, unknown>;
      options: ApprovalDecision[];
    } & SequencedRunEvent)
  | ({
      type: "tool_approval_resolved";
      approval_id: string;
      decision: ApprovalDecision;
    } & SequencedRunEvent)
  | ({ type: "run_cancel_requested"; message?: string } & SequencedRunEvent)
  | ({ type: "run_cancelled"; code?: string; message?: string } & SequencedRunEvent)
  | ({ type: "run_interrupted"; code?: string; message?: string } & SequencedRunEvent)
  | { type: "approval_error"; run_id?: string; code?: string; message?: string }
  | { type: "run_error"; session_id?: string; run_id?: string; code?: string; message?: string }
  | {
      type: "plan_error";
      session_id?: string;
      run_id?: string;
      plan_id?: string;
      code?: string;
      message?: string;
    }
  | {
      type: "plan_execution_created" | "plan_execution_attached";
      session_id: string;
      run_id: string;
      plan_id: string;
      status: PersistedRunStatus | "executing";
      request_id: string;
    }
  | {
      type: "run_resume_started";
      session_id: string;
      run_id: string;
      after_sequence: number;
      through_sequence: number;
    }
  | {
      type: "run_resume_complete";
      session_id: string;
      run_id: string;
      status: PersistedRunStatus;
      last_sequence: number;
    };

export type SequencedSocketPayload = Extract<SocketPayload, { seq: number }>;
export type SkillSocketPayload = Extract<
  SocketPayload,
  { type: "skills_loaded" | "skills_warning" | "skill_injected" }
>;

export function isSequencedRunEvent(
  payload: SocketPayload,
): payload is SequencedSocketPayload {
  return (
    "seq" in payload &&
    typeof payload.seq === "number" &&
    "run_id" in payload &&
    typeof payload.run_id === "string" &&
    "session_id" in payload &&
    typeof payload.session_id === "string"
  );
}
