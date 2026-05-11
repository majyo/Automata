import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  Check,
  CheckCircle2,
  Code2,
  FileCode2,
  FolderGit2,
  GitBranch,
  Pencil,
  Play,
  Plus,
  Send,
  Sparkles,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

const API_BASE = "http://127.0.0.1:8765";
const WS_URL = "ws://127.0.0.1:8765/ws/chat";

type SessionSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

type ChatMessage = {
  id: string;
  session_id?: string;
  role: "user" | "agent";
  text: string;
  sequence?: number;
  created_at?: string;
};

type ApiMessage = {
  id: string;
  session_id: string;
  role: "user" | "agent";
  content: string;
  sequence: number;
  created_at: string;
};

type SocketPayload = {
  type: "ready" | "started" | "token" | "done" | "error";
  content?: string;
  message?: ApiMessage | string;
};

const fileChanges = [
  { path: "api/automata_api/routers", state: "+routes" },
  { path: "api/automata_api/services", state: "+agent" },
  { path: "api/automata_api/repositories", state: "+sqlite" },
];

function App() {
  const [bridgeStatus, setBridgeStatus] = useState("Desktop bridge not checked");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("Inspect the API folder and suggest the first FastAPI route.");
  const [socketStatus, setSocketStatus] = useState("Connecting");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isNewSessionDraft, setIsNewSessionDraft] = useState(false);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingSessionIdRef = useRef<string | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? null;

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    connectSocket();
    initializeSessions();

    return () => {
      socketRef.current?.close();
    };
  }, []);

  useEffect(() => {
    messagesRef.current?.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  function connectSocket() {
    const socket = new WebSocket(WS_URL);
    socketRef.current = socket;

    socket.addEventListener("open", () => {
      setSocketStatus("Connected");
    });

    socket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data) as SocketPayload;

      if (payload.type === "ready") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : "Ready");
        return;
      }

      if (payload.type === "started") {
        setSocketStatus("Streaming");
        setIsStreaming(true);
        return;
      }

      if (payload.type === "token" && streamingMessageIdRef.current) {
        const messageId = streamingMessageIdRef.current;
        setMessages((current) =>
          current.map((message) =>
            message.id === messageId
              ? { ...message, text: `${message.text}${payload.content ?? ""}` }
              : message,
          ),
        );
        return;
      }

      if (payload.type === "done") {
        setSocketStatus("Ready");
        setIsStreaming(false);
        streamingMessageIdRef.current = null;
        const sessionId = streamingSessionIdRef.current;
        streamingSessionIdRef.current = null;
        if (sessionId) {
          refreshSessionData(sessionId);
        }
        return;
      }

      if (payload.type === "error") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : "Backend error");
        setIsStreaming(false);
        streamingMessageIdRef.current = null;
        streamingSessionIdRef.current = null;
      }
    });

    socket.addEventListener("close", () => {
      if (socketRef.current !== socket) {
        return;
      }

      setSocketStatus("Offline");
      setIsStreaming(false);
      socketRef.current = null;
    });

    socket.addEventListener("error", () => {
      if (socketRef.current !== socket) {
        return;
      }

      setSocketStatus("Backend offline");
    });
  }

  async function initializeSessions() {
    setSocketStatus("Loading sessions");
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const loadedSessions = await fetchSessions();
        if (loadedSessions.length > 0) {
          setSessions(loadedSessions);
          await selectSession(loadedSessions[0].id);
          return;
        }

        setSessions([]);
        startNewSessionDraft();
        return;
      } catch {
        await sleep(350);
      }
    }

    setSocketStatus("Backend offline");
  }

  async function refreshSessionData(sessionId: string) {
    const [loadedSessions, loadedMessages] = await Promise.all([
      fetchSessions(),
      fetchMessages(sessionId),
    ]);

    setSessions(loadedSessions);
    if (activeSessionIdRef.current === sessionId || streamingSessionIdRef.current === sessionId) {
      setMessages(loadedMessages);
    }
  }

  async function selectSession(sessionId: string) {
    if (isStreaming) {
      return;
    }

    const loadedMessages = await fetchMessages(sessionId);
    activeSessionIdRef.current = sessionId;
    setActiveSessionId(sessionId);
    setIsNewSessionDraft(false);
    setMessages(loadedMessages);
    setEditingSessionId(null);
  }

  function startNewSessionDraft() {
    activeSessionIdRef.current = null;
    setActiveSessionId(null);
    setMessages([]);
    setEditingSessionId(null);
    setIsNewSessionDraft(true);
  }

  function handleCreateSession() {
    if (isStreaming) {
      return;
    }

    startNewSessionDraft();
  }

  function startRename(session: SessionSummary) {
    setEditingSessionId(session.id);
    setEditingTitle(session.title);
  }

  async function commitRename(sessionId: string) {
    const title = editingTitle.trim();
    if (!title) {
      setEditingSessionId(null);
      return;
    }

    await updateSession(sessionId, title);
    setSessions(await fetchSessions());
    setEditingSessionId(null);
  }

  async function handleDeleteSession(sessionId: string) {
    if (isStreaming) {
      return;
    }

    await deleteSession(sessionId);
    const loadedSessions = await fetchSessions();
    if (loadedSessions.length === 0) {
      setSessions([]);
      startNewSessionDraft();
      return;
    }

    setSessions(loadedSessions);
    const nextSessionId = sessionId === activeSessionId ? loadedSessions[0].id : activeSessionId;
    if (nextSessionId) {
      await selectSession(nextSessionId);
    } else {
      startNewSessionDraft();
    }
  }

  async function runBridgeCheck() {
    try {
      setBridgeStatus(await invoke<string>("agent_status", { workspace: "automata" }));
    } catch {
      setBridgeStatus("Open with npm run tauri dev to use the desktop bridge");
    }
  }

  async function sendPrompt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedPrompt = prompt.trim();
    const socket = socketRef.current;
    if (!trimmedPrompt || isStreaming || (!activeSessionId && !isNewSessionDraft)) {
      return;
    }

    let sessionId = activeSessionId;
    if (!sessionId) {
      try {
        const session = await createSession("New session");
        sessionId = session.id;
        setSessions(await fetchSessions());
        activeSessionIdRef.current = session.id;
        setActiveSessionId(session.id);
        setIsNewSessionDraft(false);
        setEditingSessionId(null);
      } catch {
        setSocketStatus("Could not create session");
        return;
      }
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      session_id: sessionId,
      role: "user",
      text: trimmedPrompt,
    };
    const agentMessage: ChatMessage = {
      id: crypto.randomUUID(),
      session_id: sessionId,
      role: "agent",
      text: "",
    };

    setMessages((current) => [...current, userMessage, agentMessage]);
    streamingMessageIdRef.current = agentMessage.id;
    streamingSessionIdRef.current = sessionId;
    setPrompt("");

    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "prompt", session_id: sessionId, prompt: trimmedPrompt }));
    } else {
      setMessages((current) =>
        current.map((message) =>
          message.id === agentMessage.id
            ? {
                ...message,
                text: "Backend is offline. Restart the desktop app and try again.",
              }
            : message,
        ),
      );
      setSocketStatus("Backend offline");
      setIsStreaming(false);
      streamingMessageIdRef.current = null;
      streamingSessionIdRef.current = null;
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Sparkles size={20} />
          </div>
          <div>
            <strong>Automata</strong>
            <span>Local coding agent</span>
          </div>
        </div>

        <div className="sidebar-toolbar">
          <span>Sessions</span>
          <button className="icon-button small" onClick={handleCreateSession} aria-label="New session">
            <Plus size={16} />
          </button>
        </div>

        <nav className="session-list" aria-label="Agent sessions">
          {sessions.map((session) => (
            <button
              className={`session-item ${session.id === activeSessionId ? "active" : ""}`}
              disabled={isStreaming}
              key={session.id}
              onClick={() => selectSession(session.id)}
            >
              <FolderGit2 size={17} />
              <span>
                {editingSessionId === session.id ? (
                  <input
                    autoFocus
                    className="session-title-input"
                    value={editingTitle}
                    onChange={(event) => setEditingTitle(event.currentTarget.value)}
                    onClick={(event) => event.stopPropagation()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        commitRename(session.id);
                      }
                      if (event.key === "Escape") {
                        setEditingSessionId(null);
                      }
                    }}
                  />
                ) : (
                  <strong>{session.title}</strong>
                )}
                <small>
                  <GitBranch size={13} />
                  {session.message_count} messages
                </small>
              </span>
              <em>{session.id === activeSessionId ? "Active" : "Saved"}</em>
              <span className="session-actions">
                {editingSessionId === session.id ? (
                  <>
                    <span
                      className="mini-action"
                      role="button"
                      tabIndex={0}
                      onClick={(event) => {
                        event.stopPropagation();
                        commitRename(session.id);
                      }}
                    >
                      <Check size={13} />
                    </span>
                    <span
                      className="mini-action"
                      role="button"
                      tabIndex={0}
                      onClick={(event) => {
                        event.stopPropagation();
                        setEditingSessionId(null);
                      }}
                    >
                      <X size={13} />
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      className="mini-action"
                      role="button"
                      tabIndex={0}
                      onClick={(event) => {
                        event.stopPropagation();
                        startRename(session);
                      }}
                    >
                      <Pencil size={13} />
                    </span>
                    <span
                      className="mini-action danger"
                      role="button"
                      tabIndex={0}
                      onClick={(event) => {
                        event.stopPropagation();
                        handleDeleteSession(session.id);
                      }}
                    >
                      <Trash2 size={13} />
                    </span>
                  </>
                )}
              </span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button className="icon-button" onClick={handleCreateSession} aria-label="New session">
            <Plus size={18} />
          </button>
          <button className="icon-button" aria-label="Open terminal">
            <TerminalSquare size={18} />
          </button>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">D:/workspace/projects/automata</span>
            <h1>{isNewSessionDraft ? "New session" : activeSession?.title ?? "Agent workspace"}</h1>
          </div>
          <button className="run-button" onClick={runBridgeCheck}>
            <Play size={17} />
            Run bridge check
          </button>
        </header>

        <section className="content-grid">
          <section className="conversation-panel" aria-label="Agent conversation">
            <div className="panel-header">
              <div>
                <span className="eyebrow">Session</span>
                <h2>
                  {isNewSessionDraft
                    ? "No session selected"
                    : activeSession
                      ? `${activeSession.message_count} saved messages`
                      : "Loading session"}
                </h2>
              </div>
              <span className="status-pill" title={socketStatus}>
                <Activity size={14} />
                {socketStatus}
              </span>
            </div>

            {isNewSessionDraft ? (
              <div className="new-session-stage">
                <form className="new-session-dialog" onSubmit={sendPrompt}>
                  <span className="eyebrow">New session</span>
                  <h2>输入一条消息来开始新的会话</h2>
                  <div className="composer draft">
                    <input
                      autoFocus
                      value={prompt}
                      onChange={(event) => setPrompt(event.currentTarget.value)}
                      placeholder="Ask the local coding agent..."
                    />
                    <button type="submit" aria-label="Send prompt" disabled={isStreaming || !prompt.trim()}>
                      <Send size={18} />
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              <>
                <div className="messages" ref={messagesRef}>
                  {messages.length === 0 && (
                    <article className="message agent empty">
                      <p>This session is empty. Send a prompt to start a persisted conversation.</p>
                    </article>
                  )}
                  {messages.map((message) => (
                    <article className={`message ${message.role}`} key={message.id}>
                      {message.role === "user" && (
                        <div className="avatar">
                          <Code2 size={18} />
                        </div>
                      )}
                      <p>{message.text || "..."}</p>
                    </article>
                  ))}
                </div>

                <form className="composer" onSubmit={sendPrompt}>
                  <input
                    value={prompt}
                    onChange={(event) => setPrompt(event.currentTarget.value)}
                    placeholder="Ask the local coding agent..."
                  />
                  <button type="submit" aria-label="Send prompt" disabled={isStreaming || !activeSessionId}>
                    <Send size={18} />
                  </button>
                </form>
              </>
            )}
          </section>

          <aside className="inspector" aria-label="Run details">
            <section className="summary-band">
              <span className="eyebrow">Tauri command</span>
              <strong>{bridgeStatus}</strong>
            </section>

            <section className="task-list">
              <div className="panel-header compact">
                <h2>Task queue</h2>
              </div>
              <div className="task-row complete">
                <CheckCircle2 size={17} />
                <span>SQLite session storage</span>
              </div>
              <div className="task-row">
                <Activity size={17} />
                <span>WebSocket backend: {socketStatus}</span>
              </div>
              <div className="task-row">
                <FileCode2 size={17} />
                <span>{sessions.length} sessions tracked</span>
              </div>
            </section>

            <section className="changes">
              <div className="panel-header compact">
                <h2>Changed files</h2>
              </div>
              {fileChanges.map((change) => (
                <div className="change-row" key={change.path}>
                  <FileCode2 size={16} />
                  <span>{change.path}</span>
                  <em>{change.state}</em>
                </div>
              ))}
            </section>
          </aside>
        </section>
      </section>
    </main>
  );
}

async function fetchSessions(): Promise<SessionSummary[]> {
  return requestJson<SessionSummary[]>("/sessions");
}

async function createSession(title: string): Promise<SessionSummary> {
  return requestJson<SessionSummary>("/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

async function updateSession(sessionId: string, title: string): Promise<SessionSummary> {
  return requestJson<SessionSummary>(`/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`Delete failed: ${response.status}`);
  }
}

async function fetchMessages(sessionId: string): Promise<ChatMessage[]> {
  const messages = await requestJson<ApiMessage[]>(`/sessions/${sessionId}/messages`);
  return messages.map((message) => ({
    id: message.id,
    session_id: message.session_id,
    role: message.role,
    text: message.content,
    sequence: message.sequence,
    created_at: message.created_at,
  }));
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

export default App;
