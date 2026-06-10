import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AppShell } from "./components/app-shell/AppShell";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useApiConfig } from "./hooks/useApiConfig";
import { useAutoScroll } from "./hooks/useAutoScroll";
import { useSessions } from "./hooks/useSessions";
import { useTauriBridge } from "./hooks/useTauriBridge";
import { chatReducer, initialChatState } from "./state/chatReducer";
import type { SendMode } from "./types/chat";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

function App() {
  const [chatState, chatDispatch] = useReducer(chatReducer, initialChatState);
  const [prompt, setPrompt] = useState("Inspect the API folder and suggest the first FastAPI route.");
  const [sendMode, setSendMode] = useState<SendMode>("execute");
  const streamingRef = useRef(false);

  const { apiConfig, apiConfigRef, isConfigReady } = useApiConfig();
  const { bridgeStatus, runBridgeCheck, chooseDirectory } = useTauriBridge();
  const getIsStreaming = useCallback(() => streamingRef.current, []);

  const sessions = useSessions({
    apiConfigRef,
    chatDispatch,
    getIsStreaming,
  });

  const agentSocket = useAgentSocket({
    apiConfigRef,
    activeSessionIdRef: sessions.activeSessionIdRef,
    chatDispatch,
    ensureActiveSession: sessions.actions.ensureActiveSession,
    refreshSessionList: sessions.actions.refreshSessionList,
  });

  const messagesRef = useAutoScroll<HTMLDivElement>(chatState.messages);
  const canSend = Boolean(prompt.trim()) && !agentSocket.isStreaming && Boolean(sessions.activeSessionId || sessions.isNewSessionDraft);

  useEffect(() => {
    streamingRef.current = agentSocket.isStreaming;
  }, [agentSocket.isStreaming]);

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
    if (agentSocket.isStreaming) {
      return;
    }

    sessions.actions.startNewSessionDraft();
  }

  async function handleChooseDirectory() {
    if (!sessions.isNewSessionDraft || agentSocket.isStreaming) {
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
    const sent = await agentSocket.sendPrompt(prompt, sendMode);
    if (sent) {
      setPrompt("");
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
      messages={chatState.messages}
      messagesRef={messagesRef}
      bridgeStatus={bridgeStatus}
      socketStatus={agentSocket.socketStatus}
      prompt={prompt}
      sendMode={sendMode}
      isStreaming={agentSocket.isStreaming}
      canSend={canSend}
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
      onApprovePlan={agentSocket.approvePlan}
    />
  );
}

export default App;
