import { useEffect, useReducer, useState } from "react";
import type { FormEvent } from "react";
import { AppShell } from "./components/app-shell/AppShell";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useApiConfig } from "./hooks/useApiConfig";
import { useAutoScroll } from "./hooks/useAutoScroll";
import { useSessions } from "./hooks/useSessions";
import { useSkills } from "./hooks/useSkills";
import { useTauriBridge } from "./hooks/useTauriBridge";
import {
  chatReducer,
  initialChatState,
  selectMessages,
  selectSessionApprovals,
} from "./state/chatReducer";
import type { PersistedRunStatus, SendMode } from "./types/chat";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

function App() {
  const [chatState, chatDispatch] = useReducer(chatReducer, initialChatState);
  const [prompt, setPrompt] = useState("Inspect the API folder and suggest the first FastAPI route.");
  const [sendMode, setSendMode] = useState<SendMode>("execute");
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
  const runStatusBySession = Object.values(chatState.runsById).reduce<
    Record<string, PersistedRunStatus>
  >((statuses, run) => {
    statuses[run.sessionId] = run.status;
    return statuses;
  }, {});
  const messagesRef = useAutoScroll<HTMLDivElement>(messages);
  const canSend =
    Boolean(prompt.trim()) &&
    !sessions.permissionUpdating &&
    !agentSocket.isSessionRunning(sessions.activeSessionId) &&
    Boolean(sessions.activeSessionId || sessions.isNewSessionDraft);

  useEffect(() => {
    if (!isConfigReady) {
      return;
    }

    let cancelled = false;

    async function boot() {
      agentSocket.connectSocket(apiConfig);
      await sessions.actions.initializeSessions(apiConfig, agentSocket.setSocketStatus);
      if (cancelled) {
        return;
      }
    }

    void boot();

    return () => {
      cancelled = true;
    };
  }, [agentSocket.connectSocket, agentSocket.setSocketStatus, apiConfig, isConfigReady, sessions.actions.initializeSessions]);

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
      agentSocket.setSocketStatus("Type a working directory path");
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

  return (
    <AppShell
      sessions={sessions.sessions}
      activeSession={sessions.activeSession}
      activeSessionId={sessions.activeSessionId}
      isNewSessionDraft={sessions.isNewSessionDraft}
      displayedWorkingDirectory={sessions.displayedWorkingDirectory}
      defaultWorkingDirectory={apiConfig.defaultWorkingDirectory}
      editingSessionId={sessions.editingSessionId}
      editingTitle={sessions.editingTitle}
      messages={messages}
      messagesRef={messagesRef}
      bridgeStatus={bridgeStatus}
      socketStatus={agentSocket.socketStatus}
      prompt={prompt}
      sendMode={sendMode}
      permissionPreset={sessions.permissionPreset}
      permissionUpdating={sessions.permissionUpdating}
      isStreaming={agentSocket.isStreaming}
      canSend={canSend}
      approvals={approvals}
      skills={skills.skills}
      selectedSkillIds={skills.selectedIds}
      skillErrors={skills.errors}
      skillNotices={skills.notices}
      skillsLoading={skills.isLoading}
      activeRunIdBySession={agentSocket.activeRunIdBySession}
      runStatusBySession={runStatusBySession}
      onCreateSession={handleCreateSession}
      onSelectSession={sessions.actions.selectSession}
      onStartRename={sessions.actions.startRename}
      onEditingTitleChange={sessions.actions.setEditingTitle}
      onCommitRename={sessions.actions.commitRename}
      onCancelRename={sessions.actions.cancelRename}
      onDeleteSession={sessions.actions.deleteCurrentSession}
      onChooseDirectory={handleChooseDirectory}
      onWorkingDirectoryChange={sessions.actions.setDraftWorkingDirectory}
      onRunBridgeCheck={runBridgeCheck}
      onSubmit={handleSubmit}
      onPromptChange={setPrompt}
      onSendModeChange={setSendMode}
      onPermissionPresetChange={(permissionPreset) =>
        void sessions.actions.setPermissionPreset(permissionPreset)
      }
      onApprovePlan={agentSocket.approvePlan}
      onRespondToApproval={agentSocket.respondToApproval}
      onCancelRun={agentSocket.cancelRun}
      onToggleSkill={skills.toggleSelected}
      onToggleSkillEnabled={skills.toggleEnabled}
      onRefreshSkills={() => void skills.refresh(true)}
    />
  );
}

export default App;
