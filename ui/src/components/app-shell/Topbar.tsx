import { FolderOpen, PanelLeft, PanelRight } from "lucide-react";
import { ConnectionStatus } from "./ConnectionStatus";

type TopbarProps = {
  title: string;
  displayedWorkingDirectory: string;
  socketStatus: string;
  isInspectorOpen: boolean;
  onToggleSidebar(): void;
  onToggleInspector(): void;
};

export function Topbar({
  title,
  displayedWorkingDirectory,
  socketStatus,
  isInspectorOpen,
  onToggleSidebar,
  onToggleInspector,
}: TopbarProps) {
  return (
    <header className="topbar">
      <button
        className="icon-button sidebar-toggle"
        type="button"
        onClick={onToggleSidebar}
        aria-label="打开会话目录"
      >
        <PanelLeft size={18} />
      </button>
      <div className="topbar-title">
        <h1>{title}</h1>
        {displayedWorkingDirectory && (
          <span className="topbar-subtitle" title={displayedWorkingDirectory}>
            <FolderOpen size={12} />
            {displayedWorkingDirectory}
          </span>
        )}
      </div>
      <div className="topbar-actions">
        <ConnectionStatus status={socketStatus} />
        <button
          className={`icon-button ${isInspectorOpen ? "active" : ""}`}
          type="button"
          onClick={onToggleInspector}
          aria-label="切换工作区概览"
          aria-pressed={isInspectorOpen}
          title="工作区概览"
        >
          <PanelRight size={18} />
        </button>
      </div>
    </header>
  );
}
