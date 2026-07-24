import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Play, RotateCcw } from "lucide-react";
import { MarkdownContent } from "./MarkdownContent";
import type { ChatMessage } from "../../types/chat";
import { formatPlanStatus } from "../../utils/format";

type PlanBubbleProps = {
  message: ChatMessage;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function PlanBubble({ message, isStreaming, onApprovePlan }: PlanBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const canRetry = message.plan_status === "failed";
  return (
    <div className={`plan-bubble ${isExpanded ? "" : "collapsed"}`}>
      <button
        className="plan-header"
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <span className="plan-header-title">
          <CheckCircle2 size={16} />
          Plan
        </span>
        <span className="plan-header-meta">
          <em className={`plan-status ${message.plan_status ?? "pending"}`}>{formatPlanStatus(message.plan_status)}</em>
          {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
      </button>
      {isExpanded && (
        <>
          <MarkdownContent className="plan-text" text={message.text || "..."} />
          <div className="plan-actions">
            <button
              className="button button-filled"
              type="button"
              onClick={() => onApprovePlan(message)}
              disabled={isStreaming || (message.plan_status !== "pending" && !canRetry)}
            >
              {canRetry ? <RotateCcw size={15} /> : <Play size={15} />}
              {canRetry ? "Retry plan" : "Approve plan"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
