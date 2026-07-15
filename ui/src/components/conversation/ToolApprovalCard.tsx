import { ShieldAlert } from "lucide-react";
import type { ApprovalDecision, ToolApprovalRequest } from "../../types/chat";

type ToolApprovalCardProps = {
  approval: ToolApprovalRequest;
  onRespond(approval: ToolApprovalRequest, decision: ApprovalDecision): void;
};

const labels: Record<ApprovalDecision, string> = {
  allow_once: "Allow once",
  allow_for_run: "Allow for this run",
  deny: "Deny",
};

export function ToolApprovalCard({ approval, onRespond }: ToolApprovalCardProps) {
  return (
    <section className={`approval-card risk-${approval.risk}`} aria-label="Tool approval required">
      <div className="approval-heading">
        <ShieldAlert size={18} />
        <div>
          <strong>{approval.summary}</strong>
          <span>{approval.reason}</span>
        </div>
        <em>{approval.risk}</em>
      </div>
      {Object.keys(approval.preview).length > 0 ? (
        <pre>{JSON.stringify(approval.preview, null, 2)}</pre>
      ) : null}
      <div className="approval-actions">
        {approval.options.map((decision) => (
          <button
            className={decision === "deny" ? "deny" : "allow"}
            key={decision}
            type="button"
            onClick={() => onRespond(approval, decision)}
          >
            {labels[decision]}
          </button>
        ))}
      </div>
    </section>
  );
}
