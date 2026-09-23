import { useEffect, useReducer, useState } from "react";
import type { FormEvent } from "react";
import { AppShell } from "./components/app-shell/AppShell";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useApiConfig } from "./hooks/useApiConfig";
import { useAutoScroll } from "./hooks/useAutoScroll";
import { useSessions } from "./hooks/useSessions";
import { useSkills } from "./hooks/useSkills";
import { useTauriBridge } from "./hooks/useTauriBridge";
import { setupSandbox } from "./api/sandbox";
import {
  chatReducer,
  initialChatState,
  selectMessages,
  selectPendingInputs,
  selectSessionApprovals,
} from "./state/chatReducer";
import type { PersistedRunStatus, SendMode } from "./types/chat";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

function App() {
  const [chatState, chatDispatch] = useReducer(chatReducer, initialChatState);
  const [prompt, setPrompt] = useState("");
  const [sendMode, setSendMode] = useState<SendMode>("execute");
  const [sandboxSetupStatus, setSandboxSetupStatus] = useState("");
  const { apiConfig, apiConfigRef, isConfigReady } = useApiConfig();
  const { bridgeStatus, runBridgeCheck, chooseDirectory } = useTauriBridge();

  const sessions = useSessions({
    apiConfigRef,
    chatDispatch,
  });

  const skills = useSkills({
    apiConfigRef,
    workspace: sessions.displayedWorkingDirectory,
    sessionKey:
      sessions.activeSessionId ??
      `draft:${sessions.isNewSessionDraft}:${sessions.displayedWorkingDirectory}`,
    enabled: isConfigReady,
  });

  const agentSocket = useAgentSocket({
    apiConfigRef,
    activeSessionIdRef: sessions.activeSessionIdRef,
    chatDispatch,
    ensureActiveSession: sessions.actions.ensureActiveSession,
    refreshSessionList: sessions.actions.refreshSessionList,
    reloadSessionMessages: sessions.actions.reloadSessionMessages,
    onSkillEvent: skills.handleRuntimeEvent,
  });

  const messages = selectMessages(chatState, sessions.activeSessionId);
  const approvals = selectSessionApprovals(chatState, sessions.activeSessionId);
  const pendingInputs = selectPendingInputs(chatState, sessions.activeSessionId);
  const runStatusBySession = Object.values(chatState.runsById).reduce<
    Record<string, PersistedRunStatus>
  >((statuses, run) => {
    statuses[run.sessionId] = run.status;
    return statuses;
  }, {});
  const messagesRef = useAutoScroll<HTMLDivElement>(messages);
  const sessionRunStatus = sessions.activeSessionId
    ? runStatusBySession[sessions.activeSessionId]
    : undefined;
  // A cancelled or interrupted Run leaves the queue paused: the backend only
  // resumes a follow-up whose predecessor completed, so the waiting messages
  // need an explanation and a way out.
  const queuePausedReason =
    !agentSocket.isStreaming &&
    pendingInputs.some((input) => input.status === "pending") &&
    (sessionRunStatus === "cancelled" || sessionRunStatus === "interrupted")
      ? sessionRunStatus
      : undefined;
  // A session with a Run in flight accepts queued follow-ups, so only the
  // absence of a session (and an updating permission preset) blocks sending.
  const canSend =
    Boolean(prompt.trim()) &&
    !sessions.permissionUpdating &&
    Boolean(sessions.activeSessionId || sessions.isNewSessionDraft);

  useEffect(() => {
    if (!isConfigReady) {
      return;
    }

    let cancelled = false;

    async function boot() {
      agentSocket.connectSocket(apiConfig);
      await sessions.actions.initializeSessions(
        apiConfig,
        agentSocket.setSocketStatus,
      );
      if (cancelled) {
        return;
      }
    }

    void boot();

    return () => {
      cancelled = true;
    };
  }, [
    agentSocket.connectSocket,
    agentSocket.setSocketStatus,
    apiConfig,
    isConfigReady,
    sessions.actions.initializeSessions,
  ]);

  function handleCreateSession() {
    sessions.actions.startNewSessionDraft();
  }

  async function handleChooseDirectory() {
    if (!sessions.isNewSessionDraft) {
      return;
    }

    try {
      const selected = await chooseDirectory();
      if (selected) {
        sessions.actions.setDraftWorkingDirectory(selected);
      }
    } catch {
      agentSocket.setSocketStatus("请输入工作目录路径");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const sent = await agentSocket.sendPrompt(
      prompt,
      sendMode,
      skills.selectedSkills,
    );
    if (sent) {
      setPrompt("");
      skills.clearSelection();
    }
  }

  async function handleSandboxSetup() {
    setSandboxSetupStatus("正在准备沙箱…");
    try {
      const result = await setupSandbox(
        apiConfig,
        sessions.displayedWorkingDirectory,
      );
      setSandboxSetupStatus(
        result.ready
          ? `沙箱已就绪（${result.backend}）`
          : "沙箱准备未完成",
      );
    } catch (error) {
      setSandboxSetupStatus(
        error instanceof Error ? error.message : "沙箱准备失败",
      );
    }
  }

  return (
    <AppShell
      sessionView={{
        sessions: sessions.sessions,
        activeSession: sessions.activeSession,
        activeSessionId: sessions.activeSessionId,
        isNewSessionDraft: sessions.isNewSessionDraft,
        displayedWorkingDirectory: sessions.displayedWorkingDirectory,
        editingSessionId: sessions.editingSessionId,
        editingTitle: sessions.editingTitle,
        activeRunIdBySession: agentSocket.activeRunIdBySession,
        runStatusBySession,
      }}
      sessionActions={{
        createSession: handleCreateSession,
        selectSession: sessions.actions.selectSession,
        startRename: sessions.actions.startRename,
        setEditingTitle: sessions.actions.setEditingTitle,
        commitRename: sessions.actions.commitRename,
        cancelRename: sessions.actions.cancelRename,
        deleteSession: sessions.actions.deleteCurrentSession,
      }}
      connectionView={{
        bridgeStatus,
        socketStatus: agentSocket.socketStatus,
      }}
      connectionActions={{ runBridgeCheck }}
      conversationView={{
        messages,
        messagesRef,
        approvals,
        isStreaming: agentSocket.isStreaming,
      }}
      conversationActions={{
        approvePlan: agentSocket.approvePlan,
        respondToApproval: agentSocket.respondToApproval,
        cancelRun: agentSocket.cancelRun,
      }}
      composerView={{
        prompt,
        sendMode,
        canSend,
        defaultWorkingDirectory: apiConfig.defaultWorkingDirectory,
        permissionPreset: sessions.permissionPreset,
        permissionUpdating: sessions.permissionUpdating,
        sandboxSetupStatus,
        pendingInputs,
        queuePausedReason,
      }}
      composerActions={{
        chooseDirectory: handleChooseDirectory,
        workingDirectoryChange: sessions.actions.setDraftWorkingDirectory,
        submit: handleSubmit,
        promptChange: setPrompt,
        sendModeChange: setSendMode,
        permissionPresetChange: (permissionPreset) =>
          void sessions.actions.setPermissionPreset(permissionPreset),
        sandboxSetup: () => void handleSandboxSetup(),
        steerInput: agentSocket.steerInput,
        cancelInput: agentSocket.cancelInput,
        requeueInput: agentSocket.requeueInput,
        dismissInput: agentSocket.dismissInput,
      }}
      skillsView={{
        skills: skills.skills,
        selectedSkillIds: skills.selectedIds,
        skillErrors: skills.errors,
        skillNotices: skills.notices,
        skillsLoading: skills.isLoading,
      }}
      skillsActions={{
        toggleSkill: skills.toggleSelected,
        toggleSkillEnabled: skills.toggleEnabled,
        refreshSkills: () => void skills.refresh(true),
      }}
    />
  );
}

export default App;
