import { CheckCircle2, Play, RotateCcw } from "lucide-react";
import type { ChatMessage } from "../../types/chat";
import { formatPlanStatus } from "../../utils/format";

type PlanBubbleProps = {
  message: ChatMessage;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function PlanBubble({ message, isStreaming, onApprovePlan }: PlanBubbleProps) {
  const canRetry = message.plan_status === "failed";
  return (
    <div className="plan-bubble">
      <div className="plan-header">
        <span>
          <CheckCircle2 size={15} />
          Plan
        </span>
        <em className={`plan-status ${message.plan_status ?? "pending"}`}>{formatPlanStatus(message.plan_status)}</em>
      </div>
      <p>{message.text || "..."}</p>
      <div className="plan-actions">
        <button
          type="button"
          onClick={() => onApprovePlan(message)}
          disabled={isStreaming || (message.plan_status !== "pending" && !canRetry)}
        >
          {canRetry ? <RotateCcw size={15} /> : <Play size={15} />}
          {canRetry ? "Retry plan" : "Approve plan"}
        </button>
      </div>
    </div>
  );
}
