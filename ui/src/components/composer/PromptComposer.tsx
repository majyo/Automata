import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef } from "react";
import { PermissionPresetToggle } from "./PermissionPresetToggle";
import { SendModeToggle } from "./SendModeToggle";
import { PendingInputList } from "../../features/runs/components/PendingInputList";
import { SkillPicker } from "../../features/skills/components/SkillPicker";
import type { PendingInput, SendMode } from "../../types/chat";
import type { PermissionPreset } from "../../types/session";
import type { SkillRecord, SkillRuntimeNotice } from "../../types/skills";

type PromptComposerProps = {
  prompt: string;
  sendMode: SendMode;
  permissionPreset: PermissionPreset;
  permissionUpdating: boolean;
  sandboxSetupStatus: string;
  isStreaming: boolean;
  canSend: boolean;
  autoFocus?: boolean;
  draft?: boolean;
  pendingInputs: PendingInput[];
  queuePausedReason?: "cancelled" | "interrupted";
  skills: SkillRecord[];
  selectedSkillIds: Set<string>;
  skillErrors: string[];
  skillNotices: SkillRuntimeNotice[];
  skillsLoading: boolean;
  onPromptChange(prompt: string): void;
  onSendModeChange(sendMode: SendMode): void;
  onPermissionPresetChange(permissionPreset: PermissionPreset): void;
  onSandboxSetup(): void;
  onCancel(): void;
  onSteerInput(input: PendingInput): void;
  onCancelInput(input: PendingInput): void;
  onRequeueInput(input: PendingInput): void;
  onDismissInput(input: PendingInput): void;
  onToggleSkill(skillId: string): void;
  onToggleSkillEnabled(skill: SkillRecord): Promise<void>;
  onRefreshSkills(): void;
};

export function PromptComposer({
  prompt,
  sendMode,
  permissionPreset,
  permissionUpdating,
  sandboxSetupStatus,
  isStreaming,
  canSend,
  autoFocus,
  draft,
  pendingInputs,
  queuePausedReason,
  skills,
  selectedSkillIds,
  skillErrors,
  skillNotices,
  skillsLoading,
  onPromptChange,
  onSendModeChange,
  onPermissionPresetChange,
  onSandboxSetup,
  onCancel,
  onSteerInput,
  onCancelInput,
  onRequeueInput,
  onDismissInput,
  onToggleSkill,
  onToggleSkillEnabled,
  onRefreshSkills,
}: PromptComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const input = inputRef.current;
    if (input) {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    }
  }, [prompt]);
  return (
    <div className={`composer ${draft ? "draft" : ""}`}>
      <PendingInputList
        inputs={pendingInputs}
        canSteer={isStreaming}
        pausedReason={queuePausedReason}
        onSteer={onSteerInput}
        onCancel={onCancelInput}
        onRequeue={onRequeueInput}
        onDismiss={onDismissInput}
      />
      <textarea
        ref={inputRef}
        id="prompt-input"
        aria-label="输入任务消息"
        rows={2}
        autoFocus={autoFocus}
        value={prompt}
        onChange={(event) => onPromptChange(event.currentTarget.value)}
        placeholder={
          draft
            ? "描述任务，或从上方建议开始…"
            : isStreaming
              ? "输入消息，将排队到当前任务之后…"
              : "输入消息，继续处理项目…"
        }
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing &&
            event.keyCode !== 229
          ) {
            event.preventDefault();
            if (canSend) event.currentTarget.form?.requestSubmit();
          }
        }}
      />
      <div className="composer-toolbar">
        <div className="composer-actions">
          <SendModeToggle
            sendMode={sendMode}
            disabled={isStreaming}
            onChange={onSendModeChange}
          />
          <PermissionPresetToggle
            permissionPreset={permissionPreset}
            disabled={isStreaming || permissionUpdating}
            onChange={onPermissionPresetChange}
            setupStatus={sandboxSetupStatus}
            onSetupSandbox={onSandboxSetup}
          />
          <SkillPicker
            skills={skills}
            selectedIds={selectedSkillIds}
            errors={skillErrors}
            notices={skillNotices}
            isLoading={skillsLoading}
            disabled={isStreaming}
            onToggleSelected={onToggleSkill}
            onToggleEnabled={onToggleSkillEnabled}
            onRefresh={onRefreshSkills}
          />
        </div>
        {isStreaming ? (
          <div className="composer-submit-group">
            <button
              className="composer-submit queue"
              type="button"
              aria-label="排队发送消息"
              title="排队发送消息"
              disabled={!canSend}
              onClick={(event) => event.currentTarget.form?.requestSubmit()}
            >
              <ArrowUp size={18} />
            </button>
            <button
              className="composer-submit stop"
              type="button"
              aria-label="停止任务"
              title="停止任务"
              onClick={onCancel}
            >
              <Square size={13} fill="currentColor" />
            </button>
          </div>
        ) : (
          <button
            className="composer-submit"
            type="submit"
            aria-label="发送消息"
            title="发送消息"
            disabled={!canSend}
          >
            <ArrowUp size={20} />
          </button>
        )}
      </div>
    </div>
  );
}
