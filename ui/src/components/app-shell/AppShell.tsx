import type { FormEvent, RefObject } from "react";
import { ConversationPanel } from "../conversation/ConversationPanel";
import { Sidebar } from "./Sidebar";
import { FloatingInspector } from "./FloatingInspector";
import { Topbar } from "./Topbar";
import type { ApprovalDecision, ChatMessage, SendMode, ToolApprovalRequest } from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type AppShellProps = {
  sessions: SessionSummary[];
  activeSession: SessionSummary | null;
  activeSessionId: string | null;
  isNewSessionDraft: boolean;
  displayedWorkingDirectory: string;
  defaultWorkingDirectory: string;
  editingSessionId: string | null;
  editingTitle: string;
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  bridgeStatus: string;
  socketStatus: string;
  prompt: string;
  sendMode: SendMode;
  isStreaming: boolean;
  canSend: boolean;
  approvals: ToolApprovalRequest[];
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onStartRename(session: SessionSummary): void;
  onEditingTitleChange(title: string): void;
  onCommitRename(sessionId: string): void;
  onCancelRename(): void;
  onDeleteSession(sessionId: string): void;
  onChooseDirectory(): void;
  onWorkingDirectoryChange(workingDirectory: string): void;
  onRunBridgeCheck(): void;
  onSubmit(event: FormEvent<HTMLFormElement>): void;
  onPromptChange(prompt: string): void;
  onSendModeChange(sendMode: SendMode): void;
  onApprovePlan(message: ChatMessage): void;
  onRespondToApproval(approval: ToolApprovalRequest, decision: ApprovalDecision): void;
  onCancelRun(): void;
};

export function AppShell({
  sessions,
  activeSession,
  activeSessionId,
  isNewSessionDraft,
  displayedWorkingDirectory,
  defaultWorkingDirectory,
  editingSessionId,
  editingTitle,
  messages,
  messagesRef,
  bridgeStatus,
  socketStatus,
  prompt,
  sendMode,
  isStreaming,
  canSend,
  approvals,
  onCreateSession,
  onSelectSession,
  onStartRename,
  onEditingTitleChange,
  onCommitRename,
  onCancelRename,
  onDeleteSession,
  onChooseDirectory,
  onWorkingDirectoryChange,
  onRunBridgeCheck,
  onSubmit,
  onPromptChange,
  onSendModeChange,
  onApprovePlan,
  onRespondToApproval,
  onCancelRun,
}: AppShellProps) {
  const title = isNewSessionDraft ? "New session" : activeSession?.title ?? "Agent workspace";

  return (
    <main className="app-shell">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        editingSessionId={editingSessionId}
        editingTitle={editingTitle}
        isStreaming={isStreaming}
        onCreateSession={onCreateSession}
        onSelectSession={onSelectSession}
        onStartRename={onStartRename}
        onEditingTitleChange={onEditingTitleChange}
        onCommitRename={onCommitRename}
        onCancelRename={onCancelRename}
        onDeleteSession={onDeleteSession}
      />

      <section className="workspace">
        <Topbar
          displayedWorkingDirectory={displayedWorkingDirectory}
          title={title}
          onRunBridgeCheck={onRunBridgeCheck}
        />
        <FloatingInspector bridgeStatus={bridgeStatus} socketStatus={socketStatus} sessionCount={sessions.length} />

        <section className="workspace-main">
          <ConversationPanel
            activeSession={activeSession}
            isNewSessionDraft={isNewSessionDraft}
            messages={messages}
            messagesRef={messagesRef}
            socketStatus={socketStatus}
            displayedWorkingDirectory={displayedWorkingDirectory}
            defaultWorkingDirectory={defaultWorkingDirectory}
            prompt={prompt}
            sendMode={sendMode}
            isStreaming={isStreaming}
            canSend={canSend}
            approvals={approvals}
            onChooseDirectory={onChooseDirectory}
            onWorkingDirectoryChange={onWorkingDirectoryChange}
            onSubmit={onSubmit}
            onPromptChange={onPromptChange}
            onSendModeChange={onSendModeChange}
            onApprovePlan={onApprovePlan}
            onRespondToApproval={onRespondToApproval}
            onCancelRun={onCancelRun}
          />
        </section>
      </section>
    </main>
  );
}
