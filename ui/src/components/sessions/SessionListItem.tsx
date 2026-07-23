import { Check, FolderGit2, FolderOpen, Pencil, Trash2, X } from "lucide-react";
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
  const statusLabel = formatSessionRunStatus(runStatus, isRunning, isActive);
  const showStatus = statusLabel !== "Saved" && statusLabel !== "Active";

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
      <span className="session-item-icon">
        <FolderGit2 size={18} />
      </span>
      <span className="session-item-text">
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
          {session.message_count} messages · {session.backend}
        </small>
        <small className="session-directory" title={session.working_directory}>
          <FolderOpen size={12} />
          {formatDirectoryName(session.working_directory)}
        </small>
      </span>
      <span className="session-item-trailing">
        {showStatus ? (
          <em className={`status-chip ${sessionStatusTone(runStatus, isRunning)}`}>
            {isRunning ? <span className="status-dot" /> : null}
            {statusLabel}
          </em>
        ) : null}
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
                <Check size={14} />
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
                <X size={14} />
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
                <Pencil size={14} />
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
                <Trash2 size={14} />
              </span>
            </>
          )}
        </span>
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

function sessionStatusTone(status: PersistedRunStatus | undefined, isRunning: boolean): string {
  if (status === "waiting_approval") {
    return "tone-warning";
  }
  if (isRunning) {
    return "tone-primary";
  }
  if (status === "failed") {
    return "tone-error";
  }
  if (status === "completed") {
    return "tone-success";
  }
  return "tone-neutral";
}
