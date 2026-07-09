import type { RefObject } from "react";
import { MessageBubble } from "./MessageBubble";
import { ToolRunGroup } from "./ToolCard";
import type { ChatMessage } from "../../types/chat";

type MessageListProps = {
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function MessageList({ messages, messagesRef, isStreaming, onApprovePlan }: MessageListProps) {
  const items = groupToolRuns(messages);

  return (
    <div className="messages" ref={messagesRef}>
      {messages.length === 0 && (
        <article className="message agent empty">
          <p>This session is empty. Send a prompt to start a persisted conversation.</p>
        </article>
      )}
      {items.map((item) => (
        item.kind === "tool_group" ? (
          <ToolRunGroup key={item.messages.map((message) => message.id).join(":")} messages={item.messages} />
        ) : (
          <MessageBubble
            key={item.message.id}
            message={item.message}
            isStreaming={isStreaming}
            onApprovePlan={onApprovePlan}
          />
        )
      ))}
    </div>
  );
}

type MessageListItem =
  | { kind: "message"; message: ChatMessage }
  | { kind: "tool_group"; messages: ChatMessage[] };

function groupToolRuns(messages: ChatMessage[]): MessageListItem[] {
  const items: MessageListItem[] = [];
  let toolGroup: ChatMessage[] = [];

  for (const message of messages) {
    if (message.kind === "tool_run") {
      toolGroup.push(message);
      continue;
    }

    if (toolGroup.length > 0) {
      items.push({ kind: "tool_group", messages: toolGroup });
      toolGroup = [];
    }

    items.push({ kind: "message", message });
  }

  if (toolGroup.length > 0) {
    items.push({ kind: "tool_group", messages: toolGroup });
  }

  return items;
}
