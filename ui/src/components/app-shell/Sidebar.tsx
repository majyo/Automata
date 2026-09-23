import { ArrowUpRight, Plus, Search, X } from "lucide-react";
import { useState } from "react";
import { SessionList } from "./SessionList";
import type { PersistedRunStatus } from "../../types/chat";
import type { SessionSummary } from "../../types/session";

type SidebarProps = {
  open: boolean;
  modal: boolean;
  inactive: boolean;
  onClose(): void;
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
  open,
  modal,
  inactive,
  onClose,
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
  const [query, setQuery] = useState("");
  const visibleSessions = sessions.filter((session) =>
    `${session.title} ${session.working_directory}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <aside
      className={`sidebar ${open ? "mobile-open" : ""}`}
      aria-label="会话目录"
      role={modal ? "dialog" : undefined}
      aria-modal={modal || undefined}
      inert={inactive}
    >
      <div className="sidebar-heading">
        <span className="eyebrow">会话目录</span>
        <button
          className="icon-button sidebar-close"
          type="button"
          onClick={onClose}
          aria-label="关闭会话目录"
        >
          <X size={18} />
        </button>
      </div>

      <button
        className="new-session-button"
        type="button"
        onClick={() => {
          setQuery("");
          onCreateSession();
        }}
      >
        <Plus size={17} />
        <span>新建会话</span>
        <ArrowUpRight size={16} />
      </button>

      <label className="session-search">
        <Search size={15} />
        <input
          id="session-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索会话"
          aria-label="搜索会话"
        />
        {query && (
          <button
            type="button"
            className="icon-button small"
            aria-label="清除搜索"
            onClick={() => setQuery("")}
          >
            <X size={13} />
          </button>
        )}
      </label>

      <SessionList
        sessions={visibleSessions}
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
      {visibleSessions.length === 0 && (
        <p className="session-empty">
          {query
            ? "没有匹配的会话，试试其他关键词。"
            : "还没有会话。新建一个，开始处理项目。"}
        </p>
      )}
    </aside>
  );
}
