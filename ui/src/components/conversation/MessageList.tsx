import type { RefObject } from "react";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "../../types/chat";

type MessageListProps = {
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function MessageList({ messages, messagesRef, isStreaming, onApprovePlan }: MessageListProps) {
  return (
    <div className="messages" ref={messagesRef}>
      {messages.length === 0 && (
        <article className="message agent empty">
          <p>This session is empty. Send a prompt to start a persisted conversation.</p>
        </article>
      )}
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          isStreaming={isStreaming}
          onApprovePlan={onApprovePlan}
        />
      ))}
    </div>
  );
}
