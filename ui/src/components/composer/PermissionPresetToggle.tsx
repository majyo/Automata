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
    <div className="mode-toggle permission-toggle" role="group" aria-label="工具权限">
      <button
        type="button"
        className={permissionPreset === "default" ? "active" : ""}
        onClick={() => onChange("default")}
        disabled={disabled}
        aria-pressed={permissionPreset === "default"}
        title="在受管沙箱中运行已批准的工具，网络访问受限"
      >
        <ShieldCheck size={14} />
        沙箱
      </button>
      {onSetupSandbox ? (
        <button
          type="button"
          onClick={onSetupSandbox}
          disabled={disabled}
          aria-label="准备沙箱"
          title={setupStatus || "准备受管沙箱；Windows 可能会请求管理员权限"}
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
        title="无需批准直接运行工具调用，不启用沙箱。"
      >
        <ShieldAlert size={14} />
        完全访问
      </button>
    </div>
  );
}
