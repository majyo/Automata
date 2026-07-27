import { ShieldAlert, ShieldCheck } from "lucide-react";
import type { PermissionPreset } from "../../types/session";

type PermissionPresetToggleProps = {
  permissionPreset: PermissionPreset;
  disabled: boolean;
  onChange(permissionPreset: PermissionPreset): void;
};

export function PermissionPresetToggle({
  permissionPreset,
  disabled,
  onChange,
}: PermissionPresetToggleProps) {
  return (
    <div className="mode-toggle permission-toggle" role="group" aria-label="Tool permissions">
      <button
        type="button"
        className={permissionPreset === "default" ? "active" : ""}
        onClick={() => onChange("default")}
        disabled={disabled}
        aria-pressed={permissionPreset === "default"}
        title="Require approval for write, command, destructive, and external tool calls"
      >
        <ShieldCheck size={14} />
        Default
      </button>
      <button
        type="button"
        className={permissionPreset === "full_access" ? "active full-access" : "full-access"}
        onClick={() => onChange("full_access")}
        disabled={disabled}
        aria-pressed={permissionPreset === "full_access"}
        title="Run eligible tool calls without approval. No sandbox is active."
      >
        <ShieldAlert size={14} />
        Full Access
      </button>
    </div>
  );
}
