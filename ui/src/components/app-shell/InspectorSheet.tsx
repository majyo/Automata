import { ArrowUpRight, FolderOpen, FolderTree, Info, Play, X } from "lucide-react";
import { useRef, useState } from "react";
import { WorkspaceFiles } from "../../features/files/components/WorkspaceFiles";
import { WorkspaceFileTabs } from "../../features/files/components/WorkspaceFileTabs";
import { WorkspaceFileReader } from "../../features/files/components/WorkspaceFileReader";
import type { WorkspaceFilesActions, WorkspaceFilesView } from "./viewModel";
import type { PersistedRunStatus } from "../../types/chat";
import type { PermissionPreset, SessionSummary } from "../../types/session";
import { formatDirectoryName } from "../../utils/format";
import { ConnectionStatus } from "./ConnectionStatus";

type InspectorSheetProps = {
  bridgeStatus: string;
  socketStatus: string;
  activeSession: SessionSummary | null;
  workingDirectory: string;
  filesView: WorkspaceFilesView;
  filesActions: WorkspaceFilesActions;
  messageCount: number;
  permissionPreset: PermissionPreset;
  runStatus?: PersistedRunStatus;
  open: boolean;
  modal: boolean;
  compact: boolean;
  onRunBridgeCheck(): void;
  onClose(): void;
};

const runLabels: Record<PersistedRunStatus, string> = {
  queued: "排队中", running: "执行中", waiting_approval: "等待批准",
  cancelling: "正在停止", completed: "已完成", failed: "执行失败",
  cancelled: "已取消", interrupted: "已中断",
};

export function InspectorSheet({
  bridgeStatus, socketStatus, activeSession, workingDirectory,
  filesView, filesActions, messageCount, permissionPreset, runStatus,
  open, modal, compact, onRunBridgeCheck, onClose,
}: InspectorSheetProps) {
  const [isDirectoryOpen, setIsDirectoryOpen] = useState(true);
  const [isOverviewOpen, setIsOverviewOpen] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  function closeDirectory() {
    panelRef.current?.querySelector<HTMLButtonElement>('[aria-controls="workspace-directory-panel"]')?.focus();
    setIsDirectoryOpen(false);
  }
  const panelActions: WorkspaceFilesActions = {
    ...filesActions,
    openFile: (path) => {
      filesActions.openFile(path);
      setIsOverviewOpen(false);
      if (compact) closeDirectory();
    },
    selectFile: (path) => {
      filesActions.selectFile(path);
      setIsOverviewOpen(false);
      if (!path) setIsDirectoryOpen(true);
      else if (compact) setIsDirectoryOpen(false);
    },
  };

  return (
    <aside className={`inspector-sheet inspector-file-sheet ${open ? "open" : ""}`}
      ref={panelRef}
      aria-label="文件工作面板" aria-hidden={!open} inert={!open}
      role={modal ? "dialog" : undefined} aria-modal={modal || undefined}>
      <div className="inspector-inner">
        <div className="inspector-main">
          <header className="inspector-header">
            <WorkspaceFileTabs view={filesView} actions={panelActions} />
            <div className="inspector-panel-actions">
              <button className={`icon-button small ${isOverviewOpen ? "active" : ""}`} type="button"
                aria-label="工作区概览" title="工作区概览" aria-pressed={isOverviewOpen}
                onClick={() => setIsOverviewOpen((value) => !value)}><Info size={15} /></button>
              <button className={`icon-button small ${isDirectoryOpen ? "active" : ""}`} type="button"
                aria-label={isDirectoryOpen ? "收起文件目录" : "展开文件目录"}
                title={isDirectoryOpen ? "收起文件目录" : "展开文件目录"}
                aria-expanded={isDirectoryOpen} aria-controls="workspace-directory-panel"
                onClick={() => setIsDirectoryOpen((value) => !value)}><FolderTree size={16} /></button>
              <button className="icon-button small" type="button" aria-label="关闭文件面板" title="收起整个文件面板"
                onClick={onClose}><X size={17} /></button>
            </div>
          </header>
          <div className="inspector-main-content">
            <div className={`inspector-file-panel ${isOverviewOpen ? "overview-open" : ""}`}
              role="tabpanel" id="workspace-file-panel"
              aria-labelledby={filesView.selectedPath ? `workspace-file-tab-${filesView.openFiles.indexOf(filesView.selectedPath)}` : "workspace-open-file-tab"}
              aria-hidden={isOverviewOpen} inert={isOverviewOpen}>
              <WorkspaceFileReader key={JSON.stringify([activeSession?.id, workingDirectory])}
                workspace={workingDirectory} view={filesView} actions={panelActions} active={open && !isOverviewOpen}
                onRevealDirectory={(path) => {
                  filesActions.revealDirectory(path);
                  setIsDirectoryOpen(true);
                }} />
            </div>
            {isOverviewOpen && (
              <div className="inspector-body" role="region" aria-label="工作区概览">
                <div className="inspector-workspace">
                  <span className="eyebrow">当前项目</span>
                  <h3>
                    {workingDirectory
                      ? formatDirectoryName(workingDirectory)
                      : "选择工作目录"}
                  </h3>
                </div>
                <div className="inspector-section-title">
                  <span>会话信息</span>
                  <ArrowUpRight size={14} />
                </div>
                <dl className="session-metadata">
                  <div>
                    <dt>消息记录</dt>
                    <dd>
                      {String(messageCount).padStart(2, "0")} <small>条</small>
                    </dd>
                  </div>
                  <div>
                    <dt>运行状态</dt>
                    <dd>
                      {runStatus
                        ? runLabels[runStatus]
                        : activeSession
                          ? "待命"
                          : "未开始"}
                    </dd>
                  </div>
                  <div>
                    <dt>工具权限</dt>
                    <dd>
                      {permissionPreset === "full_access" ? "完全访问" : "沙箱"}
                    </dd>
                  </div>
                  <div>
                    <dt>最近更新</dt>
                    <dd>
                      {activeSession
                        ? formatUpdatedAt(activeSession.updated_at)
                        : "—"}
                    </dd>
                  </div>
                </dl>
                <div className="directory-note">
                  <FolderOpen size={15} />
                  <span>{workingDirectory || "尚未指定目录"}</span>
                </div>
                <details className="connection-details">
                  <summary>连接与诊断</summary>
                  <div className="diagnostic-content">
                    <ConnectionStatus status={socketStatus} />
                    <p>{bridgeStatus}</p>
                    <button
                      className="button button-outlined"
                      type="button"
                      onClick={onRunBridgeCheck}
                    >
                      <Play size={13} />
                      检查桌面连接
                    </button>
                  </div>
                </details>
              </div>
            )}
          </div>
        </div>
        <div className="inspector-directory" id="workspace-directory-panel" role="region" aria-label="文件目录"
          hidden={!isDirectoryOpen} inert={!isDirectoryOpen}>
          <div className="inspector-directory-heading">
            <h2>项目目录</h2>
            <button className="icon-button small" type="button" aria-label="关闭文件目录" title="收起文件目录"
              onClick={closeDirectory}><X size={15} /></button>
          </div>
          <WorkspaceFiles workspace={workingDirectory} view={filesView} actions={panelActions} />
        </div>
      </div>
    </aside>
  );
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
