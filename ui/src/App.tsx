import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
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

const DEFAULT_API_CONFIG: ApiRuntimeConfig = {
  httpBaseUrl: "http://127.0.0.1:8765",
  wsChatUrl: "ws://127.0.0.1:8765/ws/chat",
};
const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000];

type ApiRuntimeConfig = {
  httpBaseUrl: string;
  wsChatUrl: string;
};

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
  role: "user" | "agent" | "tool";
  text: string;
  kind?: "normal" | "plan";
  plan_id?: string;
  plan_status?: PlanStatus;
  sequence?: number;
  created_at?: string;
};

type PersistedPlanStatus = "pending" | "approved" | "executed" | "superseded";
type PlanStatus = PersistedPlanStatus | "approving" | "executing" | "error";
type SendMode = "execute" | "plan";

type ApiMessage = {
  id: string;
  session_id: string;
  role: "user" | "agent";
  content: string;
  sequence: number;
  created_at: string;
  plan_id?: string | null;
  plan_status?: PersistedPlanStatus | null;
};

type SocketPayload =
  | { type: "ready"; message?: string }
  | { type: "started"; session_id: string; prompt: string; mode?: SendMode }
  | { type: "agent_step"; message?: string; step?: number }
  | {
      type: "context_compressed";
      scope?: "history" | "loop";
      before_chars?: number;
      after_chars?: number;
      summary_chars?: number;
      compressed_messages?: number;
      through_sequence?: number;
    }
  | { type: "tool_call"; tool?: string; arguments?: string }
  | { type: "tool_result"; tool?: string; success?: boolean; content?: string }
  | { type: "plan_ready"; session_id: string; plan_id: string; status: "pending"; content: string }
  | { type: "plan_approved"; session_id: string; plan_id: string }
  | { type: "plan_error"; message?: string }
  | { type: "token"; content?: string }
  | { type: "done"; message?: ApiMessage }
  | { type: "error"; message?: string };

const fileChanges = [
  { path: "api/automata_api/routers", state: "+routes" },
  { path: "api/automata_api/agent", state: "+agent" },
  { path: "api/automata_api/repositories", state: "+sqlite" },
];

