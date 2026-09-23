import { ShieldAlert } from "lucide-react";
import type { ApprovalDecision, ToolApprovalRequest } from "../../../types/chat";

type ToolApprovalCardProps = {
  approval: ToolApprovalRequest;
  onRespond(approval: ToolApprovalRequest, decision: ApprovalDecision): void;
};

const labels: Record<ApprovalDecision, string> = {
  allow_once: "允许一次",
  allow_for_run: "本次运行内允许",
  deny: "拒绝",
};

const riskLabels: Record<ToolApprovalRequest["risk"], string> = {
  read: "读取",
  write: "写入",
  command: "命令",
  destructive: "破坏性",
  external: "外部访问",
};

export function ToolApprovalCard({ approval, onRespond }: ToolApprovalCardProps) {
  return (
    <section className={`approval-card risk-${approval.risk}`} aria-label="需要批准工具调用">
      <div className="approval-heading">
        <ShieldAlert size={18} />
        <div>
          <strong>{approval.summary}</strong>
          <span>{approval.reason}</span>
        </div>
        <em>{riskLabels[approval.risk] ?? approval.risk}</em>
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
