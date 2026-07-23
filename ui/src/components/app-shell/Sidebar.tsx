import { Plus, Sparkles } from "lucide-react";
import { SessionList } from "../sessions/SessionList";
import type { PersistedRunStatus } from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type SidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  editingSessionId: string | null;
  editingTitle: string;
  activeRunIdBySession: Record<string, string>;
  runStatusBySession: Record<string, PersistedRunStatus>;
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onStartRename(session: SessionSummary): void;
  onEditingTitleChange(title: string): void;
  onCommitRename(sessionId: string): void;
  onCancelRename(): void;
  onDeleteSession(sessionId: string): void;
};

export function Sidebar({
  sessions,
  activeSessionId,
  editingSessionId,
  editingTitle,
  activeRunIdBySession,
  runStatusBySession,
  onCreateSession,
  onSelectSession,
  onStartRename,
  onEditingTitleChange,
  onCommitRename,
  onCancelRename,
  onDeleteSession,
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

      <button className="new-session-button" type="button" onClick={onCreateSession}>
        <Plus size={19} />
        New session
      </button>

      <div className="sidebar-toolbar">
        <span>Sessions</span>
        <span className="count">{sessions.length}</span>
      </div>

      <SessionList
        sessions={sessions}
        activeSessionId={activeSessionId}
        editingSessionId={editingSessionId}
        editingTitle={editingTitle}
        activeRunIdBySession={activeRunIdBySession}
        runStatusBySession={runStatusBySession}
        onSelect={onSelectSession}
        onStartRename={onStartRename}
        onEditingTitleChange={onEditingTitleChange}
        onCommitRename={onCommitRename}
        onCancelRename={onCancelRename}
        onDelete={onDeleteSession}
      />
    </aside>
  );
}
