import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PermissionPresetToggle } from "./PermissionPresetToggle";

afterEach(cleanup);

describe("PermissionPresetToggle", () => {
  it("selects full access and exposes the no-sandbox warning", () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <PermissionPresetToggle
        permissionPreset="default"
        disabled={false}
        onChange={onChange}
      />,
    );

    const fullAccess = getByRole("button", { name: "完全访问" });
    expect(fullAccess).toHaveAttribute(
      "title",
      "无需批准直接运行工具调用，不启用沙箱。",
    );
    fireEvent.click(fullAccess);
    expect(onChange).toHaveBeenCalledWith("full_access");
  });

  it("marks the active preset and disables changes during a run", () => {
    const { getByRole } = render(
      <PermissionPresetToggle
        permissionPreset="full_access"
        disabled
        onChange={vi.fn()}
      />,
    );

    expect(getByRole("button", { name: "完全访问" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(getByRole("button", { name: "沙箱" })).toBeDisabled();
    expect(getByRole("button", { name: "完全访问" })).toBeDisabled();
  });

  it("runs explicit sandbox setup and exposes its current status", () => {
    const onSetupSandbox = vi.fn();
    const { getByRole } = render(
      <PermissionPresetToggle
        permissionPreset="default"
        disabled={false}
        onChange={vi.fn()}
        setupStatus="Sandbox ready (windows-appcontainer)"
        onSetupSandbox={onSetupSandbox}
      />,
    );

    const setup = getByRole("button", { name: "准备沙箱" });
    expect(setup).toHaveAttribute(
      "title",
      "Sandbox ready (windows-appcontainer)",
    );
    fireEvent.click(setup);
    expect(onSetupSandbox).toHaveBeenCalledOnce();
  });
});
