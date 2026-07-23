import { FolderOpen, Moon, PanelRight, Play, Sun } from "lucide-react";

type TopbarProps = {
  title: string;
  displayedWorkingDirectory: string;
  socketStatus: string;
  theme: "light" | "dark";
  isInspectorOpen: boolean;
  onRunBridgeCheck(): void;
  onToggleTheme(): void;
  onToggleInspector(): void;
};

export function Topbar({
  title,
  displayedWorkingDirectory,
  socketStatus,
  theme,
  isInspectorOpen,
  onRunBridgeCheck,
  onToggleTheme,
  onToggleInspector,
}: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>{title}</h1>
        {displayedWorkingDirectory ? (
          <span className="topbar-subtitle" title={displayedWorkingDirectory}>
            <FolderOpen size={13} />
            {displayedWorkingDirectory}
          </span>
        ) : null}
      </div>

      <div className="topbar-actions">
        <span className={`status-chip ${socketStatusTone(socketStatus)}`} title={socketStatus}>
          <span className="status-dot" />
          {socketStatus}
        </span>

        <button className="button button-tonal" type="button" onClick={onRunBridgeCheck}>
          <Play size={16} />
          Run bridge check
        </button>

        <span className="topbar-divider" />

        <button
          className="icon-button"
          type="button"
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
        </button>

        <button
          className={`icon-button ${isInspectorOpen ? "active" : ""}`}
          type="button"
          onClick={onToggleInspector}
          aria-label="Toggle run details panel"
          aria-pressed={isInspectorOpen}
          title="Toggle run details panel"
        >
          <PanelRight size={19} />
        </button>
      </div>
    </header>
  );
}

function socketStatusTone(status: string): string {
  const value = status.toLowerCase();
  if (/(error|fail|closed|offline|disconnect(?!ing))/.test(value)) {
    return "tone-error";
  }
  if (/(connecting|waiting|pending|starting)/.test(value)) {
    return "tone-primary";
  }
  if (/(connected|ready|online|ok)/.test(value)) {
    return "tone-success";
  }
  return "tone-neutral";
}
