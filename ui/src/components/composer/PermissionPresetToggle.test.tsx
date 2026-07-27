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

    const fullAccess = getByRole("button", { name: "Full Access" });
    expect(fullAccess).toHaveAttribute(
      "title",
      "Run eligible tool calls without approval. No sandbox is active.",
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

    expect(getByRole("button", { name: "Full Access" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(getByRole("button", { name: "Default" })).toBeDisabled();
    expect(getByRole("button", { name: "Full Access" })).toBeDisabled();
  });
});
