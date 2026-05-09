import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  CheckCircle2,
  Code2,
  FileCode2,
  FolderGit2,
  GitBranch,
  Play,
  Send,
  Settings2,
  Sparkles,
  TerminalSquare,
} from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

const sessions = [
  { name: "Automata UI shell", status: "Active", branch: "main" },
  { name: "Python sidecar plan", status: "Draft", branch: "agent/api" },
  { name: "Diff review pass", status: "Idle", branch: "review/demo" },
];

type ChatMessage = {
  id: string;
  role: "user" | "agent";
  text: string;
};

const initialTranscript: ChatMessage[] = [
  {
    id: "seed-user",
    role: "user",
    text: "Create a small Tauri desktop shell for the local coding agent.",
  },
  {
    id: "seed-agent-1",
    role: "agent",
    text: "Scaffolded React, wired the desktop bridge, and prepared the workspace for a Python sidecar.",
  },
  {
    id: "seed-agent-2",
    role: "agent",
    text: "Next step: connect FastAPI over localhost WebSocket and stream task events into this view.",
  },
];

const fileChanges = [
  { path: "ui/src/App.tsx", state: "+184" },
  { path: "ui/src/App.css", state: "+312" },
  { path: "ui/src-tauri/src/lib.rs", state: "+8" },
];

function App() {
  const [bridgeStatus, setBridgeStatus] = useState("Desktop bridge not checked");
  const [messages, setMessages] = useState(initialTranscript);
  const [prompt, setPrompt] = useState("Inspect the API folder and suggest the first FastAPI route.");
  const [socketStatus, setSocketStatus] = useState("Connecting");
  const [isStreaming, setIsStreaming] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const socket = new WebSocket("ws://127.0.0.1:8765/ws/chat");
    socketRef.current = socket;

    socket.addEventListener("open", () => {
      setSocketStatus("Connected");
    });

    socket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data) as {
        type: "ready" | "started" | "token" | "done" | "error";
        content?: string;
        message?: string;
      };

      if (payload.type === "ready") {
        setSocketStatus("Ready");
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
        return;
      }

      if (payload.type === "error") {
        setSocketStatus(payload.message ?? "Backend error");
        setIsStreaming(false);
        streamingMessageIdRef.current = null;
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

    return () => {
      socket.close();
    };
  }, []);

  useEffect(() => {
    messagesRef.current?.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  async function runBridgeCheck() {
    try {
      setBridgeStatus(await invoke<string>("agent_status", { workspace: "automata" }));
    } catch {
      setBridgeStatus("Open with npm run tauri dev to use the desktop bridge");
    }
  }

  function sendPrompt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedPrompt = prompt.trim();
    const socket = socketRef.current;
    if (!trimmedPrompt || isStreaming) {
      return;
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: trimmedPrompt,
    };
    const agentMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "agent",
      text: "",
    };

    setMessages((current) => [...current, userMessage, agentMessage]);
    streamingMessageIdRef.current = agentMessage.id;
    setPrompt("");

    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "prompt", prompt: trimmedPrompt }));
    } else {
      setMessages((current) =>
        current.map((message) =>
          message.id === agentMessage.id
            ? {
                ...message,
                text: "Backend is offline. Start the demo API on ws://127.0.0.1:8765/ws/chat and try again.",
              }
            : message,
        ),
      );
      setSocketStatus("Backend offline");
      setIsStreaming(false);
      streamingMessageIdRef.current = null;
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

        <nav className="session-list" aria-label="Agent sessions">
          {sessions.map((session) => (
            <button className="session-item" key={session.name}>
              <FolderGit2 size={17} />
              <span>
                <strong>{session.name}</strong>
                <small>
                  <GitBranch size={13} />
                  {session.branch}
                </small>
              </span>
              <em>{session.status}</em>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button className="icon-button" aria-label="Open settings">
            <Settings2 size={18} />
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
            <h1>Agent workspace</h1>
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
                <h2>Implementation thread</h2>
              </div>
              <span className="status-pill">
                <Activity size={14} />
                {socketStatus}
              </span>
            </div>

            <div className="messages" ref={messagesRef}>
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
              <button type="submit" aria-label="Send prompt" disabled={isStreaming}>
                <Send size={18} />
              </button>
            </form>
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
                <span>Scaffold desktop app</span>
              </div>
              <div className="task-row">
                <Activity size={17} />
                <span>WebSocket backend: {socketStatus}</span>
              </div>
              <div className="task-row">
                <FileCode2 size={17} />
                <span>Add Monaco editor surface</span>
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

export default App;
