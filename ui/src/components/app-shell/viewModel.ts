import type { FormEvent, RefObject } from "react";
import type {
  ApprovalDecision,
  ChatMessage,
  PendingInput,
  PersistedRunStatus,
  SendMode,
  ToolApprovalRequest,
} from "../../types/chat";
import type { PermissionPreset, SessionSummary } from "../../types/session";
import type { SkillRecord, SkillRuntimeNotice } from "../../types/skills";

/**
 * The models `AppShell` renders.
 *
 * These exist so the shell takes a bounded set of named models and action
 * groups instead of a flat list of business fields and callbacks. A feature
 * owns its slice of state and publishes it here; the shell only lays it out.
 * Adding a field to one feature does not lengthen every intermediate
 * component's prop list.
 */

export type SessionView = {
  sessions: SessionSummary[];
  activeSession: SessionSummary | null;
  activeSessionId: string | null;
  isNewSessionDraft: boolean;
  displayedWorkingDirectory: string;
  editingSessionId: string | null;
  editingTitle: string;
  /** Run identity and status per session, for the sidebar's run indicator. */
  activeRunIdBySession: Record<string, string>;
  runStatusBySession: Record<string, PersistedRunStatus>;
};

export type SessionActions = {
  createSession(): void;
  selectSession(sessionId: string): void;
  startRename(session: SessionSummary): void;
  setEditingTitle(title: string): void;
  commitRename(sessionId: string): void;
  cancelRename(): void;
  deleteSession(sessionId: string): void;
};

export type ConnectionView = {
  bridgeStatus: string;
  socketStatus: string;
};

export type ConnectionActions = {
  runBridgeCheck(): void;
};

export type ConversationView = {
  messages: ChatMessage[];
  messagesRef: RefObject<HTMLDivElement | null>;
  approvals: ToolApprovalRequest[];
  isStreaming: boolean;
};

export type ConversationActions = {
  approvePlan(message: ChatMessage): void;
  respondToApproval(
    approval: ToolApprovalRequest,
    decision: ApprovalDecision,
  ): void;
  cancelRun(): void;
};

export type ComposerView = {
  prompt: string;
  sendMode: SendMode;
  canSend: boolean;
  defaultWorkingDirectory: string;
  permissionPreset: PermissionPreset;
  permissionUpdating: boolean;
  sandboxSetupStatus: string;
  /** Prompts submitted while the session was busy, in submission order. */
  pendingInputs: PendingInput[];
  /** Set when a cancelled or interrupted Run left the queue paused. */
  queuePausedReason?: "cancelled" | "interrupted";
};

export type ComposerActions = {
  chooseDirectory(): void;
  workingDirectoryChange(workingDirectory: string): void;
  submit(event: FormEvent<HTMLFormElement>): void;
  promptChange(prompt: string): void;
  sendModeChange(sendMode: SendMode): void;
  permissionPresetChange(permissionPreset: PermissionPreset): void;
  sandboxSetup(): void;
  steerInput(input: PendingInput): void;
  cancelInput(input: PendingInput): void;
  requeueInput(input: PendingInput): void;
  dismissInput(input: PendingInput): void;
};

export type SkillsView = {
  skills: SkillRecord[];
  selectedSkillIds: Set<string>;
  skillErrors: string[];
  skillNotices: SkillRuntimeNotice[];
  skillsLoading: boolean;
};

export type SkillsActions = {
  toggleSkill(skillId: string): void;
  toggleSkillEnabled(skill: SkillRecord): Promise<void>;
  refreshSkills(): void;
};
