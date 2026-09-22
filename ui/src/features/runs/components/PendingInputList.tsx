import { CornerUpRight, X } from "lucide-react";
import type { PendingInput } from "../../../types/chat";

type PendingInputListProps = {
  inputs: PendingInput[];
  canSteer: boolean;
  onSteer(input: PendingInput): void;
  onCancel(input: PendingInput): void;
};

/**
 * Messages the user submitted while the session was busy.
 *
 * They are not part of the transcript yet: a queued message becomes its own
 * Run once the current one finishes, and 插话 delivers it into the Run that
 * is active now. Both buttons are disabled until the backend has acknowledged
 * the input, because only then does it have an id to steer or withdraw.
 */
export function PendingInputList({
  inputs,
  canSteer,
  onSteer,
  onCancel,
}: PendingInputListProps) {
  if (inputs.length === 0) {
    return null;
  }

  return (
    <div className="pending-inputs" aria-label="排队中的消息">
      <div className="pending-inputs-heading">
        <span>排队中</span>
        <span>当前任务结束后依次执行</span>
      </div>
      <ul>
        {inputs.map((input) => {
          const settled = input.status === "pending" && Boolean(input.inputId);
          const steering = settled && Boolean(input.steerRequestId);
          return (
            <li
              key={input.requestId}
              className={`pending-input ${input.status}`}
              aria-busy={input.status !== "pending"}
            >
              <span className="pending-input-prompt" title={input.prompt}>
                {input.prompt}
              </span>
              <span className="pending-input-actions">
                <button
                  type="button"
                  disabled={!settled || !canSteer || steering}
                  onClick={() => onSteer(input)}
                  title="插话：立即交给正在进行的任务"
                  aria-label="插话"
                >
                  <CornerUpRight size={12} />
                  {steering ? "插话中" : "插话"}
                </button>
                <button
                  type="button"
                  disabled={!settled || input.status === "cancelling"}
                  onClick={() => onCancel(input)}
                  title="删除这条排队消息"
                  aria-label="删除"
                >
                  <X size={12} />
                </button>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
