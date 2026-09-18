import { SessionListItem } from "./SessionListItem";
import type { PersistedRunStatus } from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type SessionListProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  editingSessionId: string | null;
  editingTitle: string;
  activeRunIdBySession: Record<string, string>;
  runStatusBySession: Record<string, PersistedRunStatus>;
  onSelect(sessionId: string): void;
  onStartRename(session: SessionSummary): void;
  onEditingTitleChange(title: string): void;
  onCommitRename(sessionId: string): void;
  onCancelRename(): void;
  onDelete(sessionId: string): void;
};

export function SessionList(props: SessionListProps) {
  return (
    <nav className="session-list" aria-label="Agent sessions">
      {props.sessions.map((session) => (
        <SessionListItem
          key={session.id}
          session={session}
          isActive={session.id === props.activeSessionId}
          isRunning={Boolean(props.activeRunIdBySession[session.id])}
          runStatus={props.runStatusBySession[session.id]}
          editingSessionId={props.editingSessionId}
          editingTitle={props.editingTitle}
          onSelect={props.onSelect}
          onStartRename={props.onStartRename}
          onEditingTitleChange={props.onEditingTitleChange}
          onCommitRename={props.onCommitRename}
          onCancelRename={props.onCancelRename}
          onDelete={props.onDelete}
        />
      ))}
    </nav>
  );
}
