import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PendingInputList } from "./PendingInputList";
import type { PendingInput } from "../../../types/chat";

afterEach(cleanup);

function input(overrides: Partial<PendingInput> = {}): PendingInput {
  return {
    requestId: "request-1",
    sessionId: "session-1",
    prompt: "then run the tests",
    delivery: "queue",
    status: "pending",
    inputId: "input-1",
    ...overrides,
  };
}

function renderList(
  inputs: PendingInput[],
  overrides: Partial<Parameters<typeof PendingInputList>[0]> = {},
) {
  const handlers = {
    onSteer: vi.fn(),
    onCancel: vi.fn(),
    onRequeue: vi.fn(),
    onDismiss: vi.fn(),
  };
  const result = render(
    <PendingInputList inputs={inputs} canSteer {...handlers} {...overrides} />,
  );
  return { ...result, ...handlers };
}

describe("PendingInputList", () => {
  it("renders nothing while no input is waiting", () => {
    const { queryByLabelText } = renderList([]);

    expect(queryByLabelText("排队中的消息")).toBeNull();
  });

  it("offers 插话 and 删除 for an acknowledged queued message", () => {
    const entry = input();
    const { getByText, getByRole, onSteer, onCancel } = renderList([entry]);

    expect(getByText("then run the tests")).toBeDefined();
    fireEvent.click(getByRole("button", { name: "插话" }));
    fireEvent.click(getByRole("button", { name: "删除" }));

    expect(onSteer).toHaveBeenCalledWith(entry);
    expect(onCancel).toHaveBeenCalledWith(entry);
  });

  it("waits for the input id before either button can act", () => {
    const { getByRole } = renderList([
      input({ status: "sending", inputId: undefined }),
    ]);

    expect(getByRole("button", { name: "插话" })).toBeDisabled();
    expect(getByRole("button", { name: "删除" })).toBeDisabled();
  });

  it("blocks a second steering attempt while one is in flight", () => {
    const { getByRole, getByText } = renderList([
      input({ steerRequestId: "steer-request-1" }),
    ]);

    expect(getByText("插话中")).toBeDefined();
    expect(getByRole("button", { name: "插话" })).toBeDisabled();
  });

  it("does not steer when the session has no run to steer into", () => {
    const { getByRole } = renderList([input()], { canSteer: false });

    expect(getByRole("button", { name: "插话" })).toBeDisabled();
    // Withdrawal stays available: it never needs an active Run.
    expect(getByRole("button", { name: "删除" })).toBeEnabled();
  });

  it("keeps a withdrawn message with its text and offers 重新排队", () => {
    const entry = input({ status: "cancelled", cancelReason: "predecessor_failed" });
    const { getByText, getByRole, onRequeue, onDismiss } = renderList([entry]);

    expect(getByText("then run the tests")).toBeDefined();
    expect(getByText("已取消：前置任务失败")).toBeDefined();
    expect(getByRole("button", { name: "重新排队" })).toBeEnabled();
    fireEvent.click(getByRole("button", { name: "重新排队" }));
    fireEvent.click(getByRole("button", { name: "关闭" }));

    expect(onRequeue).toHaveBeenCalledWith(entry);
    expect(onDismiss).toHaveBeenCalledWith(entry);
  });

  it("explains why the queue is paused", () => {
    const { getByText } = renderList([input()], { pausedReason: "cancelled" });

    expect(getByText("上次任务已取消，排队已暂停")).toBeDefined();
  });

  it("explains the default waiting rule while a run is active", () => {
    const { getByText } = renderList([input()]);

    expect(getByText("当前任务结束后依次执行")).toBeDefined();
  });
});
