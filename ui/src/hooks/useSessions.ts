import { useCallback, useMemo, useReducer, useRef, useState } from "react";
import { createSession, deleteSession, fetchMessages, fetchSessions, updateSession } from "../api/sessions";
import { initialSessionState, sessionReducer } from "../state/sessionReducer";
import type { ChatAction } from "../state/chatReducer";
import type { ApiRuntimeConfig } from "../types/api";
import type { PermissionPreset, SessionSummary } from "../types/session";
import { sleep } from "../utils/timing";

type UseSessionsOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  chatDispatch: React.Dispatch<ChatAction>;
};

export function useSessions({ apiConfigRef, chatDispatch }: UseSessionsOptions) {
  const [state, dispatch] = useReducer(sessionReducer, initialSessionState);
  const [permissionUpdating, setPermissionUpdating] = useState(false);
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
  const permissionPreset = state.isNewSessionDraft
    ? state.draftPermissionPreset
    : activeSession?.permission_preset ?? "default";

  const startNewSessionDraft = useCallback(() => {
    activeSessionIdRef.current = null;
    dispatch({ type: "newDraftStarted", defaultWorkingDirectory: apiConfigRef.current.defaultWorkingDirectory });
  }, [apiConfigRef, chatDispatch]);

  const selectSession = useCallback(
    async (sessionId: string) => {
      const loadedMessages = await fetchMessages(apiConfigRef.current, sessionId);
      activeSessionIdRef.current = sessionId;
      dispatch({ type: "sessionSelected", sessionId });
      chatDispatch({
        type: "messagesLoaded",
        sessionId,
        messages: loadedMessages,
        preserveTransient: true,
      });
    },
    [apiConfigRef, chatDispatch],
  );

  const reloadSessionMessages = useCallback(
    async (sessionId: string) => {
      const loadedMessages = await fetchMessages(apiConfigRef.current, sessionId);
      chatDispatch({ type: "messagesLoaded", sessionId, messages: loadedMessages });
      return loadedMessages;
    },
    [apiConfigRef, chatDispatch],
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
            chatDispatch({ type: "messagesLoaded", sessionId: loadedSessions[0].id, messages: loadedMessages });
            return;
          }

          dispatch({ type: "sessionsLoaded", sessions: [] });
          activeSessionIdRef.current = null;
          dispatch({ type: "newDraftStarted", defaultWorkingDirectory: config.defaultWorkingDirectory });
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

  const setPermissionPreset = useCallback(
    async (nextPermissionPreset: PermissionPreset) => {
      if (stateRef.current.isNewSessionDraft || !activeSessionIdRef.current) {
        dispatch({
          type: "draftPermissionPresetChanged",
          permissionPreset: nextPermissionPreset,
        });
        return;
      }

      setPermissionUpdating(true);
      try {
        const updated = await updateSession(
          apiConfigRef.current,
          activeSessionIdRef.current,
          { permission_preset: nextPermissionPreset },
        );
        dispatch({ type: "sessionUpdated", session: updated });
      } finally {
        setPermissionUpdating(false);
      }
    },
    [apiConfigRef],
  );

  const commitRename = useCallback(
    async (sessionId: string) => {
      const title = stateRef.current.editingTitle.trim();
      if (!title) {
        dispatch({ type: "renameEnded" });
        return;
      }

      await updateSession(apiConfigRef.current, sessionId, { title });
      dispatch({ type: "sessionsLoaded", sessions: await fetchSessions(apiConfigRef.current) });
      dispatch({ type: "renameEnded" });
    },
    [apiConfigRef],
  );

  const deleteCurrentSession = useCallback(
    async (sessionId: string) => {
      await deleteSession(apiConfigRef.current, sessionId);
      chatDispatch({ type: "sessionMessagesCleared", sessionId });
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
    [apiConfigRef, chatDispatch, selectSession, startNewSessionDraft],
  );

  const ensureActiveSession = useCallback(async () => {
    if (activeSessionIdRef.current) {
      return activeSessionIdRef.current;
    }

    const session = await createSession(
      apiConfigRef.current,
      "New session",
      stateRef.current.draftWorkingDirectory,
      undefined,
      stateRef.current.draftPermissionPreset,
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
    permissionPreset,
    permissionUpdating,
    editingSessionId: state.editingSessionId,
    editingTitle: state.editingTitle,
    actions: {
      initializeSessions,
      refreshSessionList,
      reloadSessionMessages,
      selectSession,
      startNewSessionDraft,
      startRename,
      cancelRename,
      commitRename,
      deleteCurrentSession,
      ensureActiveSession,
      setDraftWorkingDirectory,
      setPermissionPreset,
      setEditingTitle,
    },
  };
}
