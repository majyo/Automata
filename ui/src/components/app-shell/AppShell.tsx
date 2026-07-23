import { useEffect, useState } from "react";
import type { FormEvent, RefObject } from "react";
import { ConversationPanel } from "../conversation/ConversationPanel";
import { Sidebar } from "./Sidebar";
import { InspectorSheet } from "./InspectorSheet";
import { Topbar } from "./Topbar";
import type {
  ApprovalDecision,
  ChatMessage,
  PersistedRunStatus,
  SendMode,
  ToolApprovalRequest,
} from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "automata-theme";

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
  activeRunIdBySession: Record<string, string>;
  runStatusBySession: Record<string, PersistedRunStatus>;
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
  activeRunIdBySession,
  runStatusBySession,
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
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const [isInspectorOpen, setIsInspectorOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage may be unavailable in some webview contexts; theme still applies for the session.
    }
  }, [theme]);

  const title = isNewSessionDraft ? "New session" : activeSession?.title ?? "Agent workspace";

  return (
    <main className="app-shell">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        editingSessionId={editingSessionId}
        editingTitle={editingTitle}
        activeRunIdBySession={activeRunIdBySession}
        runStatusBySession={runStatusBySession}
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
          title={title}
          displayedWorkingDirectory={displayedWorkingDirectory}
          socketStatus={socketStatus}
          theme={theme}
          isInspectorOpen={isInspectorOpen}
          onRunBridgeCheck={onRunBridgeCheck}
          onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
          onToggleInspector={() => setIsInspectorOpen((open) => !open)}
        />

        <div className="workspace-body">
          <ConversationPanel
            isNewSessionDraft={isNewSessionDraft}
            messages={messages}
            messagesRef={messagesRef}
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

          <InspectorSheet
            bridgeStatus={bridgeStatus}
            socketStatus={socketStatus}
            sessionCount={sessions.length}
            open={isInspectorOpen}
            onClose={() => setIsInspectorOpen(false)}
          />
        </div>
      </section>
    </main>
  );
}

function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}
