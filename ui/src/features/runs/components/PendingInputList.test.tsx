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

describe("PendingInputList", () => {
  it("renders nothing while no input is waiting", () => {
    const { queryByLabelText } = render(
      <PendingInputList
        inputs={[]}
        canSteer
        onSteer={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(queryByLabelText("排队中的消息")).toBeNull();
  });

  it("offers 插话 and 删除 for an acknowledged queued message", () => {
    const onSteer = vi.fn();
    const onCancel = vi.fn();
    const entry = input();
    const { getByText, getByRole } = render(
      <PendingInputList
        inputs={[entry]}
        canSteer
        onSteer={onSteer}
        onCancel={onCancel}
      />,
    );

    expect(getByText("then run the tests")).toBeDefined();
    fireEvent.click(getByRole("button", { name: "插话" }));
    fireEvent.click(getByRole("button", { name: "删除" }));

    expect(onSteer).toHaveBeenCalledWith(entry);
    expect(onCancel).toHaveBeenCalledWith(entry);
  });

  it("waits for the input id before either button can act", () => {
    const { getByRole } = render(
      <PendingInputList
        inputs={[input({ status: "sending", inputId: undefined })]}
        canSteer
        onSteer={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(getByRole("button", { name: "插话" })).toBeDisabled();
    expect(getByRole("button", { name: "删除" })).toBeDisabled();
  });

  it("blocks a second steering attempt while one is in flight", () => {
    const { getByRole, getByText } = render(
      <PendingInputList
        inputs={[input({ steerRequestId: "steer-request-1" })]}
        canSteer
        onSteer={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(getByText("插话中")).toBeDefined();
    expect(getByRole("button", { name: "插话" })).toBeDisabled();
  });

  it("does not steer when the session has no run to steer into", () => {
    const { getByRole } = render(
      <PendingInputList
        inputs={[input()]}
        canSteer={false}
        onSteer={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(getByRole("button", { name: "插话" })).toBeDisabled();
    // Withdrawal stays available: it never needs an active Run.
    expect(getByRole("button", { name: "删除" })).toBeEnabled();
  });
});
