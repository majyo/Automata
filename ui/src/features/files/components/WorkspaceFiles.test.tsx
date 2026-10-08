import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceFilesActions, WorkspaceFilesView } from "../model";
import { WorkspaceFiles } from "./WorkspaceFiles";
import { WorkspaceFileReader } from "./WorkspaceFileReader";
import { WorkspaceFileTabs } from "./WorkspaceFileTabs";

afterEach(cleanup);
const view: WorkspaceFilesView = {
  expandedDirectories: ["."], openFiles: ["readme.txt", "src/main.py"], selectedPath: "readme.txt",
  directories: {
    ".": { loading: false, error: "", directory: {
      workspace: "D:/repo", path: ".", truncated: false,
      entries: [
        { name: "src", path: "src", kind: "directory", size: null, modified_at: null, accessible: true, access_error: null },
        { name: "readme.txt", path: "readme.txt", kind: "file", size: 10, modified_at: null, accessible: true, access_error: null },
        { name: "external.txt", path: "external.txt", kind: "file", size: null, modified_at: null, accessible: false, access_error: "工作目录之外" },
      ],
    } },
    src: { loading: false, error: "", directory: {
      workspace: "D:/repo", path: "src", truncated: false,
      entries: [{ name: "main.py", path: "src/main.py", kind: "file", size: 8, modified_at: null, accessible: true, access_error: null }],
    } },
  },
  previewLoading: false, previewError: "",
  preview: { workspace: "D:/repo", path: "readme.txt", content: "<script>unsafe()</script>\n中文内容", size: 10, modified_at: "", encoding: "UTF-8", truncated: false },
};
function actions(): WorkspaceFilesActions {
  return {
    toggleDirectory: vi.fn(), revealDirectory: vi.fn(), openFile: vi.fn(), selectFile: vi.fn(),
    closeFile: vi.fn(), reloadFile: vi.fn(), refresh: vi.fn(),
  };
}

describe("workspace directory tree", () => {
  it("expands nested folders, disables inaccessible entries and filters files", () => {
    const handlers = actions();
    const { rerender } = render(<WorkspaceFiles workspace="D:/repo" view={view} actions={handlers} />);
    fireEvent.click(screen.getByRole("treeitem", { name: "目录 src" }));
    expect(handlers.toggleDirectory).toHaveBeenCalledWith("src");
    expect(screen.getByRole("treeitem", { name: "阅读文件 external.txt" })).toBeDisabled();
    expect(screen.queryByRole("treeitem", { name: "阅读文件 main.py" })).not.toBeInTheDocument();
    rerender(<WorkspaceFiles workspace="D:/repo" view={{ ...view, expandedDirectories: [".", "src"] }} actions={handlers} />);
    expect(screen.getByRole("treeitem", { name: "目录 src" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("treeitem", { name: "阅读文件 main.py" }));
    expect(handlers.openFile).toHaveBeenCalledWith("src/main.py");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "readme" } });
    expect(screen.queryByRole("treeitem", { name: "阅读文件 main.py" })).not.toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: "目录 src" })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: "阅读文件 readme.txt" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("region", { name: "文件内容" })).not.toBeInTheDocument();
  });

  it("supports keyboard tree navigation and collapse", () => {
    const handlers = actions();
    render(<WorkspaceFiles workspace="D:/repo" view={{ ...view, expandedDirectories: [".", "src"] }} actions={handlers} />);
    const root = screen.getByRole("treeitem", { name: "目录 repo" });
    root.focus();
    fireEvent.keyDown(root, { key: "ArrowDown" });
    const folder = screen.getByRole("treeitem", { name: "目录 src" });
    expect(folder).toHaveFocus();
    fireEvent.keyDown(folder, { key: "ArrowRight" });
    const file = screen.getByRole("treeitem", { name: "阅读文件 main.py" });
    expect(file).toHaveFocus();
    fireEvent.keyDown(file, { key: "ArrowLeft" });
    expect(folder).toHaveFocus();
    fireEvent.keyDown(folder, { key: "ArrowLeft" });
    expect(handlers.toggleDirectory).toHaveBeenCalledWith("src");
  });
});