function App() {
  const [bridgeStatus, setBridgeStatus] = useState("Desktop bridge not checked");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("Inspect the API folder and suggest the first FastAPI route.");
  const [sendMode, setSendMode] = useState<SendMode>("execute");
  const [socketStatus, setSocketStatus] = useState("Connecting");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isNewSessionDraft, setIsNewSessionDraft] = useState(false);
  const [isInspectorExpanded, setIsInspectorExpanded] = useState(true);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const apiConfigRef = useRef<ApiRuntimeConfig>(DEFAULT_API_CONFIG);
  const activeSessionIdRef = useRef<string | null>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingSessionIdRef = useRef<string | null>(null);
  const executingPlanIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const shouldReconnectRef = useRef(true);
  const messagesRef = useRef<HTMLDivElement | null>(null);

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? null;

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    let cancelled = false;
    shouldReconnectRef.current = true;

    async function boot() {
      const config = await loadApiConfig();
      if (cancelled) {
        return;
      }

      apiConfigRef.current = config;
      connectSocket(config);
      await initializeSessions(config);
    }

    void boot();

    return () => {
      cancelled = true;
      shouldReconnectRef.current = false;
      clearReconnectTimer();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  useEffect(() => {
    messagesRef.current?.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  function connectSocket(config = apiConfigRef.current) {
    clearReconnectTimer();
    setSocketStatus("Connecting");

    const socket = new WebSocket(config.wsChatUrl);
    socketRef.current = socket;

    socket.addEventListener("open", () => {
      reconnectAttemptRef.current = 0;
      setSocketStatus("Connected");
    });

    socket.addEventListener("message", (event) => {
      let payload: SocketPayload;
      try {
        payload = JSON.parse(event.data) as SocketPayload;
      } catch {
        setSocketStatus("Invalid backend event");
        return;
      }

      if (payload.type === "ready") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : "Ready");
        return;
      }

      if (payload.type === "started") {
        setSocketStatus("Streaming");
        setIsStreaming(true);
        return;
      }

      if (payload.type === "agent_step") {
        setSocketStatus(typeof payload.message === "string" ? payload.message : `Agent step ${payload.step ?? ""}`);
        return;
      }

      if (payload.type === "context_compressed") {
        setSocketStatus(payload.scope === "loop" ? "Compressed tool context" : "Compressed session context");
        appendRunEventMessage(formatContextCompressed(payload));
        return;
      }

      if (payload.type === "tool_call") {
        setSocketStatus(payload.tool ? `Tool: ${payload.tool}` : "Calling tool");
        appendRunEventMessage(formatToolCall(payload));
        return;
      }

      if (payload.type === "tool_result") {
        setSocketStatus(payload.tool ? `Tool complete: ${payload.tool}` : "Tool complete");
        appendRunEventMessage(formatToolResult(payload));
        return;
      }

      if (payload.type === "plan_ready") {
        setSocketStatus("Plan ready");
        const messageId = streamingMessageIdRef.current ?? crypto.randomUUID();
        streamingMessageIdRef.current = messageId;
        streamingSessionIdRef.current = payload.session_id;
        setMessages((current) =>
          current.some((message) => message.id === messageId)
            ? current.map((message) =>
                message.id === messageId
                  ? {
                      ...message,
                      session_id: payload.session_id,
                      role: "agent",
                      text: payload.content,
                      kind: "plan",
                      plan_id: payload.plan_id,
                      plan_status: "pending",
                    }
                  : message,
              )
            : [
                ...current,
                {
                  id: messageId,
                  session_id: payload.session_id,
                  role: "agent",
                  text: payload.content,
                  kind: "plan",
                  plan_id: payload.plan_id,
                  plan_status: "pending",
                },
              ],
        );
        return;
      }

      if (payload.type === "plan_approved") {
        setSocketStatus("Plan approved");
        executingPlanIdRef.current = payload.plan_id;
        updatePlanMessageStatus(payload.plan_id, "executing");
        return;
      }

      if (payload.type === "plan_error") {
        const message = typeof payload.message === "string" ? payload.message : "Plan error";
        setSocketStatus(message);
        markCurrentPlanError();
        finishStreamingWithError(message);
        return;
      }

      if (payload.type === "token" && streamingMessageIdRef.current) {
        const messageId = streamingMessageIdRef.current;
        const sessionId = streamingSessionIdRef.current ?? undefined;
        const content = payload.content ?? "";
        if (!content) {
          return;
        }

        setMessages((current) =>
          current.some((message) => message.id === messageId)
            ? current.map((message) =>
                message.id === messageId ? { ...message, text: `${message.text}${content}` } : message,
              )
            : [...current, { id: messageId, session_id: sessionId, role: "agent", text: content }],
        );
        return;
      }

      if (payload.type === "done") {
        setSocketStatus("Ready");
        setIsStreaming(false);
        if (executingPlanIdRef.current) {
          updatePlanMessageStatus(executingPlanIdRef.current, "executed");
          executingPlanIdRef.current = null;
        }
        streamingMessageIdRef.current = null;
        const sessionId = streamingSessionIdRef.current;
        streamingSessionIdRef.current = null;
        if (sessionId) {
          refreshSessionList();
        }
        return;
      }

      if (payload.type === "error") {
        const message = typeof payload.message === "string" ? payload.message : "Backend error";
        setSocketStatus(message);
        finishStreamingWithError(message);
      }
    });

    socket.addEventListener("close", () => {
      if (socketRef.current !== socket) {
        return;
      }

      setSocketStatus("Offline");
      socketRef.current = null;
      if (streamingMessageIdRef.current) {
        finishStreamingWithError("Backend connection closed before a response was received.");
      } else {
        setIsStreaming(false);
      }
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      if (socketRef.current !== socket) {
        return;
      }

      setSocketStatus("Backend offline");
    });
  }

  function scheduleReconnect() {
    if (!shouldReconnectRef.current || reconnectTimerRef.current !== null) {
      return;
    }

    const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
    reconnectAttemptRef.current += 1;
    setSocketStatus("Reconnecting");
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      connectSocket(apiConfigRef.current);
    }, delay);
  }

  function clearReconnectTimer() {
    if (reconnectTimerRef.current === null) {
      return;
    }

    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }

  async function initializeSessions(config = apiConfigRef.current) {
    setSocketStatus("Loading sessions");
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const loadedSessions = await fetchSessions(config);
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

  async function refreshSessionList() {
    const loadedSessions = await fetchSessions(apiConfigRef.current);
    setSessions(loadedSessions);
  }

  async function selectSession(sessionId: string) {
    if (isStreaming) {
      return;
    }

    const loadedMessages = await fetchMessages(apiConfigRef.current, sessionId);
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

    await updateSession(apiConfigRef.current, sessionId, title);
    setSessions(await fetchSessions(apiConfigRef.current));
    setEditingSessionId(null);
  }

  async function handleDeleteSession(sessionId: string) {
    if (isStreaming) {
      return;
    }

    await deleteSession(apiConfigRef.current, sessionId);
    const loadedSessions = await fetchSessions(apiConfigRef.current);
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
        const session = await createSession(apiConfigRef.current, "New session");
        sessionId = session.id;
        setSessions(await fetchSessions(apiConfigRef.current));
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

    setMessages((current) => [...current, userMessage]);
    streamingMessageIdRef.current = agentMessage.id;
    streamingSessionIdRef.current = sessionId;
    setPrompt("");

    if (socket?.readyState === WebSocket.OPEN) {
      const payload =
        sendMode === "plan"
          ? { type: "prompt", session_id: sessionId, prompt: trimmedPrompt, mode: "plan" }
          : { type: "prompt", session_id: sessionId, prompt: trimmedPrompt };
      socket.send(JSON.stringify(payload));
    } else {
      setMessages((current) => [
        ...current,
        {
          ...agentMessage,
          text: "Backend is offline. Restart the desktop app and try again.",
        },
      ]);
      setSocketStatus("Backend offline");
      scheduleReconnect();
      setIsStreaming(false);
      streamingMessageIdRef.current = null;
      streamingSessionIdRef.current = null;
    }
  }

  function approvePlan(message: ChatMessage) {
    const socket = socketRef.current;
    const sessionId = message.session_id ?? activeSessionIdRef.current;
    const planId = message.plan_id;
    if (!sessionId || !planId || isStreaming || message.plan_status !== "pending") {
      return;
    }

    const agentMessage: ChatMessage = {
      id: crypto.randomUUID(),
      session_id: sessionId,
      role: "agent",
      text: "",
    };

    updatePlanMessageStatus(planId, "approving");
    streamingMessageIdRef.current = agentMessage.id;
    streamingSessionIdRef.current = sessionId;
    executingPlanIdRef.current = planId;
    setIsStreaming(true);
    setMessages((current) => [...current, agentMessage]);

    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "approve_plan", session_id: sessionId, plan_id: planId }));
      return;
    }

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
    updatePlanMessageStatus(planId, "error");
    setSocketStatus("Backend offline");
    scheduleReconnect();
    setIsStreaming(false);
    streamingMessageIdRef.current = null;
    streamingSessionIdRef.current = null;
    executingPlanIdRef.current = null;
  }

  function finishStreamingWithError(errorText: string) {
    const messageId = streamingMessageIdRef.current;
    const sessionId = streamingSessionIdRef.current;
    const planId = executingPlanIdRef.current;

    if (messageId) {
      setMessages((current) =>
        current.some((message) => message.id === messageId)
          ? current.map((message) =>
              message.id === messageId && !message.text.trim() ? { ...message, text: errorText } : message,
            )
          : [
              ...current,
              {
                id: messageId,
                session_id: sessionId ?? undefined,
                role: "agent",
                text: errorText,
              },
            ],
      );
    }

    setIsStreaming(false);
    streamingMessageIdRef.current = null;
    streamingSessionIdRef.current = null;
    executingPlanIdRef.current = null;

    if (planId) {
      updatePlanMessageStatus(planId, "error");
    }

    if (sessionId) {
      void refreshSessionList().catch(() => undefined);
    }
  }

  function updatePlanMessageStatus(planId: string, status: PlanStatus) {
    setMessages((current) =>
      current.map((message) =>
        message.plan_id === planId
          ? {
              ...message,
              plan_status: status,
            }
          : message,
      ),
    );
  }

  function markCurrentPlanError() {
    const planId = executingPlanIdRef.current;
    if (planId) {
      updatePlanMessageStatus(planId, "error");
      return;
    }

    setMessages((current) =>
      current.map((message) =>
        message.kind === "plan" && (message.plan_status === "pending" || message.plan_status === "approving")
          ? { ...message, plan_status: "error" }
          : message,
      ),
    );
  }

  function appendRunEventMessage(text: string) {
    const sessionId = streamingSessionIdRef.current ?? activeSessionIdRef.current ?? undefined;
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "tool",
        text,
      },
    ]);
  }

  function renderComposer(options: { autoFocus?: boolean; draft?: boolean } = {}) {
    const canSend = Boolean(prompt.trim()) && !isStreaming && Boolean(activeSessionId || isNewSessionDraft);

    return (
      <div className={`composer ${options.draft ? "draft" : ""}`}>
        <div className="mode-toggle" aria-label="Prompt mode">
          <button
            type="button"
            className={sendMode === "execute" ? "active" : ""}
            onClick={() => setSendMode("execute")}
            disabled={isStreaming}
            title="Execute prompt"
          >
            <Play size={14} />
            Execute
          </button>
          <button
            type="button"
            className={sendMode === "plan" ? "active" : ""}
            onClick={() => setSendMode("plan")}
            disabled={isStreaming}
            title="Generate a plan"
          >
            <CheckCircle2 size={14} />
            Plan
          </button>
        </div>
        <input
          autoFocus={options.autoFocus}
          value={prompt}
          onChange={(event) => setPrompt(event.currentTarget.value)}
          placeholder="Ask the local coding agent..."
        />
        <button className="composer-submit" type="submit" aria-label="Send prompt" disabled={!canSend}>
          <Send size={18} />
        </button>
      </div>
    );
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

        <aside className={`floating-inspector ${isInspectorExpanded ? "expanded" : "collapsed"}`} aria-label="Run details">
          <button
            className="floating-inspector-toggle"
            type="button"
            aria-expanded={isInspectorExpanded}
            onClick={() => setIsInspectorExpanded((expanded) => !expanded)}
          >
            <span className="floating-status">
              <Activity size={15} />
              <span>{isInspectorExpanded ? "Run details" : socketStatus}</span>
            </span>
            {isInspectorExpanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </button>

          {isInspectorExpanded && (
            <div className="floating-inspector-body">
              <section className="summary-band">
                <span className="eyebrow">Tauri command</span>
                <strong>{bridgeStatus}</strong>
              </section>

              <section className="task-list">
                <div className="panel-header compact">
                  <h2>Task queue</h2>
                  <span className="status-pill" title={socketStatus}>
                    <Activity size={14} />
                    {socketStatus}
                  </span>
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
            </div>
          )}
        </aside>

        <section className="workspace-main">
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
                  {renderComposer({ autoFocus: true, draft: true })}
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
                    <article className={`message ${message.role} ${message.kind === "plan" ? "plan" : ""}`} key={message.id}>
                      {message.role === "user" && (
                        <div className="avatar">
                          <Code2 size={18} />
                        </div>
                      )}
                      {message.kind === "plan" ? (
                        <div className="plan-bubble">
                          <div className="plan-header">
                            <span>
                              <CheckCircle2 size={15} />
                              Plan
                            </span>
                            <em className={`plan-status ${message.plan_status ?? "pending"}`}>
                              {formatPlanStatus(message.plan_status)}
                            </em>
                          </div>
                          <p>{message.text || "..."}</p>
                          <div className="plan-actions">
                            <button
                              type="button"
                              onClick={() => approvePlan(message)}
                              disabled={isStreaming || message.plan_status !== "pending"}
                            >
                              <Play size={15} />
                              Approve plan
                            </button>
                          </div>
                        </div>
                      ) : (
                        <p>{message.text || "..."}</p>
                      )}
                    </article>
                  ))}
                </div>

                <form className="composer-form" onSubmit={sendPrompt}>
                  {renderComposer()}
                </form>
              </>
            )}
          </section>
        </section>
      </section>
    </main>
  );
}

