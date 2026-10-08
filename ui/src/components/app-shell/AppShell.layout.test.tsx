import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

const originalWidth = window.innerWidth;
beforeEach(() => {
  window.innerWidth = 1440;
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.innerWidth = originalWidth;
});

function shellProps(): ComponentProps<typeof AppShell> {
  return {
    sessionView: {
      sessions: [], activeSession: null, activeSessionId: "session-a", isNewSessionDraft: false,
      displayedWorkingDirectory: "D:/repo", editingSessionId: null, editingTitle: "",
      activeRunIdBySession: {}, runStatusBySession: {},
    },
    sessionActions: {
      createSession: vi.fn(), selectSession: vi.fn(), startRename: vi.fn(), setEditingTitle: vi.fn(),
      commitRename: vi.fn(), cancelRename: vi.fn(), deleteSession: vi.fn(),
    },
    connectionView: { bridgeStatus: "Connected", socketStatus: "Ready" },
    connectionActions: { runBridgeCheck: vi.fn() },
    conversationView: {
      messages: [], messagesRef: { current: null }, approvals: [], isStreaming: false,
    },
    conversationActions: { approvePlan: vi.fn(), respondToApproval: vi.fn(), cancelRun: vi.fn() },
    composerView: {
      prompt: "保留会话草稿", sendMode: "execute", canSend: true, defaultWorkingDirectory: "D:/repo",
      permissionPreset: "default", permissionUpdating: false, sandboxSetupStatus: "", pendingInputs: [],
    },
    composerActions: {
      chooseDirectory: vi.fn(), workingDirectoryChange: vi.fn(), submit: vi.fn(), promptChange: vi.fn(),
      sendModeChange: vi.fn(), permissionPresetChange: vi.fn(), sandboxSetup: vi.fn(),
      steerInput: vi.fn(), cancelInput: vi.fn(), requeueInput: vi.fn(), dismissInput: vi.fn(),
    },
    skillsView: {
      skills: [], selectedSkillIds: new Set(), skillErrors: [], skillNotices: [], skillsLoading: false,
    },
    skillsActions: { toggleSkill: vi.fn(), toggleSkillEnabled: vi.fn().mockResolvedValue(undefined), refreshSkills: vi.fn() },
    filesView: {
      expandedDirectories: ["."], openFiles: ["main.py"], selectedPath: "main.py",
      directories: { ".": { loading: false, error: "", directory: {
        workspace: "D:/repo", path: ".", truncated: false,
        entries: [{ name: "main.py", path: "main.py", kind: "file", size: 8, modified_at: null, accessible: true, access_error: null }],
      } } },
      preview: { workspace: "D:/repo", path: "main.py", content: "print(1)", size: 8, encoding: "UTF-8", modified_at: "", truncated: false },
      previewLoading: false, previewError: "",
    },
    filesActions: {
      toggleDirectory: vi.fn(), revealDirectory: vi.fn(), openFile: vi.fn(), selectFile: vi.fn(),
      closeFile: vi.fn(), reloadFile: vi.fn(), refresh: vi.fn(),
    },
  };
}

describe("independent file work panel", () => {
  it("keeps file tabs and the reader outside the live conversation", () => {
    render(<AppShell {...shellProps()} />);
    const chat = screen.getByRole("main", { name: "对话区域" });
    const panel = screen.getByRole("complementary", { name: "文件工作面板" });
    expect(within(chat).getByRole("region", { name: "编程助手会话" })).toBeVisible();
    expect(within(chat).getByRole("textbox", { name: "输入任务消息" })).toHaveValue("保留会话草稿");
    expect(within(chat).queryByRole("tablist")).not.toBeInTheDocument();
    expect(within(chat).queryByRole("region", { name: "文件内容" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("tablist", { name: "文件标签页" })).toBeInTheDocument();
    expect(within(panel).getByRole("region", { name: "文件内容" })).toHaveTextContent("print(1)");
  });

  it("folds the inner directory and whole panel independently without remounting chat", async () => {
    render(<AppShell {...shellProps()} />);
    const originalInput = screen.getByRole("textbox", { name: "输入任务消息" });
    fireEvent.click(screen.getByRole("button", { name: "收起文件目录" }));
    expect(screen.queryByRole("tree", { name: "项目文件目录" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "文件内容" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "文件工作面板" })).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "关闭文件面板" });
    close.focus();
    fireEvent.click(close);
    expect(screen.queryByRole("complementary", { name: "文件工作面板" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "输入任务消息" })).toBe(originalInput);
    await waitFor(() => expect(screen.getByRole("button", { name: "展开文件面板" })).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: "展开文件面板" }));
    expect(screen.getByRole("region", { name: "文件内容" })).toHaveTextContent("print(1)");
    expect(screen.queryByRole("tree", { name: "项目文件目录" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开文件目录" }));
    expect(screen.getByRole("tree", { name: "项目文件目录" })).toBeInTheDocument();
  });

  it("opens a file on mobile by folding only the directory, leaving the work panel open", () => {
    window.innerWidth = 390;
    const props = shellProps();
    render(<AppShell {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "展开文件面板" }));
    fireEvent.click(screen.getByRole("treeitem", { name: "阅读文件 main.py" }));
    expect(props.filesActions.openFile).toHaveBeenCalledWith("main.py");
    expect(screen.getByRole("dialog", { name: "文件工作面板" })).toBeInTheDocument();
    expect(screen.queryByRole("tree", { name: "项目文件目录" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "文件内容" })).toHaveTextContent("print(1)");
    expect(screen.getByRole("button", { name: "展开文件目录" })).toHaveFocus();
  });
});
