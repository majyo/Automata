import { Check, FolderGit2, FolderOpen, GitBranch, Pencil, ServerCog, Trash2, X } from "lucide-react";
import type { KeyboardEvent } from "react";
import type { PersistedRunStatus } from "../../types/chat";
import type { SessionSummary } from "../../types/session";
import { formatDirectoryName } from "../../utils/format";

type SessionListItemProps = {
  session: SessionSummary;
  isActive: boolean;
  isRunning: boolean;
  runStatus?: PersistedRunStatus;
  editingSessionId: string | null;
  editingTitle: string;
  onSelect(sessionId: string): void;
  onStartRename(session: SessionSummary): void;
  onEditingTitleChange(title: string): void;
  onCommitRename(sessionId: string): void;
  onCancelRename(): void;
  onDelete(sessionId: string): void;
};

export function SessionListItem({
  session,
  isActive,
  isRunning,
  runStatus,
  editingSessionId,
  editingTitle,
  onSelect,
  onStartRename,
  onEditingTitleChange,
  onCommitRename,
  onCancelRename,
  onDelete,
}: SessionListItemProps) {
  const isEditing = editingSessionId === session.id;

  function handleTitleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      onCommitRename(session.id);
    }
    if (event.key === "Escape") {
      onCancelRename();
    }
  }

  return (
    <button
      className={`session-item ${isActive ? "active" : ""} ${isRunning ? "running" : ""}`}
      onClick={() => onSelect(session.id)}
    >
      <FolderGit2 size={17} />
      <span>
        {isEditing ? (
          <input
            autoFocus
            className="session-title-input"
            value={editingTitle}
            onChange={(event) => onEditingTitleChange(event.currentTarget.value)}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={handleTitleKeyDown}
          />
        ) : (
          <strong>{session.title}</strong>
        )}
        <small>
          <GitBranch size={13} />
          {session.message_count} messages
        </small>
        <small>
          <ServerCog size={13} />
          {session.backend}
        </small>
        <small className="session-directory" title={session.working_directory}>
          <FolderOpen size={13} />
          {formatDirectoryName(session.working_directory)}
        </small>
      </span>
      <em>{formatSessionRunStatus(runStatus, isRunning, isActive)}</em>
      <span className="session-actions">
        {isEditing ? (
          <>
            <span
              className="mini-action"
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation();
                onCommitRename(session.id);
              }}
            >
              <Check size={13} />
            </span>
            <span
              className="mini-action"
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation();
                onCancelRename();
              }}
            >
              <X size={13} />
            </span>
          </>
        ) : (
          <>
            <span
              className="mini-action"
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation();
                onStartRename(session);
              }}
            >
              <Pencil size={13} />
            </span>
            <span
              className="mini-action danger"
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation();
                onDelete(session.id);
              }}
            >
              <Trash2 size={13} />
            </span>
          </>
        )}
      </span>
    </button>
  );
}

function formatSessionRunStatus(
  status: PersistedRunStatus | undefined,
  isRunning: boolean,
  isActive: boolean,
): string {
  if (status === "waiting_approval") {
    return "Approval";
  }
  if (isRunning) {
    return status === "cancelling" ? "Stopping" : "Running";
  }
  if (isActive) {
    return "Active";
  }
  if (status === "completed") {
    return "Completed";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "cancelled") {
    return "Cancelled";
  }
  if (status === "interrupted") {
    return "Interrupted";
  }
  return "Saved";
}
