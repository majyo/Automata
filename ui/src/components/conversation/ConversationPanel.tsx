import type { FormEvent, RefObject } from "react";
import { Activity } from "lucide-react";
import { MessageList } from "./MessageList";
import { PromptComposer } from "../composer/PromptComposer";
import { WorkspacePicker } from "../sessions/WorkspacePicker";
import { ToolApprovalCard } from "./ToolApprovalCard";
import type { ApprovalDecision, ChatMessage, SendMode, ToolApprovalRequest } from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type ConversationPanelProps = {
  activeSession: SessionSummary | null;
  isNewSessionDraft: boolean;
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  socketStatus: string;
  displayedWorkingDirectory: string;
  defaultWorkingDirectory: string;
  prompt: string;
  sendMode: SendMode;
  isStreaming: boolean;
  canSend: boolean;
  approvals: ToolApprovalRequest[];
  onChooseDirectory(): void;
  onWorkingDirectoryChange(workingDirectory: string): void;
  onSubmit(event: FormEvent<HTMLFormElement>): void;
  onPromptChange(prompt: string): void;
  onSendModeChange(sendMode: SendMode): void;
  onApprovePlan(message: ChatMessage): void;
  onRespondToApproval(approval: ToolApprovalRequest, decision: ApprovalDecision): void;
  onCancelRun(): void;
};

export function ConversationPanel({
  activeSession,
  isNewSessionDraft,
  messages,
  messagesRef,
  socketStatus,
  displayedWorkingDirectory,
  defaultWorkingDirectory,
  prompt,
  sendMode,
  isStreaming,
  canSend,
  approvals,
  onChooseDirectory,
  onWorkingDirectoryChange,
  onSubmit,
  onPromptChange,
  onSendModeChange,
  onApprovePlan,
  onRespondToApproval,
  onCancelRun,
}: ConversationPanelProps) {
  return (
    <section className="conversation-panel" aria-label="Agent conversation">
      <div className="panel-header">
        <div>
          <span className="eyebrow">Session</span>
          <h2>
            {isNewSessionDraft
              ? "No session selected"
              : activeSession
                ? `${activeSession.message_count} saved messages`
                : "Loading session"}
          </h2>
        </div>
        <span className="status-pill" title={socketStatus}>
          <Activity size={14} />
          {socketStatus}
        </span>
      </div>

      {isNewSessionDraft ? (
        <div className="new-session-stage">
          <form className="new-session-dialog" onSubmit={onSubmit}>
            <span className="eyebrow">New session</span>
            <h2>输入一条消息来开始新的会话</h2>
            <WorkspacePicker
              displayedWorkingDirectory={displayedWorkingDirectory}
              defaultWorkingDirectory={defaultWorkingDirectory}
              isNewSessionDraft={isNewSessionDraft}
              isStreaming={isStreaming}
              onChooseDirectory={onChooseDirectory}
              onWorkingDirectoryChange={onWorkingDirectoryChange}
            />
            <PromptComposer
              autoFocus
              draft
              prompt={prompt}
              sendMode={sendMode}
              isStreaming={isStreaming}
              canSend={canSend}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onCancel={onCancelRun}
            />
          </form>
        </div>
      ) : (
        <>
          <MessageList
            messages={messages}
            messagesRef={messagesRef}
            isStreaming={isStreaming}
            onApprovePlan={onApprovePlan}
          />

          <form className="composer-form" onSubmit={onSubmit}>
            {approvals[0] ? (
              <ToolApprovalCard approval={approvals[0]} onRespond={onRespondToApproval} />
            ) : null}
            <PromptComposer
              prompt={prompt}
              sendMode={sendMode}
              isStreaming={isStreaming}
              canSend={canSend}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onCancel={onCancelRun}
            />
          </form>
        </>
      )}
    </section>
  );
}