describe("side-panel file reader", () => {
  it("renders literal source and reveals folders from breadcrumbs", () => {
    const reveal = vi.fn();
    const { container } = render(<WorkspaceFileReader workspace="D:/repo" view={view} actions={actions()} onRevealDirectory={reveal} />);
    expect(screen.getByText("<script>unsafe()</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "repo" }));
    expect(reveal).toHaveBeenCalledWith(".");
  });

  it("retains each tab's wrap setting and scroll position, including across the file picker", () => {
    const handlers = actions();
    const { rerender } = render(<WorkspaceFileReader workspace="D:/repo" view={view} actions={handlers} onRevealDirectory={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "自动换行" }));
    const original = screen.getByRole("region", { name: "文件内容" });
    original.scrollTop = 180;
    fireEvent.scroll(original);
    rerender(<WorkspaceFileReader workspace="D:/repo" view={{ ...view, selectedPath: "src/main.py", preview: { ...view.preview!, path: "src/main.py", content: "print(1)" } }} actions={handlers} onRevealDirectory={vi.fn()} />);
    expect(screen.getByRole("region", { name: "文件内容" })).not.toHaveClass("wrap");
    expect(screen.getByRole("region", { name: "文件内容" }).scrollTop).toBe(0);
    rerender(<WorkspaceFileReader workspace="D:/repo" view={{ ...view, selectedPath: null, preview: null }} actions={handlers} onRevealDirectory={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "文件内容" })).not.toBeInTheDocument();
    rerender(<WorkspaceFileReader workspace="D:/repo" view={view} actions={handlers} onRevealDirectory={vi.fn()} />);
    expect(screen.getByRole("region", { name: "文件内容" })).toHaveClass("wrap");
    expect(screen.getByRole("region", { name: "文件内容" }).scrollTop).toBe(180);
  });

  it("retries the current file after an error", () => {
    const handlers = actions();
    render(<WorkspaceFileReader workspace="D:/repo" view={{ ...view, preview: null, previewError: "二进制文件" }} actions={handlers} onRevealDirectory={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("二进制文件");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(handlers.reloadFile).toHaveBeenCalledWith("readme.txt");
    expect(handlers.refresh).not.toHaveBeenCalled();
  });

  it("restores the reading position after the whole file panel is folded", () => {
    const props = { workspace: "D:/repo", view, actions: actions(), onRevealDirectory: vi.fn() };
    const { rerender } = render(<WorkspaceFileReader {...props} />);
    const content = screen.getByRole("region", { name: "文件内容" });
    content.scrollTop = 220;
    fireEvent.scroll(content);
    rerender(<WorkspaceFileReader {...props} active={false} />);
    content.scrollTop = 0;
    fireEvent.scroll(content);
    rerender(<WorkspaceFileReader {...props} active />);
    expect(content.scrollTop).toBe(220);
  });
});

describe("top file tabs", () => {
  it("switches between the file picker and files and closes a named file", () => {
    const handlers = actions();
    render(<WorkspaceFileTabs view={view} actions={handlers} />);
    expect(screen.getByRole("tab", { name: "文件 readme.txt" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "打开文件" }));
    expect(handlers.selectFile).toHaveBeenCalledWith(null);
    fireEvent.click(screen.getByRole("tab", { name: "文件 src/main.py" }));
    expect(handlers.selectFile).toHaveBeenCalledWith("src/main.py");
    fireEvent.click(screen.getByRole("button", { name: "关闭文件 readme.txt" }));
    expect(handlers.closeFile).toHaveBeenCalledWith("readme.txt");
  });

  it("supports arrow keys and Delete without nesting close buttons inside tabs", () => {
    const handlers = actions();
    render(<WorkspaceFileTabs view={view} actions={handlers} />);
    const current = screen.getByRole("tab", { name: "文件 readme.txt" });
    current.focus();
    fireEvent.keyDown(current, { key: "ArrowRight" });
    const next = screen.getByRole("tab", { name: "文件 src/main.py" });
    expect(next).toHaveFocus();
    expect(handlers.selectFile).toHaveBeenCalledWith("src/main.py");
    fireEvent.keyDown(next, { key: "Delete" });
    expect(handlers.closeFile).toHaveBeenCalledWith("src/main.py");
    expect(current.querySelector("button")).toBeNull();
  });

  it("disambiguates files with matching names using their directory", () => {
    render(<WorkspaceFileTabs view={{ ...view, openFiles: ["src/main.py", "test/main.py"], selectedPath: "src/main.py" }} actions={actions()} />);
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("test")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "文件 src/main.py" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "文件 test/main.py" })).toBeInTheDocument();
  });
});
