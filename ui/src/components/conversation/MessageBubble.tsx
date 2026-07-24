import { Bot } from "lucide-react";
import { PlanBubble } from "./PlanBubble";
import { ToolCard } from "./ToolCard";
import type { ChatMessage } from "../../types/chat";

type MessageBubbleProps = {
  message: ChatMessage;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function MessageBubble({ message, isStreaming, onApprovePlan }: MessageBubbleProps) {
  return (
    <article
      className={`message ${message.role} ${message.kind === "plan" ? "plan" : ""} ${
        message.kind === "tool_run" ? "tool-run" : ""
      }`}
    >
      {message.role === "agent" && message.kind !== "plan" && (
        <div className="avatar">
          <Bot size={15} />
        </div>
      )}
      {message.kind === "tool_run" ? (
        <ToolCard metadata={message.metadata ?? null} />
      ) : message.kind === "plan" ? (
        <PlanBubble message={message} isStreaming={isStreaming} onApprovePlan={onApprovePlan} />
      ) : (
        <p className="message-bubble">{message.text || "..."}</p>
      )}
    </article>
  );
}