async function loadApiConfig(): Promise<ApiRuntimeConfig> {
  try {
    const config = await invoke<ApiRuntimeConfig>("api_config");
    if (config.httpBaseUrl.trim() && config.wsChatUrl.trim()) {
      return config;
    }
  } catch {
    return DEFAULT_API_CONFIG;
  }

  return DEFAULT_API_CONFIG;
}

function formatPlanStatus(status?: PlanStatus): string {
  if (status === "approving") {
    return "Approving";
  }
  if (status === "approved") {
    return "Approved";
  }
  if (status === "executing") {
    return "Executing";
  }
  if (status === "executed") {
    return "Executed";
  }
  if (status === "superseded") {
    return "Superseded";
  }
  if (status === "error") {
    return "Error";
  }
  return "Pending";
}

async function fetchSessions(config: ApiRuntimeConfig): Promise<SessionSummary[]> {
  return requestJson<SessionSummary[]>(config, "/sessions");
}

async function createSession(config: ApiRuntimeConfig, title: string): Promise<SessionSummary> {
  return requestJson<SessionSummary>(config, "/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

async function updateSession(config: ApiRuntimeConfig, sessionId: string, title: string): Promise<SessionSummary> {
  return requestJson<SessionSummary>(config, `/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

async function deleteSession(config: ApiRuntimeConfig, sessionId: string): Promise<void> {
  const response = await fetch(`${config.httpBaseUrl}/sessions/${sessionId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`Delete failed: ${response.status}`);
  }
}

async function fetchMessages(config: ApiRuntimeConfig, sessionId: string): Promise<ChatMessage[]> {
  const messages = await requestJson<ApiMessage[]>(config, `/sessions/${sessionId}/messages`);
  return messages.map((message) => ({
    id: message.id,
    session_id: message.session_id,
    role: message.role,
    text: message.content,
    kind: message.plan_id ? "plan" : "normal",
    plan_id: message.plan_id ?? undefined,
    plan_status: message.plan_status ?? undefined,
    sequence: message.sequence,
    created_at: message.created_at,
  }));
}

async function requestJson<T>(config: ApiRuntimeConfig, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${config.httpBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

function formatContextCompressed(payload: Extract<SocketPayload, { type: "context_compressed" }>): string {
  const scope = payload.scope === "loop" ? "tool context" : "session context";
  const compressed = typeof payload.compressed_messages === "number" ? `${payload.compressed_messages} messages` : "context";
  return `Context compressed: ${scope}\nCompressed ${compressed}.`;
}

function formatToolCall(payload: Extract<SocketPayload, { type: "tool_call" }>): string {
  const tool = payload.tool ?? "unknown_tool";
  const argumentsText = compactToolText(payload.arguments ?? "{}");
  return `Tool call: ${tool}\n${argumentsText}`;
}

function formatToolResult(payload: Extract<SocketPayload, { type: "tool_result" }>): string {
  const tool = payload.tool ?? "unknown_tool";
  const status = payload.success === false ? "failed" : "completed";
  const content = compactToolText(payload.content ?? "");
  return content ? `Tool result: ${tool} ${status}\n${content}` : `Tool result: ${tool} ${status}`;
}

function compactToolText(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }

  return trimmed.length > 700 ? `${trimmed.slice(0, 700)}...` : trimmed;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

export default App;
