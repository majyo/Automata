import { useState } from "react";
import { Check, Copy, UserRound } from "lucide-react";
import { AutomataMark } from "../app-shell/AutomataMark";
import { MarkdownContent } from "./MarkdownContent";
import { PlanBubble } from "./PlanBubble";
import { ToolCard } from "./ToolCard";
import type { ChatMessage } from "../../types/chat";

type MessageBubbleProps = {
  message: ChatMessage;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function MessageBubble({
  message,
  isStreaming,
  onApprovePlan,
}: MessageBubbleProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">(
    "idle",
  );
  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }
  if (message.kind === "tool_run")
    return (
      <article className="message tool-run">
        <ToolCard metadata={message.metadata ?? null} />
      </article>
    );
  if (message.kind === "plan")
    return (
      <article className="message plan">
        <PlanBubble
          message={message}
          isStreaming={isStreaming}
          onApprovePlan={onApprovePlan}
        />
      </article>
    );

  const user = message.role === "user";
  const date = message.created_at ? new Date(message.created_at) : null;
  return (
    <article className={`message ${message.role}`}>
      <div className={`avatar ${user ? "user-avatar" : ""}`}>
        {user ? <UserRound size={16} /> : <AutomataMark />}
      </div>
      <div className="message-content">
        <div className="message-byline">
          <strong>{user ? "你" : "AUTOMATA"}</strong>
          <span>{user ? "任务" : "编程助手"}</span>
          {date && !Number.isNaN(date.getTime()) && (
            <time dateTime={message.created_at}>
              {date.toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              })}
            </time>
          )}
        </div>
        {user ? (
          <p className="message-bubble">{message.text || "…"}</p>
        ) : (
          <MarkdownContent
            className="message-bubble"
            text={message.text || "正在处理…"}
          />
        )}
        {!user && message.text && (
          <div className="message-actions">
            <span className="message-action-rule" />
            <button
              className="copy-message"
              type="button"
              onClick={() => void copyMessage()}
              aria-label="复制回复"
            >
              {copyState === "copied" ? (
                <Check size={13} />
              ) : (
                <Copy size={13} />
              )}
              <span>
                {copyState === "copied"
                  ? "已复制"
                  : copyState === "failed"
                    ? "复制失败，请重试"
                    : "复制"}
              </span>
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
