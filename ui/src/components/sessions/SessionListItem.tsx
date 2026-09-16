import {
  ArrowUpRight,
  Check,
  FolderOpen,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
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

const statusLabels: Partial<Record<PersistedRunStatus, string>> = {
  waiting_approval: "等待批准",
  cancelling: "正在停止",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
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
  const status =
    runStatus === "waiting_approval"
      ? "等待批准"
      : isRunning
        ? runStatus === "cancelling"
          ? "正在停止"
          : "执行中"
        : runStatus
          ? statusLabels[runStatus]
          : undefined;
  function handleTitleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter") {
      event.preventDefault();
      onCommitRename(session.id);
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      onCancelRename();
    }
  }
  return (
    <div
      className={`session-item ${isActive ? "active" : ""} ${isRunning ? "running" : ""}`}
    >
      {isEditing ? (
        <div className="session-edit">
          <input
            aria-label="会话名称"
            autoFocus
            className="session-title-input"
            value={editingTitle}
            onChange={(event) =>
              onEditingTitleChange(event.currentTarget.value)
            }
            onKeyDown={handleTitleKeyDown}
          />
          <button
            className="mini-action"
            type="button"
            aria-label="保存会话名称"
            onClick={() => onCommitRename(session.id)}
          >
            <Check size={14} />
          </button>
          <button
            className="mini-action"
            type="button"
            aria-label="取消重命名"
            onClick={onCancelRename}
          >
            <X size={14} />
          </button>
        </div>
      ) : (
        <button
          className="session-select"
          type="button"
          onClick={() => onSelect(session.id)}
          aria-current={isActive ? "page" : undefined}
        >
          <span className="session-directory">
            <FolderOpen size={12} />
            {formatDirectoryName(session.working_directory)}
          </span>
          <strong>{session.title}</strong>
          <span className="session-item-bottom">
            <span>{session.message_count} 条消息</span>
            {status ? (
              <em
                className={`session-run-status ${runStatus === "failed" ? "tone-error" : runStatus === "waiting_approval" ? "tone-warning" : ""}`}
              >
                {isRunning && <span className="status-dot" />}
                {status}
              </em>
            ) : (
              <ArrowUpRight size={13} />
            )}
          </span>
        </button>
      )}
      {!isEditing && (
        <div className="session-actions">
          <button
            className="mini-action"
            type="button"
            aria-label={`重命名 ${session.title}`}
            title="重命名"
            onClick={() => onStartRename(session)}
          >
            <Pencil size={13} />
          </button>
          <button
            className="mini-action danger"
            type="button"
            aria-label={`删除 ${session.title}`}
            title="删除会话"
            onClick={() => onDelete(session.id)}
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}
    </div>
  );
}
