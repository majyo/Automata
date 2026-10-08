import { StrictMode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchWorkspaceDirectory, fetchWorkspaceText } from "../../api/workspace";
import type { ApiRuntimeConfig } from "../../types/api";
import type { WorkspaceDirectory, WorkspaceText } from "../../types/workspace";
import { useWorkspaceFiles } from "./useWorkspaceFiles";

vi.mock("../../api/workspace", () => ({ fetchWorkspaceDirectory: vi.fn(), fetchWorkspaceText: vi.fn() }));
const config: ApiRuntimeConfig = {
  httpBaseUrl: "http://localhost", wsChatUrl: "ws://localhost",
  defaultWorkingDirectory: "D:/project", apiToken: "test",
};
const directory: WorkspaceDirectory = { workspace: "D:/project", path: ".", entries: [], truncated: false };
const text: WorkspaceText = {
  workspace: "D:/project", path: "readme.txt", content: "hello", encoding: "UTF-8",
  size: 5, modified_at: "2026-10-08T00:00:00Z", truncated: false,
};
const props = { config, workspace: "D:/project", sessionKey: "a", enabled: true };

describe("workspace file tabs and directory tree", () => {
  beforeEach(() => {
    vi.mocked(fetchWorkspaceDirectory).mockReset().mockImplementation(async (_, workspace, path) => ({ ...directory, workspace, path }));
    vi.mocked(fetchWorkspaceText).mockReset().mockImplementation(async (_, workspace, path) => ({ ...text, workspace, path, content: path }));
  });

  it("waits for configuration and a workspace", () => {
    const { rerender } = renderHook(useWorkspaceFiles, { initialProps: { ...props, enabled: false } });
    expect(fetchWorkspaceDirectory).not.toHaveBeenCalled();
    rerender({ ...props, workspace: "", enabled: true });
    expect(fetchWorkspaceDirectory).not.toHaveBeenCalled();
  });

  it("loads directories lazily and reuses their results after collapsing", async () => {
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    await waitFor(() => expect(result.current.view.directories["."].loading).toBe(false));
    expect(fetchWorkspaceDirectory).toHaveBeenCalledTimes(1);
    act(() => result.current.actions.toggleDirectory("src"));
    await waitFor(() => expect(result.current.view.directories.src.loading).toBe(false));
    act(() => result.current.actions.toggleDirectory("src"));
    expect(result.current.view.expandedDirectories).toEqual(["."]);
    act(() => result.current.actions.toggleDirectory("src"));
    expect(result.current.view.directories.src.directory?.path).toBe("src");
    expect(fetchWorkspaceDirectory).toHaveBeenCalledTimes(2);
  });

  it("opens unique tabs and returns to cached files or the file picker without re-fetching", async () => {
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("src/main.py"));
    await waitFor(() => expect(result.current.view.preview?.content).toBe("src/main.py"));
    expect(result.current.view.expandedDirectories).toEqual([".", "src"]);
    act(() => result.current.actions.openFile("readme.txt"));
    await waitFor(() => expect(result.current.view.preview?.content).toBe("readme.txt"));
    act(() => result.current.actions.selectFile(null));
    expect(result.current.view.preview).toBeNull();
    expect(result.current.view.openFiles).toEqual(["src/main.py", "readme.txt"]);
    act(() => result.current.actions.openFile("src/main.py"));
    expect(result.current.view.preview?.content).toBe("src/main.py");
    expect(result.current.view.openFiles).toEqual(["src/main.py", "readme.txt"]);
    expect(fetchWorkspaceText).toHaveBeenCalledTimes(2);
  });

  it("closes inactive tabs without switching and picks a neighbor when closing the active tab", async () => {
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => { result.current.actions.openFile("a.txt"); result.current.actions.openFile("b.txt"); result.current.actions.openFile("c.txt"); });
    await waitFor(() => expect(result.current.view.previewLoading).toBe(false));
    act(() => result.current.actions.closeFile("a.txt"));
    expect(result.current.view.selectedPath).toBe("c.txt");
    act(() => result.current.actions.closeFile("c.txt"));
    expect(result.current.view.selectedPath).toBe("b.txt");
    await waitFor(() => expect(result.current.view.preview?.path).toBe("b.txt"));
    act(() => result.current.actions.closeFile("b.txt"));
    expect(result.current.view.openFiles).toEqual([]);
    expect(result.current.view.selectedPath).toBeNull();
  });

  it("keeps a slow inactive response in its own tab", async () => {
    let resolveOld!: (value: WorkspaceText) => void;
    vi.mocked(fetchWorkspaceText).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("old.txt"));
    act(() => result.current.actions.openFile("new.txt"));
    await waitFor(() => expect(result.current.view.preview?.content).toBe("new.txt"));
    await act(async () => resolveOld({ ...text, path: "old.txt", content: "old content" }));
    expect(result.current.view.preview?.content).toBe("new.txt");
    act(() => result.current.actions.selectFile("old.txt"));
    expect(result.current.view.preview?.content).toBe("old content");
    expect(fetchWorkspaceText).toHaveBeenCalledTimes(2);
  });

  it("aborts closed tabs and cannot resurrect them with a late response", async () => {
    let resolveOld!: (value: WorkspaceText) => void;
    vi.mocked(fetchWorkspaceText).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("old.txt"));
    const signal = vi.mocked(fetchWorkspaceText).mock.calls[0][3];
    act(() => result.current.actions.closeFile("old.txt"));
    expect(signal?.aborted).toBe(true);
    await act(async () => resolveOld({ ...text, path: "old.txt", content: "stale" }));
    expect(result.current.view.openFiles).toEqual([]);
    act(() => result.current.actions.openFile("old.txt"));
    await waitFor(() => expect(result.current.view.preview?.content).toBe("old.txt"));
    expect(fetchWorkspaceText).toHaveBeenCalledTimes(2);
  });

  it("resets tabs and ignores old responses on session or workspace changes", async () => {
    let resolveOldDirectory!: (value: WorkspaceDirectory) => void;
    let resolveOldFile!: (value: WorkspaceText) => void;
    vi.mocked(fetchWorkspaceDirectory).mockImplementationOnce(() => new Promise((resolve) => { resolveOldDirectory = resolve; }));
    vi.mocked(fetchWorkspaceText).mockImplementationOnce(() => new Promise((resolve) => { resolveOldFile = resolve; }));
    const { result, rerender } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("src/old.txt"));
    rerender({ ...props, workspace: "D:/other", sessionKey: "b" });
    expect(result.current.view.openFiles).toEqual([]);
    expect(result.current.view.expandedDirectories).toEqual(["."]);
    expect(result.current.view.preview).toBeNull();
    await waitFor(() => expect(result.current.view.directories["."].loading).toBe(false));
    await act(async () => {
      resolveOldDirectory({ ...directory, path: "stale" });
      resolveOldFile({ ...text, content: "stale" });
    });
    expect(result.current.view.directories["."].directory?.workspace).toBe("D:/other");
    expect(result.current.view.preview).toBeNull();
    rerender(props);
    expect(result.current.view.openFiles).toEqual([]);
  });

  it("refreshes expanded directories and active text, and invalidates inactive text", async () => {
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("src/main.py"));
    await waitFor(() => expect(result.current.view.previewLoading).toBe(false));
    act(() => result.current.actions.openFile("readme.txt"));
    await waitFor(() => expect(result.current.view.preview?.path).toBe("readme.txt"));
    act(() => result.current.actions.refresh());
    await waitFor(() => expect(fetchWorkspaceText).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.view.directories.src.loading).toBe(false));
    expect(fetchWorkspaceDirectory).toHaveBeenCalledTimes(4);
    act(() => result.current.actions.selectFile("src/main.py"));
    await waitFor(() => expect(fetchWorkspaceText).toHaveBeenCalledTimes(4));
  });

  it("retries only the failed file without dropping other tabs", async () => {
    vi.mocked(fetchWorkspaceText).mockRejectedValueOnce(new Error("二进制文件"));
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props });
    act(() => result.current.actions.openFile("image.png"));
    await waitFor(() => expect(result.current.view.previewError).toBe("二进制文件"));
    act(() => result.current.actions.reloadFile("image.png"));
    await waitFor(() => expect(result.current.view.preview?.path).toBe("image.png"));
    expect(result.current.view.previewError).toBe("");
    expect(result.current.view.openFiles).toEqual(["image.png"]);
    expect(fetchWorkspaceDirectory).toHaveBeenCalledTimes(1);
  });

  it("finishes loading in StrictMode after the development cleanup aborts requests", async () => {
    const { result } = renderHook(useWorkspaceFiles, { initialProps: props, wrapper: StrictMode });
    await waitFor(() => expect(result.current.view.directories["."].loading).toBe(false));
    act(() => result.current.actions.openFile("readme.txt"));
    await waitFor(() => expect(result.current.view.preview?.path).toBe("readme.txt"));
  });
});
