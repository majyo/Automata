import { CheckCircle2, Play } from "lucide-react";
import type { SendMode } from "../../types/chat";

type SendModeToggleProps = {
  sendMode: SendMode;
  disabled: boolean;
  onChange(sendMode: SendMode): void;
};

export function SendModeToggle({ sendMode, disabled, onChange }: SendModeToggleProps) {
  return (
    <div className="mode-toggle" role="group" aria-label="Prompt mode">
      <button
        type="button"
        className={sendMode === "execute" ? "active" : ""}
        onClick={() => onChange("execute")}
        disabled={disabled}
        aria-pressed={sendMode === "execute"}
        title="Execute prompt"
      >
        <Play size={14} />
        Execute
      </button>
      <button
        type="button"
        className={sendMode === "plan" ? "active" : ""}
        onClick={() => onChange("plan")}
        disabled={disabled}
        aria-pressed={sendMode === "plan"}
        title="Generate a plan"
      >
        <CheckCircle2 size={14} />
        Plan
      </button>
    </div>
  );
}
