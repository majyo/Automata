import { Plus, Sparkles, TerminalSquare } from "lucide-react";
import { SessionList } from "../sessions/SessionList";
import { WorkspacePicker } from "../sessions/WorkspacePicker";
import type { SessionSummary } from "../../types/session";

type SidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  editingSessionId: string | null;
  editingTitle: string;
  displayedWorkingDirectory: string;
  defaultWorkingDirectory: string;
  isNewSessionDraft: boolean;
  isStreaming: boolean;
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onStartRename(session: SessionSummary): void;
  onEditingTitleChange(title: string): void;
  onCommitRename(sessionId: string): void;
  onCancelRename(): void;
  onDeleteSession(sessionId: string): void;
  onChooseDirectory(): void;
  onWorkingDirectoryChange(workingDirectory: string): void;
};

export function Sidebar({
  sessions,
  activeSessionId,
  editingSessionId,
  editingTitle,
  displayedWorkingDirectory,
  defaultWorkingDirectory,
  isNewSessionDraft,
  isStreaming,
  onCreateSession,
  onSelectSession,
  onStartRename,
  onEditingTitleChange,
  onCommitRename,
  onCancelRename,
  onDeleteSession,
  onChooseDirectory,
  onWorkingDirectoryChange,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <Sparkles size={20} />
        </div>
        <div>
          <strong>Automata</strong>
          <span>Local coding agent</span>
        </div>
      </div>

      <div className="sidebar-toolbar">
        <span>Sessions</span>
        <button className="icon-button small" onClick={onCreateSession} aria-label="New session">
          <Plus size={16} />
        </button>
      </div>

      <SessionList
        sessions={sessions}
        activeSessionId={activeSessionId}
        editingSessionId={editingSessionId}
        editingTitle={editingTitle}
        disabled={isStreaming}
        onSelect={onSelectSession}
        onStartRename={onStartRename}
        onEditingTitleChange={onEditingTitleChange}
        onCommitRename={onCommitRename}
        onCancelRename={onCancelRename}
        onDelete={onDeleteSession}
      />

      <div className="sidebar-footer">
        <WorkspacePicker
          displayedWorkingDirectory={displayedWorkingDirectory}
          defaultWorkingDirectory={defaultWorkingDirectory}
          isNewSessionDraft={isNewSessionDraft}
          isStreaming={isStreaming}
          onChooseDirectory={onChooseDirectory}
          onWorkingDirectoryChange={onWorkingDirectoryChange}
        />

        <div className="sidebar-footer-actions">
          <button className="icon-button" onClick={onCreateSession} aria-label="New session">
            <Plus size={18} />
          </button>
          <button className="icon-button" aria-label="Open terminal">
            <TerminalSquare size={18} />
          </button>
        </div>
      </div>
    </aside>
  );
}
