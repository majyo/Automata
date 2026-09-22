import { CornerUpRight, RotateCcw, X } from "lucide-react";
import type { PendingInput } from "../../../types/chat";

type PendingInputListProps = {
  inputs: PendingInput[];
  canSteer: boolean;
  /** Why the waiting messages are not running yet, when that is known. */
  pausedReason?: "cancelled" | "interrupted";
  onSteer(input: PendingInput): void;
  onCancel(input: PendingInput): void;
  onRequeue(input: PendingInput): void;
  onDismiss(input: PendingInput): void;
};

/**
 * Messages the user submitted while the session was busy.
 *
 * They are not part of the transcript yet: a queued message becomes its own
 * Run once the current one finishes, and 插话 delivers it into the Run that is
 * active now. Both buttons wait for the backend to acknowledge the input,
 * because only then does it have an id to steer or withdraw.
 *
 * A withdrawn message keeps its text and offers 重新排队 instead: the backend
 * drops the follow-ups of a Run that failed, and retyping them would be worse
 * than queueing them again.
 */
export function PendingInputList({
  inputs,
  canSteer,
  pausedReason,
  onSteer,
  onCancel,
  onRequeue,
  onDismiss,
}: PendingInputListProps) {
  if (inputs.length === 0) {
    return null;
  }

  return (
    <div className="pending-inputs" aria-label="排队中的消息">
      <div className="pending-inputs-heading">
        <span>排队中</span>
        <span>
          {pausedReason === "cancelled"
            ? "上次任务已取消，排队已暂停"
            : pausedReason === "interrupted"
              ? "上次任务被中断，排队已暂停"
              : "当前任务结束后依次执行"}
        </span>
      </div>
      <ul>
        {inputs.map((input) => {
          const settled = input.status === "pending" && Boolean(input.inputId);
          const withdrawn = input.status === "cancelled";
          const steering = settled && Boolean(input.steerRequestId);
          return (
            <li
              key={input.requestId}
              className={`pending-input ${input.status}`}
              aria-busy={input.status === "sending" || input.status === "cancelling"}
            >
              <span className="pending-input-prompt" title={input.prompt}>
                {input.prompt}
              </span>
              {withdrawn ? (
                <span className="pending-input-note">已取消：前置任务失败</span>
              ) : null}
              <span className="pending-input-actions">
                {withdrawn ? (
                  <>
                    <button
                      type="button"
                      onClick={() => onRequeue(input)}
                      title="重新排队：按现在的状态再执行一次"
                      aria-label="重新排队"
                    >
                      <RotateCcw size={12} />
                      重新排队
                    </button>
                    <button
                      type="button"
                      onClick={() => onDismiss(input)}
                      title="关闭这条已取消的消息"
                      aria-label="关闭"
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <>
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
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
