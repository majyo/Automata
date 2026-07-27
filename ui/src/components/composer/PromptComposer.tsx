import { Send, Square } from "lucide-react";
import { PermissionPresetToggle } from "./PermissionPresetToggle";
import { SendModeToggle } from "./SendModeToggle";
import { SkillPicker } from "./SkillPicker";
import type { SendMode } from "../../types/chat";
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
  onToggleSkill,
  onToggleSkillEnabled,
  onRefreshSkills,
}: PromptComposerProps) {
  return (
    <div className={`composer ${draft ? "draft" : ""}`}>
      <input
        autoFocus={autoFocus}
        value={prompt}
        onChange={(event) => onPromptChange(event.currentTarget.value)}
        placeholder="Ask the local coding agent..."
      />
      <div className="composer-toolbar">
        <div className="composer-actions">
          <SendModeToggle sendMode={sendMode} disabled={isStreaming} onChange={onSendModeChange} />
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
        <button
          className={`composer-submit ${isStreaming ? "stop" : ""}`}
          type={isStreaming ? "button" : "submit"}
          aria-label={isStreaming ? "Stop run" : "Send prompt"}
          title={isStreaming ? "Stop run" : "Send prompt"}
          disabled={isStreaming ? false : !canSend}
          onClick={isStreaming ? onCancel : undefined}
        >
          {isStreaming ? <Square size={12} fill="currentColor" /> : <Send size={15} />}
        </button>
      </div>
    </div>
  );
}
