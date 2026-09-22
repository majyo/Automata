import type { FormEvent, RefObject } from "react";
import { ArrowUpRight } from "lucide-react";
import { MessageList } from "./MessageList";
import { PromptComposer } from "../../../components/composer/PromptComposer";
import { WorkspacePicker } from "./WorkspacePicker";
import { ToolApprovalCard } from "./ToolApprovalCard";
import type {
  ApprovalDecision,
  ChatMessage,
  PendingInput,
  SendMode,
  ToolApprovalRequest,
} from "../../../types/chat";
import type { PermissionPreset } from "../../../types/session";
import type { SkillRecord, SkillRuntimeNotice } from "../../../types/skills";

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
  sandboxSetupStatus: string;
  isStreaming: boolean;
  canSend: boolean;
  pendingInputs: PendingInput[];
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
  onSandboxSetup(): void;
  onSteerInput(input: PendingInput): void;
  onCancelInput(input: PendingInput): void;
  onApprovePlan(message: ChatMessage): void;
  onRespondToApproval(
    approval: ToolApprovalRequest,
    decision: ApprovalDecision,
  ): void;
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
  sandboxSetupStatus,
  isStreaming,
  canSend,
  pendingInputs,
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
  onSandboxSetup,
  onSteerInput,
  onCancelInput,
  onApprovePlan,
  onRespondToApproval,
  onCancelRun,
  onToggleSkill,
  onToggleSkillEnabled,
  onRefreshSkills,
}: ConversationPanelProps) {
  return (
    <section className="conversation-panel" aria-label="编程助手会话">
      {isNewSessionDraft ? (
        <div className="new-session-stage">
          <form className="new-session-dialog" onSubmit={onSubmit}>
            <div className="new-session-content">
              <div className="welcome-heading">
                <span className="eyebrow">开始一个新任务</span>
                <h2>今天，处理什么项目？</h2>
                <p>选择工作目录，描述你要完成的事。</p>
              </div>
              <div className="starter-prompts" aria-label="任务建议">
                {[
                  {
                    title: "了解项目",
                    description: "梳理结构与主要调用关系",
                    prompt:
                      "请先查看当前项目的目录与入口，梳理主要模块及其调用关系。",
                  },
                  {
                    title: "实现功能",
                    description: "从需求拆解到代码修改",
                    prompt:
                      "我想在当前项目中实现一个功能，请先了解现有代码，再和我确认具体需求。",
                  },
                  {
                    title: "排查问题",
                    description: "定位原因并给出修复方案",
                    prompt:
                      "请帮助我排查当前项目的问题，先查看项目结构和现有测试，再根据我提供的现象定位原因。",
                  },
                ].map((item, index) => (
                  <button
                    type="button"
                    key={item.title}
                    onClick={() => {
                      onPromptChange(item.prompt);
                      document.getElementById("prompt-input")?.focus();
                    }}
                  >
                    <span className="starter-number">0{index + 1}</span>
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.description}</small>
                    </span>
                    <ArrowUpRight size={16} />
                  </button>
                ))}
              </div>
              <WorkspacePicker
                displayedWorkingDirectory={displayedWorkingDirectory}
                defaultWorkingDirectory={defaultWorkingDirectory}
                isNewSessionDraft={isNewSessionDraft}
                isStreaming={isStreaming}
                onChooseDirectory={onChooseDirectory}
                onWorkingDirectoryChange={onWorkingDirectoryChange}
              />
            </div>
            <PromptComposer
              draft
              prompt={prompt}
              sendMode={sendMode}
              permissionPreset={permissionPreset}
              permissionUpdating={permissionUpdating}
              sandboxSetupStatus={sandboxSetupStatus}
              isStreaming={isStreaming}
              canSend={canSend}
              pendingInputs={pendingInputs}
              skills={skills}
              selectedSkillIds={selectedSkillIds}
              skillErrors={skillErrors}
              skillNotices={skillNotices}
              skillsLoading={skillsLoading}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onPermissionPresetChange={onPermissionPresetChange}
              onSandboxSetup={onSandboxSetup}
              onCancel={onCancelRun}
              onSteerInput={onSteerInput}
              onCancelInput={onCancelInput}
              onToggleSkill={onToggleSkill}
              onToggleSkillEnabled={onToggleSkillEnabled}
              onRefreshSkills={onRefreshSkills}
            />
            <div className="composer-footnote">
              <span>先选择执行方式与工具权限，再发送消息。</span>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
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
            <div className="composer-heading">
              <span>继续会话</span>
              <span>{isStreaming ? "正在处理任务" : "描述下一步要做的事"}</span>
            </div>
            {approvals[0] ? (
              <ToolApprovalCard
                approval={approvals[0]}
                onRespond={onRespondToApproval}
              />
            ) : null}
            <PromptComposer
              prompt={prompt}
              sendMode={sendMode}
              permissionPreset={permissionPreset}
              permissionUpdating={permissionUpdating}
              sandboxSetupStatus={sandboxSetupStatus}
              isStreaming={isStreaming}
              canSend={canSend}
              pendingInputs={pendingInputs}
              skills={skills}
              selectedSkillIds={selectedSkillIds}
              skillErrors={skillErrors}
              skillNotices={skillNotices}
              skillsLoading={skillsLoading}
              onPromptChange={onPromptChange}
              onSendModeChange={onSendModeChange}
              onPermissionPresetChange={onPermissionPresetChange}
              onSandboxSetup={onSandboxSetup}
              onCancel={onCancelRun}
              onSteerInput={onSteerInput}
              onCancelInput={onCancelInput}
              onToggleSkill={onToggleSkill}
              onToggleSkillEnabled={onToggleSkillEnabled}
              onRefreshSkills={onRefreshSkills}
            />
            <div className="composer-footnote">
              <span>
                {sendMode === "plan"
                  ? "先生成计划，确认后执行。"
                  : "按当前权限设置执行任务。"}
              </span>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
          </form>
        </>
      )}
    </section>
  );
}
