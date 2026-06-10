import type { SessionSummary } from "../types/session";

export type SessionState = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  isNewSessionDraft: boolean;
  draftWorkingDirectory: string;
  editingSessionId: string | null;
  editingTitle: string;
};

export type SessionAction =
  | { type: "sessionsLoaded"; sessions: SessionSummary[] }
  | { type: "sessionSelected"; sessionId: string }
  | { type: "newDraftStarted"; defaultWorkingDirectory: string }
  | { type: "draftWorkingDirectoryChanged"; workingDirectory: string }
  | { type: "renameStarted"; session: SessionSummary }
  | { type: "editingTitleChanged"; title: string }
  | { type: "renameEnded" };

export const initialSessionState: SessionState = {
  sessions: [],
  activeSessionId: null,
  isNewSessionDraft: false,
  draftWorkingDirectory: "",
  editingSessionId: null,
  editingTitle: "",
};

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  if (action.type === "sessionsLoaded") {
    return { ...state, sessions: action.sessions };
  }

  if (action.type === "sessionSelected") {
    return {
      ...state,
      activeSessionId: action.sessionId,
      isNewSessionDraft: false,
      editingSessionId: null,
    };
  }

  if (action.type === "newDraftStarted") {
    return {
      ...state,
      activeSessionId: null,
      isNewSessionDraft: true,
      draftWorkingDirectory: action.defaultWorkingDirectory,
      editingSessionId: null,
    };
  }

  if (action.type === "draftWorkingDirectoryChanged") {
    return { ...state, draftWorkingDirectory: action.workingDirectory };
  }

  if (action.type === "renameStarted") {
    return {
      ...state,
      editingSessionId: action.session.id,
      editingTitle: action.session.title,
    };
  }

  if (action.type === "editingTitleChanged") {
    return { ...state, editingTitle: action.title };
  }

  if (action.type === "renameEnded") {
    return { ...state, editingSessionId: null };
  }

  return state;
}
