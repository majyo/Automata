import { useEffect, useRef, useState } from "react";
import { ConversationPanel } from "../../features/conversation/components/ConversationPanel";
import { Sidebar } from "./Sidebar";
import { InspectorSheet } from "./InspectorSheet";
import { Topbar } from "./Topbar";
import type {
  ComposerActions,
  ComposerView,
  ConnectionActions,
  ConnectionView,
  ConversationActions,
  ConversationView,
  SessionActions,
  SessionView,
  SkillsActions,
  SkillsView,
} from "./viewModel";

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "automata-theme";

type AppShellProps = {
  sessionView: SessionView;
  sessionActions: SessionActions;
  connectionView: ConnectionView;
  connectionActions: ConnectionActions;
  conversationView: ConversationView;
  conversationActions: ConversationActions;
  composerView: ComposerView;
  composerActions: ComposerActions;
  skillsView: SkillsView;
  skillsActions: SkillsActions;
};

export function AppShell({
  sessionView,
  sessionActions,
  connectionView,
  connectionActions,
  conversationView,
  conversationActions,
  composerView,
  composerActions,
  skillsView,
  skillsActions,
}: AppShellProps) {
  const {
    sessions,
    activeSession,
    activeSessionId,
    isNewSessionDraft,
    displayedWorkingDirectory,
    editingSessionId,
    editingTitle,
    activeRunIdBySession,
    runStatusBySession,
  } = sessionView;
  const { messages, messagesRef, approvals, isStreaming } = conversationView;
  const { bridgeStatus, socketStatus } = connectionView;
  const {
    prompt,
    sendMode,
    permissionPreset,
    permissionUpdating,
    defaultWorkingDirectory,
    sandboxSetupStatus,
    canSend,
    pendingInputs,
    queuePausedReason,
  } = composerView;
  const {
    skills,
    selectedSkillIds,
    skillErrors,
    skillNotices,
    skillsLoading,
  } = skillsView;
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const [isInspectorOpen, setIsInspectorOpen] = useState(
    () => window.innerWidth >= 1160,
  );
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const shellRef = useRef<HTMLDivElement>(null);
  const sidebarModal = isSidebarOpen && viewportWidth <= 760;
  const inspectorModal = isInspectorOpen && viewportWidth < 1160;

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!sidebarModal && !inspectorModal) return;
    const panel = shellRef.current?.querySelector<HTMLElement>(
      sidebarModal ? ".sidebar" : ".inspector-sheet",
    );
    if (!panel) return;
    const returnTarget = shellRef.current?.querySelector<HTMLElement>(
      sidebarModal ? ".sidebar-toggle" : ".topbar-actions button",
    );
    const controls = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), summary, [tabindex='0']",
        ),
      ).filter((element) => element.getClientRects().length > 0);
    controls()[0]?.focus();
    function trapFocus(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    panel.addEventListener("keydown", trapFocus);
    return () => {
      panel.removeEventListener("keydown", trapFocus);
      requestAnimationFrame(() => returnTarget?.focus());
    };
  }, [sidebarModal, inspectorModal]);

  useEffect(() => {
    function closePanels(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsSidebarOpen(false);
        setIsInspectorOpen(false);
      }
    }
    window.addEventListener("keydown", closePanels);
    return () => window.removeEventListener("keydown", closePanels);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage may be unavailable in some webview contexts; theme still applies for the session.
    }
  }, [theme]);

  const title = isNewSessionDraft
    ? "新建会话"
    : (activeSession?.title ?? "开始工作");

  return (
    <div className="app-shell" ref={shellRef}>
      <div className="app-content">
        {isSidebarOpen && (
          <button
            className="panel-backdrop sidebar-backdrop"
            type="button"
            aria-label="关闭会话目录遮罩"
            onClick={() => setIsSidebarOpen(false)}
          />
        )}
        <Sidebar
          open={isSidebarOpen}
          modal={sidebarModal}
          inactive={inspectorModal}
          onClose={() => setIsSidebarOpen(false)}
          theme={theme}
          onToggleTheme={() =>
            setTheme((current) => (current === "dark" ? "light" : "dark"))
          }
          sessions={sessions}
          activeSessionId={activeSessionId}
          editingSessionId={editingSessionId}
          editingTitle={editingTitle}
          activeRunIdBySession={activeRunIdBySession}
          runStatusBySession={runStatusBySession}
          onCreateSession={() => {
            sessionActions.createSession();
            setIsSidebarOpen(false);
          }}
          onSelectSession={(sessionId) => {
            sessionActions.selectSession(sessionId);
            setIsSidebarOpen(false);
          }}
          onStartRename={sessionActions.startRename}
          onEditingTitleChange={sessionActions.setEditingTitle}
          onCommitRename={sessionActions.commitRename}
          onCancelRename={sessionActions.cancelRename}
          onDeleteSession={sessionActions.deleteSession}
        />

        <main className="workspace" inert={sidebarModal || inspectorModal}>
          <Topbar
            title={title}
            displayedWorkingDirectory={displayedWorkingDirectory}
            socketStatus={socketStatus}
            isInspectorOpen={isInspectorOpen}
            onToggleSidebar={() => {
              setIsSidebarOpen((open) => !open);
              setIsInspectorOpen(false);
            }}
            onToggleInspector={() => {
              setIsInspectorOpen((open) => !open);
              setIsSidebarOpen(false);
            }}
          />

          <div className="workspace-body">
            <ConversationPanel
              isNewSessionDraft={isNewSessionDraft}
              messages={messages}
              messagesRef={messagesRef}
              displayedWorkingDirectory={displayedWorkingDirectory}
              defaultWorkingDirectory={defaultWorkingDirectory}
              prompt={prompt}
              sendMode={sendMode}
              permissionPreset={permissionPreset}
              permissionUpdating={permissionUpdating}
              sandboxSetupStatus={sandboxSetupStatus}
              isStreaming={isStreaming}
              canSend={canSend}
              pendingInputs={pendingInputs}
              queuePausedReason={queuePausedReason}
              approvals={approvals}
              skills={skills}
              selectedSkillIds={selectedSkillIds}
              skillErrors={skillErrors}
              skillNotices={skillNotices}
              skillsLoading={skillsLoading}
              onChooseDirectory={composerActions.chooseDirectory}
              onWorkingDirectoryChange={composerActions.workingDirectoryChange}
              onSubmit={composerActions.submit}
              onPromptChange={composerActions.promptChange}
              onSendModeChange={composerActions.sendModeChange}
              onPermissionPresetChange={composerActions.permissionPresetChange}
              onSandboxSetup={composerActions.sandboxSetup}
              onSteerInput={composerActions.steerInput}
              onCancelInput={composerActions.cancelInput}
              onRequeueInput={composerActions.requeueInput}
              onDismissInput={composerActions.dismissInput}
              onApprovePlan={conversationActions.approvePlan}
              onRespondToApproval={conversationActions.respondToApproval}
              onCancelRun={conversationActions.cancelRun}
              onToggleSkill={skillsActions.toggleSkill}
              onToggleSkillEnabled={skillsActions.toggleSkillEnabled}
              onRefreshSkills={skillsActions.refreshSkills}
            />
          </div>
        </main>
        {isInspectorOpen && (
          <button
            className="panel-backdrop inspector-backdrop"
            type="button"
            aria-label="关闭工作区概览遮罩"
            onClick={() => setIsInspectorOpen(false)}
          />
        )}
        <InspectorSheet
          modal={inspectorModal}
          bridgeStatus={bridgeStatus}
          socketStatus={socketStatus}
          activeSession={activeSession}
          workingDirectory={displayedWorkingDirectory}
          messageCount={messages.length}
          permissionPreset={permissionPreset}
          runStatus={
            activeSessionId ? runStatusBySession[activeSessionId] : undefined
          }
          onRunBridgeCheck={connectionActions.runBridgeCheck}
          open={isInspectorOpen}
          onClose={() => setIsInspectorOpen(false)}
        />
      </div>
    </div>
  );
}

function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}
