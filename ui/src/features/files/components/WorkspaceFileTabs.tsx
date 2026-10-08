import { useLayoutEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { FileText, FolderOpen, X } from "lucide-react";
import type { WorkspaceFilesActions, WorkspaceFilesView } from "../model";

export function WorkspaceFileTabs({ view, actions }: {
  view: WorkspaceFilesView;
  actions: WorkspaceFilesActions;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const revealActive = () => list.querySelector<HTMLElement>(".workspace-tab.selected")
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    revealActive();
    // Resizing or unfolding the directory changes the space available to tabs.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(revealActive);
    observer?.observe(list);
    window.addEventListener("resize", revealActive);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", revealActive);
    };
  }, [view.selectedPath]);

  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    const tab = (event.target as HTMLElement).closest<HTMLButtonElement>('[role="tab"]');
    if (!tab) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const index = tabs.indexOf(tab);
    if ((event.key === "Delete" || event.key === "Backspace") && index > 0) {
      event.preventDefault();
      closeFile(view.openFiles[index - 1]);
      return;
    }
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : -1;
    if (next < 0) return;
    event.preventDefault();
    tabs[next]?.focus();
    tabs[next]?.click();
  }

  function closeFile(path: string) {
    actions.closeFile(path);
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus();
    });
  }

  return (
    <div className="workspace-file-tabs" role="tablist" aria-label="文件标签页" ref={listRef} onKeyDown={handleKey}>
      <div className={`workspace-tab ${view.selectedPath === null ? "selected" : ""}`}>
        <button type="button" role="tab" id="workspace-open-file-tab" aria-controls="workspace-file-panel"
          aria-selected={view.selectedPath === null} tabIndex={view.selectedPath === null ? 0 : -1}
          aria-label="打开文件" onClick={() => actions.selectFile(null)}>
          <FolderOpen size={14} aria-hidden="true" />
          <span>打开文件</span>
        </button>
      </div>
      {view.openFiles.map((path, index) => {
        const name = fileName(path);
        const duplicateName = view.openFiles.some((other) => other !== path && fileName(other) === name);
        return (
          <div className={`workspace-tab ${view.selectedPath === path ? "selected" : ""}`} key={path}>
            <button type="button" role="tab" id={`workspace-file-tab-${index}`} aria-controls="workspace-file-panel"
              aria-selected={view.selectedPath === path} tabIndex={view.selectedPath === path ? 0 : -1}
              aria-label={`文件 ${path}`} title={path} onClick={() => actions.selectFile(path)}>
              <FileText size={14} aria-hidden="true" />
              <span>{name}{duplicateName && <small>{path.split("/").slice(0, -1).join("/") || "."}</small>}</span>
            </button>
            <button className="workspace-tab-close" type="button" aria-label={`关闭文件 ${path}`} title="关闭文件"
              tabIndex={view.selectedPath === path ? 0 : -1} onClick={() => closeFile(path)}>
              <X size={13} aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function fileName(path: string): string {
  return path.split("/").slice(-1)[0];
}
