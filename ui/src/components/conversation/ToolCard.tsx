import { useState } from "react";
import { Activity, CheckCircle2, ChevronDown, ChevronUp, X } from "lucide-react";
import type { ToolRunMetadata, ToolRunStatus } from "../../types/chat";
import { formatToolRunStatus } from "../../utils/format";

type ToolCardProps = {
  metadata: ToolRunMetadata | null;
};

export function ToolCard({ metadata }: ToolCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const tool = metadata?.tool?.trim() || "unknown_tool";
  const argumentsText = metadata?.arguments ?? "{}";
  const result = metadata?.result ?? null;
  const status: ToolRunStatus = result ? (result.success === false ? "failed" : "completed") : "running";
  const resultText = result?.content ?? "";

  return (
    <div className={`tool-card ${status}`}>
      <button
        className="tool-card-header"
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <span className="tool-card-title">
          {status === "running" ? (
            <Activity size={15} />
          ) : status === "failed" ? (
            <X size={15} />
          ) : (
            <CheckCircle2 size={15} />
          )}
          <strong>{tool}</strong>
        </span>
        <span className={`tool-card-status ${status}`}>{formatToolRunStatus(status)}</span>
        {isExpanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
      </button>

      {isExpanded && (
        <div className="tool-card-body">
          <section className="tool-card-section">
            <span>Arguments</span>
            <pre className="tool-card-content">{argumentsText || "{}"}</pre>
          </section>
          <section className="tool-card-section">
            <span>Result</span>
            <pre className="tool-card-content">{result ? resultText || "(empty)" : "Running..."}</pre>
          </section>
        </div>
      )}
    </div>
  );
}
