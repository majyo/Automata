import { Settings2, ShieldAlert, ShieldCheck } from "lucide-react";
import type { PermissionPreset } from "../../types/session";

type PermissionPresetToggleProps = {
  permissionPreset: PermissionPreset;
  disabled: boolean;
  onChange(permissionPreset: PermissionPreset): void;
  setupStatus?: string;
  onSetupSandbox?(): void;
};

export function PermissionPresetToggle({
  permissionPreset,
  disabled,
  onChange,
  setupStatus = "",
  onSetupSandbox,
}: PermissionPresetToggleProps) {
  return (
    <div className="mode-toggle permission-toggle" role="group" aria-label="Tool permissions">
      <button
        type="button"
        className={permissionPreset === "default" ? "active" : ""}
        onClick={() => onChange("default")}
        disabled={disabled}
        aria-pressed={permissionPreset === "default"}
        title="Run approved tools in the managed workspace sandbox with restricted network access"
      >
        <ShieldCheck size={14} />
        Default
      </button>
      {onSetupSandbox ? (
        <button
          type="button"
          onClick={onSetupSandbox}
          disabled={disabled}
          aria-label="Prepare Sandbox"
          title={setupStatus || "Prepare the managed sandbox; Windows may request elevation"}
        >
          <Settings2 size={14} />
        </button>
      ) : null}
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
