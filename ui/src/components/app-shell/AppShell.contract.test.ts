import { describe, expect, it } from "vitest";
import type {
  ComposerActions,
  ComposerView,
  ConnectionView,
  ConversationView,
  SessionView,
  SkillsView,
} from "./viewModel";
import type { ChatMessage, PersistedRunStatus } from "../../types/chat";

/**
 * The shell's view boundary.
 *
 * `AppShell` takes named view models and action groups instead of a flat
 * list of business fields. These checks pin the composition the app builds
 * and the invariant that a view model carries *data only* — behaviour lives
 * in the matching action group, so a feature can render the shell from a
 * plain snapshot without handing it callbacks.
 */

const RUN_STATUS: PersistedRunStatus = "running";

function sessionView(): SessionView {
  return {
    sessions: [],
    activeSession: null,
    activeSessionId: null,
    isNewSessionDraft: true,
    displayedWorkingDirectory: "D:/workspace/project",
    editingSessionId: null,
    editingTitle: "",
    activeRunIdBySession: { "session-1": "run-1" },
    runStatusBySession: { "session-1": RUN_STATUS },
  };
}

function conversationView(messages: ChatMessage[]): ConversationView {
  return {
    messages,
    messagesRef: { current: null },
    approvals: [],
    isStreaming: false,
  };
}

function composerView(): ComposerView {
  return {
    prompt: "hello",
    sendMode: "execute",
    canSend: true,
    defaultWorkingDirectory: "D:/workspace",
    permissionPreset: "default",
    permissionUpdating: false,
    sandboxSetupStatus: "Sandbox ready",
    pendingInputs: [
      {
        requestId: "input-request-1",
        sessionId: "session-1",
        prompt: "then run the tests",
        delivery: "queue",
        status: "pending",
        inputId: "input-1",
      },
    ],
  };
}

function skillsView(): SkillsView {
  return {
    skills: [],
    selectedSkillIds: new Set(["refactor"]),
    skillErrors: [],
    skillNotices: [],
    skillsLoading: false,
  };
}

function connectionView(): ConnectionView {
  return { bridgeStatus: "Connected", socketStatus: "Ready" };
}

describe("shell view models", () => {
  it("carry per-session run identity and status", () => {
    const view = sessionView();

    expect(view.activeRunIdBySession["session-1"]).toBe("run-1");
    expect(view.runStatusBySession["session-1"]).toBe("running");
    expect(view.isNewSessionDraft).toBe(true);
  });

  it("expose the composer's resolved working directory and preset", () => {
    const view = composerView();

    expect(view.defaultWorkingDirectory).toBe("D:/workspace");
    expect(view.permissionPreset).toBe("default");
    expect(view.sandboxSetupStatus).toBe("Sandbox ready");
    expect(view.canSend).toBe(true);
  });

  it("keep selected skills as a set so toggling stays O(1)", () => {
    const view = skillsView();

    expect(view.selectedSkillIds.has("refactor")).toBe(true);
    expect(view.selectedSkillIds.has("missing")).toBe(false);
  });

  it("carry conversation data without behaviour", () => {
    const messages: ChatMessage[] = [
      {
        id: "m1",
        role: "user",
        kind: "normal",
        text: "hi",
        sequence: 1,
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const view = conversationView(messages);

    expect(view.messages).toBe(messages);
    expect(view.isStreaming).toBe(false);
    expect(view.approvals).toEqual([]);

    // Behaviour is published through the action group, not the view model.
    const actionNames = Object.keys(view);
    expect(actionNames).not.toContain("approvePlan");
    expect(actionNames).not.toContain("cancelRun");
  });

  it("keep the connection view to status text only", () => {
    const view = connectionView();

    expect(Object.keys(view).sort()).toEqual(["bridgeStatus", "socketStatus"]);
  });

  it("declare action groups as callables independent of the view", () => {
    const actions: ComposerActions = {
      chooseDirectory: () => undefined,
      workingDirectoryChange: () => undefined,
      submit: () => undefined,
      promptChange: () => undefined,
      sendModeChange: () => undefined,
      permissionPresetChange: () => undefined,
      sandboxSetup: () => undefined,
      steerInput: () => undefined,
      cancelInput: () => undefined,
    };

    expect(Object.values(actions).every((value) => typeof value === "function")).toBe(
      true,
    );
  });

  it("carry queued inputs as data, with no delivery behaviour in the view", () => {
    const view = composerView();

    expect(view.pendingInputs).toHaveLength(1);
    expect(view.pendingInputs[0]?.delivery).toBe("queue");
    expect(Object.keys(view)).not.toContain("steerInput");
    expect(Object.keys(view)).not.toContain("cancelInput");
  });
});
