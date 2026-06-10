import { useState } from "react";
import { Activity, CheckCircle2, ChevronDown, ChevronUp, FileCode2 } from "lucide-react";

const fileChanges = [
  { path: "api/automata_api/routers", state: "+routes" },
  { path: "api/automata_api/agent", state: "+agent" },
  { path: "api/automata_api/repositories", state: "+sqlite" },
];

type FloatingInspectorProps = {
  bridgeStatus: string;
  socketStatus: string;
  sessionCount: number;
};

export function FloatingInspector({ bridgeStatus, socketStatus, sessionCount }: FloatingInspectorProps) {
  const [isInspectorExpanded, setIsInspectorExpanded] = useState(true);

  return (
    <aside className={`floating-inspector ${isInspectorExpanded ? "expanded" : "collapsed"}`} aria-label="Run details">
      <button
        className="floating-inspector-toggle"
        type="button"
        aria-expanded={isInspectorExpanded}
        onClick={() => setIsInspectorExpanded((expanded) => !expanded)}
      >
        <span className="floating-status">
          <Activity size={15} />
          <span>{isInspectorExpanded ? "Run details" : socketStatus}</span>
        </span>
        {isInspectorExpanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
      </button>

      {isInspectorExpanded && (
        <div className="floating-inspector-body">
          <section className="summary-band">
            <span className="eyebrow">Tauri command</span>
            <strong>{bridgeStatus}</strong>
          </section>

          <section className="task-list">
            <div className="panel-header compact">
              <h2>Task queue</h2>
              <span className="status-pill" title={socketStatus}>
                <Activity size={14} />
                {socketStatus}
              </span>
            </div>
            <div className="task-row complete">
              <CheckCircle2 size={17} />
              <span>SQLite session storage</span>
            </div>
            <div className="task-row">
              <Activity size={17} />
              <span>WebSocket backend: {socketStatus}</span>
            </div>
            <div className="task-row">
              <FileCode2 size={17} />
              <span>{sessionCount} sessions tracked</span>
            </div>
          </section>

          <section className="changes">
            <div className="panel-header compact">
              <h2>Changed files</h2>
            </div>
            {fileChanges.map((change) => (
              <div className="change-row" key={change.path}>
                <FileCode2 size={16} />
                <span>{change.path}</span>
                <em>{change.state}</em>
              </div>
            ))}
          </section>
        </div>
      )}
    </aside>
  );
}
