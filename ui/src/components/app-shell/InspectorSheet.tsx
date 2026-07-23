import { Activity, CheckCircle2, FileCode2, X } from "lucide-react";

const fileChanges = [
  { path: "api/automata_api/routers", state: "+routes" },
  { path: "api/automata_api/agent", state: "+agent" },
  { path: "api/automata_api/repositories", state: "+sqlite" },
];

type InspectorSheetProps = {
  bridgeStatus: string;
  socketStatus: string;
  sessionCount: number;
  open: boolean;
  onClose(): void;
};

export function InspectorSheet({ bridgeStatus, socketStatus, sessionCount, open, onClose }: InspectorSheetProps) {
  return (
    <aside className={`inspector-sheet ${open ? "open" : ""}`} aria-label="Run details" aria-hidden={!open}>
      <div className="inspector-inner">
        <div className="inspector-header">
          <h2>Run details</h2>
          <button className="icon-button small" type="button" onClick={onClose} aria-label="Close run details">
            <X size={17} />
          </button>
        </div>

        <div className="inspector-body">
          <section className="inspector-section summary-band">
            <span className="eyebrow">Tauri command</span>
            <strong>{bridgeStatus}</strong>
          </section>

          <section className="inspector-section">
            <div className="inspector-section-header">
              <h3>Task queue</h3>
              <span className="status-chip tone-primary" title={socketStatus}>
                <Activity size={13} />
                {socketStatus}
              </span>
            </div>
            <div className="task-row complete">
              <CheckCircle2 size={16} />
              <span>SQLite session storage</span>
            </div>
            <div className="task-row">
              <Activity size={16} />
              <span>WebSocket backend: {socketStatus}</span>
            </div>
            <div className="task-row">
              <FileCode2 size={16} />
              <span>{sessionCount} sessions tracked</span>
            </div>
          </section>

          <section className="inspector-section">
            <div className="inspector-section-header">
              <h3>Changed files</h3>
            </div>
            {fileChanges.map((change) => (
              <div className="change-row" key={change.path}>
                <FileCode2 size={15} />
                <span>{change.path}</span>
                <em>{change.state}</em>
              </div>
            ))}
          </section>
        </div>
      </div>
    </aside>
  );
}
