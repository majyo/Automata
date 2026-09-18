import type { RefObject } from "react";
import { AutomataMark } from "../../../components/app-shell/AutomataMark";
import { MessageBubble } from "./MessageBubble";
import { ToolRunGroup } from "./ToolCard";
import type { ChatMessage } from "../../../types/chat";

type MessageListProps = {
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  isStreaming: boolean;
  onApprovePlan(message: ChatMessage): void;
};

export function MessageList({
  messages,
  messagesRef,
  isStreaming,
  onApprovePlan,
}: MessageListProps) {
  const items = groupToolRuns(messages);

  return (
    <div
      className="messages"
      ref={messagesRef}
      role="log"
      aria-label="会话记录"
      aria-busy={isStreaming}
    >
      {messages.length > 0 && (
        <div className="conversation-start">
          <span />
          会话记录
          <span />
        </div>
      )}
      {messages.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">
            <AutomataMark />
          </div>
          <h3>从一个具体任务开始</h3>
          <p>在下方输入问题、需求或需要排查的现象。</p>
        </div>
      )}
      {items.map((item) =>
        item.kind === "tool_group" ? (
          <ToolRunGroup
            key={item.messages.map((message) => message.id).join(":")}
            messages={item.messages}
          />
        ) : (
          <MessageBubble
            key={item.message.id}
            message={item.message}
            isStreaming={isStreaming}
            onApprovePlan={onApprovePlan}
          />
        ),
      )}
      {messages.length > 0 && (
        <div className="conversation-end">
          <span className="tiny-square" />
          {isStreaming ? "正在处理…" : "已显示全部记录"}
        </div>
      )}
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
