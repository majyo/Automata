import { useCallback, useMemo, useReducer, useRef } from "react";
import { createSession, deleteSession, fetchMessages, fetchSessions, updateSession } from "../api/sessions";
import { initialSessionState, sessionReducer } from "../state/sessionReducer";
import type { ChatAction } from "../state/chatReducer";
import type { ApiRuntimeConfig } from "../types/api";
import type { SessionSummary } from "../types/session";
import { sleep } from "../utils/timing";

type UseSessionsOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  chatDispatch: React.Dispatch<ChatAction>;
  getIsStreaming(): boolean;
};

export function useSessions({ apiConfigRef, chatDispatch, getIsStreaming }: UseSessionsOptions) {
  const [state, dispatch] = useReducer(sessionReducer, initialSessionState);
  const activeSessionIdRef = useRef<string | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const activeSession = useMemo(
    () => state.sessions.find((session) => session.id === state.activeSessionId) ?? null,
    [state.activeSessionId, state.sessions],
  );

  const displayedWorkingDirectory = state.isNewSessionDraft
    ? state.draftWorkingDirectory
    : activeSession?.working_directory ?? apiConfigRef.current.defaultWorkingDirectory;

  const startNewSessionDraft = useCallback(() => {
    activeSessionIdRef.current = null;
    dispatch({ type: "newDraftStarted", defaultWorkingDirectory: apiConfigRef.current.defaultWorkingDirectory });
    chatDispatch({ type: "messagesCleared" });
  }, [apiConfigRef, chatDispatch]);

  const selectSession = useCallback(
    async (sessionId: string) => {
      if (getIsStreaming()) {
        return;
      }

      const loadedMessages = await fetchMessages(apiConfigRef.current, sessionId);
      activeSessionIdRef.current = sessionId;
      dispatch({ type: "sessionSelected", sessionId });
      chatDispatch({ type: "messagesLoaded", messages: loadedMessages });
    },
    [apiConfigRef, chatDispatch, getIsStreaming],
  );

  const refreshSessionList = useCallback(async () => {
    const loadedSessions = await fetchSessions(apiConfigRef.current);
    dispatch({ type: "sessionsLoaded", sessions: loadedSessions });
    return loadedSessions;
  }, [apiConfigRef]);

  const initializeSessions = useCallback(
    async (config: ApiRuntimeConfig, setSocketStatus: (status: string) => void) => {
      setSocketStatus("Loading sessions");
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          const loadedSessions = await fetchSessions(config);
          if (loadedSessions.length > 0) {
            dispatch({ type: "sessionsLoaded", sessions: loadedSessions });
            const loadedMessages = await fetchMessages(config, loadedSessions[0].id);
            activeSessionIdRef.current = loadedSessions[0].id;
            dispatch({ type: "sessionSelected", sessionId: loadedSessions[0].id });
            chatDispatch({ type: "messagesLoaded", messages: loadedMessages });
            return;
          }

          dispatch({ type: "sessionsLoaded", sessions: [] });
          activeSessionIdRef.current = null;
          dispatch({ type: "newDraftStarted", defaultWorkingDirectory: config.defaultWorkingDirectory });
          chatDispatch({ type: "messagesCleared" });
          return;
        } catch {
          await sleep(350);
        }
      }

      setSocketStatus("Backend offline");
    },
    [chatDispatch],
  );

  const startRename = useCallback((session: SessionSummary) => {
    dispatch({ type: "renameStarted", session });
  }, []);

  const cancelRename = useCallback(() => {
    dispatch({ type: "renameEnded" });
  }, []);

  const setEditingTitle = useCallback((title: string) => {
    dispatch({ type: "editingTitleChanged", title });
  }, []);

  const setDraftWorkingDirectory = useCallback((workingDirectory: string) => {
    dispatch({ type: "draftWorkingDirectoryChanged", workingDirectory });
  }, []);

  const commitRename = useCallback(
    async (sessionId: string) => {
      const title = stateRef.current.editingTitle.trim();
      if (!title) {
        dispatch({ type: "renameEnded" });
        return;
      }

      await updateSession(apiConfigRef.current, sessionId, title);
      dispatch({ type: "sessionsLoaded", sessions: await fetchSessions(apiConfigRef.current) });
      dispatch({ type: "renameEnded" });
    },
    [apiConfigRef],
  );

  const deleteCurrentSession = useCallback(
    async (sessionId: string) => {
      if (getIsStreaming()) {
        return;
      }

      await deleteSession(apiConfigRef.current, sessionId);
      const loadedSessions = await fetchSessions(apiConfigRef.current);
      if (loadedSessions.length === 0) {
        dispatch({ type: "sessionsLoaded", sessions: [] });
        startNewSessionDraft();
        return;
      }

      dispatch({ type: "sessionsLoaded", sessions: loadedSessions });
      const nextSessionId = sessionId === activeSessionIdRef.current ? loadedSessions[0].id : activeSessionIdRef.current;
      if (nextSessionId) {
        await selectSession(nextSessionId);
      } else {
        startNewSessionDraft();
      }
    },
    [apiConfigRef, getIsStreaming, selectSession, startNewSessionDraft],
  );

  const ensureActiveSession = useCallback(async () => {
    if (activeSessionIdRef.current) {
      return activeSessionIdRef.current;
    }

    const session = await createSession(
      apiConfigRef.current,
      "New session",
      stateRef.current.draftWorkingDirectory,
    );
    const loadedSessions = await fetchSessions(apiConfigRef.current);
    dispatch({ type: "sessionsLoaded", sessions: loadedSessions });
    activeSessionIdRef.current = session.id;
    dispatch({ type: "sessionSelected", sessionId: session.id });
    return session.id;
  }, [apiConfigRef]);

  return {
    sessions: state.sessions,
    activeSession,
    activeSessionId: state.activeSessionId,
    activeSessionIdRef,
    isNewSessionDraft: state.isNewSessionDraft,
    draftWorkingDirectory: state.draftWorkingDirectory,
    displayedWorkingDirectory,
    editingSessionId: state.editingSessionId,
    editingTitle: state.editingTitle,
    actions: {
      initializeSessions,
      refreshSessionList,
      selectSession,
      startNewSessionDraft,
      startRename,
      cancelRename,
      commitRename,
      deleteCurrentSession,
      ensureActiveSession,
      setDraftWorkingDirectory,
      setEditingTitle,
    },
  };
}
