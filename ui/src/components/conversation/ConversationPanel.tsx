import type { FormEvent, RefObject } from "react";
import { Sparkles } from "lucide-react";
import { MessageList } from "./MessageList";
import { PromptComposer } from "../composer/PromptComposer";
import { WorkspacePicker } from "../sessions/WorkspacePicker";
import { ToolApprovalCard } from "./ToolApprovalCard";
import type { ApprovalDecision, ChatMessage, SendMode, ToolApprovalRequest } from "../../types/chat";
import type { PermissionPreset } from "../../types/session";
import type { SkillRecord, SkillRuntimeNotice } from "../../types/skills";

type ConversationPanelProps = {
  isNewSessionDraft: boolean;
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  displayedWorkingDirectory: string;
  defaultWorkingDirectory: string;
  prompt: string;
  sendMode: SendMode;
  permissionPreset: PermissionPreset;
  permissionUpdating: boolean;
  isStreaming: boolean;
  canSend: boolean;
  approvals: ToolApprovalRequest[];
  skills: SkillRecord[];
  selectedSkillIds: Set<string>;
  skillErrors: string[];
  skillNotices: SkillRuntimeNotice[];
  skillsLoading: boolean;
  onChooseDirectory(): void;
  onWorkingDirectoryChange(workingDirectory: string): void;
  onSubmit(event: FormEvent<HTMLFormElement>): void;
  onPromptChange(prompt: string): void;
  onSendModeChange(sendMode: SendMode): void;
  onPermissionPresetChange(permissionPreset: PermissionPreset): void;
  onApprovePlan(message: ChatMessage): void;
  onRespondToApproval(approval: ToolApprovalRequest, decision: ApprovalDecision): void;
  onCancelRun(): void;
  onToggleSkill(skillId: string): void;
  onToggleSkillEnabled(skill: SkillRecord): Promise<void>;
  onRefreshSkills(): void;
};

export function ConversationPanel({
  isNewSessionDraft,
  messages,
  messagesRef,
  displayedWorkingDirectory,
  defaultWorkingDirectory,
  prompt,
  sendMode,
  permissionPreset,
  permissionUpdating,
  isStreaming,
  canSend,
  approvals,
  skills,
  selectedSkillIds,
  skillErrors,
  skillNotices,
  skillsLoading,
  onChooseDirectory,
  onWorkingDirectoryChange,
  onSubmit,
  onPromptChange,
  onSendModeChange,
  onPermissionPresetChange,
  onApprovePlan,
  onRespondToApproval,
  onCancelRun,
  onToggleSkill,
  onToggleSkillEnabled,
  onRefreshSkills,
}: ConversationPanelProps) {
  return (
    <section className="conversation-panel" aria-label="Agent conversation">
      {isNewSessionDraft ? (
        <div className="new-session-stage">
          <form className="new-session-dialog" onSubmit={onSubmit}>
            <div className="new-session-icon">
              <Sparkles size={18} />
            </div>
            <h2>输入一条消息来开始新的会话</h2>
            <WorkspacePicker
              displayedWorkingDirectory={displayedWorkingDirectory}
              defaultWorkingDirectory={defaultWorkingDirectory}
              isNewSessionDraft={isNewSessionDraft}
              isStreaming={isStreaming}
              onChooseDirectory={onChooseDirectory}
              onWorkingDirectoryChange={onWorkingDirectoryChange}
            />
            <PromptComposer
              autoFocus
              draft
              prompt={prompt}
              sendMode={sendMode}
              permissionPreset={permissionPreset}
              permissionUpdating={permissionUpdating}
              isStreaming={isStreaming}
              canSend={canSend}
              skills={skills}
              selectedSkillIds={selectedSkillIds}
              skillErrors={skillErrors}
              skillNotices={skillNotices}
              skillsLoading={skillsLoading}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onPermissionPresetChange={onPermissionPresetChange}
              onCancel={onCancelRun}
              onToggleSkill={onToggleSkill}
              onToggleSkillEnabled={onToggleSkillEnabled}
              onRefreshSkills={onRefreshSkills}
            />
          </form>
        </div>
      ) : (
        <>
          <MessageList
            messages={messages}
            messagesRef={messagesRef}
            isStreaming={isStreaming}
            onApprovePlan={onApprovePlan}
          />

          <form className="composer-form" onSubmit={onSubmit}>
            {approvals[0] ? (
              <ToolApprovalCard approval={approvals[0]} onRespond={onRespondToApproval} />
            ) : null}
            <PromptComposer
              prompt={prompt}
              sendMode={sendMode}
              permissionPreset={permissionPreset}
              permissionUpdating={permissionUpdating}
              isStreaming={isStreaming}
              canSend={canSend}
              skills={skills}
              selectedSkillIds={selectedSkillIds}
              skillErrors={skillErrors}
              skillNotices={skillNotices}
              skillsLoading={skillsLoading}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onPermissionPresetChange={onPermissionPresetChange}
              onCancel={onCancelRun}
              onToggleSkill={onToggleSkill}
              onToggleSkillEnabled={onToggleSkillEnabled}
              onRefreshSkills={onRefreshSkills}
            />
          </form>
        </>
      )}
    </section>
  );
}
