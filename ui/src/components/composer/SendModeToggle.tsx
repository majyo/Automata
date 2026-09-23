import { CheckCircle2, Play } from "lucide-react";
import type { SendMode } from "../../types/chat";

type SendModeToggleProps = {
  sendMode: SendMode;
  disabled: boolean;
  onChange(sendMode: SendMode): void;
};

export function SendModeToggle({ sendMode, disabled, onChange }: SendModeToggleProps) {
  return (
    <div className="mode-toggle" role="group" aria-label="发送方式">
      <button
        type="button"
        className={sendMode === "execute" ? "active" : ""}
        onClick={() => onChange("execute")}
        disabled={disabled}
        aria-pressed={sendMode === "execute"}
        title="直接执行任务"
      >
        <Play size={14} />
        执行
      </button>
      <button
        type="button"
        className={sendMode === "plan" ? "active" : ""}
        onClick={() => onChange("plan")}
        disabled={disabled}
        aria-pressed={sendMode === "plan"}
        title="先生成计划，确认后再执行"
      >
        <CheckCircle2 size={14} />
        计划
      </button>
    </div>
  );
}
