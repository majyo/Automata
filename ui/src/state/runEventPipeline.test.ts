import { describe, expect, it } from "vitest";
import { RunStreamController } from "../features/runs/runStreamController";
import type { SequencedSocketPayload } from "../types/socket";
import {
  chatReducer,
  initialChatState,
  selectActiveRun,
  selectMessages,
  selectSessionApprovals,
} from "./chatReducer";
import type { ChatState } from "./chatReducer";

const base = { session_id: "session-1", run_id: "run-1", schema_version: 1 };

function createPipeline() {
  let state: ChatState = initialChatState;
  const statuses: string[] = [];
  const effects: string[] = [];
  const resumes: number[] = [];

  const controller = new RunStreamController({
    onRunEvent: (projection) => {
      for (const action of projection.actions) {
        state = chatReducer(state, action);
      }
      for (const effect of projection.effects) {
        if (effect.kind === "status") {
          statuses.push(effect.status);
        } else {
          effects.push(effect.kind);
        }
      }
    },
    onResumeRequested: (request) => resumes.push(request.afterSequence),
  });

  return {
    controller,
    statuses,
    effects,
    resumes,
    state: () => state,
    accept: (event: SequencedSocketPayload) => controller.acceptEvent(event),
  };
}

describe("run event pipeline", () => {
  it("drives one coordinated chat state through a full tool run", () => {
    const pipeline = createPipeline();

    pipeline.accept({ type: "started", prompt: "hi", ...base, seq: 1 });
    pipeline.accept({ type: "token", content: "Hel", ...base, seq: 2 });
    pipeline.accept({ type: "token", content: "lo", ...base, seq: 3 });
    pipeline.accept({
      type: "tool_call",
      tool_call_id: "call-1",
      tool: "exec_command",
      arguments: '{"cmd":"ls"}',
      ...base,
      seq: 4,
    });
    pipeline.accept({
      type: "tool_output_delta",
      tool_call_id: "call-1",
      tool: "exec_command",
      stream: "stdout",
      content: "file.txt\n",
      ...base,
      seq: 5,
    });
    pipeline.accept({
      type: "tool_result",
      tool_call_id: "call-1",
      tool: "exec_command",
      success: true,
      content: '{"stdout":"file.txt"}',
      ...base,
      seq: 6,
    });
    pipeline.accept({ type: "token", content: "done", ...base, seq: 7 });
    pipeline.accept({ type: "done", ...base, seq: 8 });

    const state = pipeline.state();
    expect(state.runsById["run-1"]).toMatchObject({
      status: "completed",
      lastSequence: 8,
      isReplaying: false,
    });
    expect(state.activeRunIdBySession["session-1"]).toBeUndefined();

    // Tokens before and after the tool call stay in separate agent messages
    // because tool calls and tool results each advance the agent segment.
    expect(selectMessages(state, "session-1").map((message) => message.id)).toEqual([
      "run-1:agent:0",
      "run-1:tool:call-1",
      "run-1:agent:2",
    ]);
    expect(selectMessages(state, "session-1")[0].text).toBe("Hello");
    expect(selectMessages(state, "session-1")[2].text).toBe("done");
    expect(selectMessages(state, "session-1")[1].metadata).toMatchObject({
      tool_call_id: "call-1",
      tool: "exec_command",
      result: { success: true, content: '{"stdout":"file.txt"}' },
      live_output: { stdout: "file.txt\n", stderr: "" },
    });

    expect(pipeline.statuses).toContain("Streaming");
    expect(pipeline.statuses).toContain("Tool: exec_command");
    expect(pipeline.statuses[pipeline.statuses.length - 1]).toBe("Ready");
    expect(pipeline.effects).toContain("runActivated");
    expect(pipeline.effects).toContain("runDeactivated");
    expect(pipeline.effects).toContain("sessionRefreshRequested");
    expect(pipeline.resumes).toEqual([]);
  });

  it("suspends on approvals and resumes the run afterwards", () => {
    const pipeline = createPipeline();

    pipeline.accept({ type: "started", prompt: "hi", ...base, seq: 1 });
    pipeline.accept({
      type: "tool_approval_required",
      approval_id: "approval-1",
      tool_call_id: "call-1",
      tool: "apply_patch",
      risk: "write",
      reason: "writes files",
      summary: "patch",
      preview: {},
      options: ["allow_once", "deny"],
      ...base,
      seq: 2,
    });

    expect(selectActiveRun(pipeline.state(), "session-1")).toMatchObject({
      status: "waiting_approval",
    });
    expect(selectSessionApprovals(pipeline.state(), "session-1")).toHaveLength(1);

    pipeline.accept({
      type: "tool_approval_resolved",
      approval_id: "approval-1",
      decision: "allow_once",
      ...base,
      seq: 3,
    });

    expect(selectActiveRun(pipeline.state(), "session-1")).toMatchObject({
      status: "running",
    });
    expect(selectSessionApprovals(pipeline.state(), "session-1")).toEqual([]);
  });

  it("finishes the executing plan when the run completes", () => {
    const pipeline = createPipeline();

    pipeline.accept({ type: "started", prompt: "hi", ...base, seq: 1 });
    pipeline.accept({
      type: "plan_ready",
      plan_id: "plan-1",
      status: "pending",
      content: "1. do it",
      ...base,
      seq: 2,
    });
    pipeline.accept({ type: "plan_approved", plan_id: "plan-1", ...base, seq: 3 });
    pipeline.accept({ type: "done", ...base, seq: 4 });

    expect(selectMessages(pipeline.state(), "session-1")).toEqual([
      {
        id: "run-1:plan:plan-1",
        session_id: "session-1",
        role: "agent",
        text: "1. do it",
        kind: "plan",
        plan_id: "plan-1",
        plan_status: "executed",
      },
    ]);
  });

  it("requests a resume on a gap and refuses the out-of-order event", () => {
    const pipeline = createPipeline();

    pipeline.accept({ type: "started", prompt: "hi", ...base, seq: 1 });
    pipeline.accept({ type: "token", content: "skipped", ...base, seq: 4 });

    expect(pipeline.resumes).toEqual([1]);
    expect(pipeline.state().runsById["run-1"].lastSequence).toBe(1);
    expect(selectMessages(pipeline.state(), "session-1")).toEqual([]);

    pipeline.accept({ type: "token", content: "resumed", ...base, seq: 2 });
    expect(selectMessages(pipeline.state(), "session-1")[0].text).toBe("resumed");
  });
});
