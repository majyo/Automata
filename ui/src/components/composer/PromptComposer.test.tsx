import { cleanup, fireEvent, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptComposer } from "./PromptComposer";

afterEach(cleanup);

function renderComposer(
  overrides: Partial<ComponentProps<typeof PromptComposer>> = {},
) {
  const onSubmit = vi.fn((event) => event.preventDefault());
  const onCancel = vi.fn();
  const props: ComponentProps<typeof PromptComposer> = {
    prompt: "检查项目入口",
    sendMode: "execute",
    permissionPreset: "default",
    permissionUpdating: false,
    sandboxSetupStatus: "",
    isStreaming: false,
    canSend: true,
    pendingInputs: [],
    skills: [],
    selectedSkillIds: new Set(),
    skillErrors: [],
    skillNotices: [],
    skillsLoading: false,
    onPromptChange: vi.fn(),
    onSendModeChange: vi.fn(),
    onPermissionPresetChange: vi.fn(),
    onSandboxSetup: vi.fn(),
    onCancel,
    onSteerInput: vi.fn(),
    onCancelInput: vi.fn(),
    onRequeueInput: vi.fn(),
    onDismissInput: vi.fn(),
    onToggleSkill: vi.fn(),
    onToggleSkillEnabled: vi.fn(async () => {}),
    onRefreshSkills: vi.fn(),
    ...overrides,
  };
  const result = render(
    <form onSubmit={onSubmit}>
      <PromptComposer {...props} />
    </form>,
  );
  return {
    ...result,
    onSubmit,
    onCancel,
    input: result.getByRole("textbox", { name: "输入任务消息" }),
  };
}

describe("PromptComposer keyboard submission", () => {
  it("submits a ready prompt with Enter", () => {
    const { input, onSubmit } = renderComposer();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("keeps Shift+Enter and IME confirmation out of the submit path", () => {
    const { input, onSubmit } = renderComposer();
    expect(fireEvent.keyDown(input, { key: "Enter", shiftKey: true })).toBe(
      true,
    );
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not submit when sending is unavailable", () => {
    const { input, onSubmit, getByRole } = renderComposer({ canSend: false });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(getByRole("button", { name: "发送消息" })).toBeDisabled();
  });

  it("queues with Enter during a run and still exposes cancellation", () => {
    const { input, onSubmit, onCancel, getByRole } = renderComposer({
      isStreaming: true,
    });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(getByRole("button", { name: "停止任务" }));
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("keeps the queue button disabled while there is nothing to send", () => {
    const { getByRole } = renderComposer({ isStreaming: true, canSend: false });
    expect(getByRole("button", { name: "排队发送消息" })).toBeDisabled();
  });
});
