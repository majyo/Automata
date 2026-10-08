import { useEffect, useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen, RefreshCw, Search } from "lucide-react";
import type { WorkspaceEntry } from "../../../types/workspace";
import type { WorkspaceFilesActions, WorkspaceFilesView } from "../model";
import { formatDirectoryName } from "../../../utils/format";

type WorkspaceFilesProps = { workspace: string; view: WorkspaceFilesView; actions: WorkspaceFilesActions };

export function WorkspaceFiles({ workspace, view, actions }: WorkspaceFilesProps) {
  const [filter, setFilter] = useState("");
  const [focusedPath, setFocusedPath] = useState(".");
  useEffect(() => { setFilter(""); setFocusedPath("."); }, [workspace]);
  const query = filter.trim().toLocaleLowerCase();
  const expanded = (path: string) => view.expandedDirectories.includes(path);
  // Keep directories available during filtering so unopened folders can still be explored.
  const entriesFor = (path: string) => view.directories[path]?.directory?.entries.filter((entry) =>
    entry.kind === "directory" || entry.path.toLocaleLowerCase().includes(query),
  ) ?? [];
  const visiblePaths: string[] = ["."];
  function collectPaths(path: string) {
    if (!expanded(path)) return;
    for (const entry of entriesFor(path)) {
      if (entry.accessible) visiblePaths.push(entry.path);
      if (entry.kind === "directory" && entry.accessible) collectPaths(entry.path);
    }
  }
  collectPaths(".");
  const tabStop = visiblePaths.includes(focusedPath) ? focusedPath : ".";

  function handleTreeKey(event: KeyboardEvent<HTMLUListElement>) {
    const item = (event.target as HTMLElement).closest<HTMLButtonElement>('[role="treeitem"]');
    if (!item) return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="treeitem"]:not(:disabled)'));
    const index = items.indexOf(item);
    const path = item.dataset.path!;
    const directory = item.dataset.kind === "directory";
    let next: HTMLButtonElement | undefined;
    if (event.key === "ArrowDown") next = items[Math.min(index + 1, items.length - 1)];
    else if (event.key === "ArrowUp") next = items[Math.max(index - 1, 0)];
    else if (event.key === "Home") next = items[0];
    else if (event.key === "End") next = items[items.length - 1];
    else if (event.key === "ArrowRight" && directory) {
      if (!expanded(path)) actions.toggleDirectory(path);
      else if (items[index + 1]?.dataset.parent === path) next = items[index + 1];
    } else if (event.key === "ArrowLeft") {
      if (directory && expanded(path)) actions.toggleDirectory(path);
      else next = items.find((candidate) => candidate.dataset.path === item.dataset.parent);
    } else return;
    event.preventDefault();
    next?.focus();
  }

  function renderChildren(path: string, level: number) {
    if (!expanded(path)) return null;
    const state = view.directories[path];
    const entries = entriesFor(path);
    return (
      <ul role="group" id={groupId(path)}>
        {state?.loading ? <li role="none" className="file-tree-state" style={indent(level)}><span role="status">正在读取目录…</span></li>
          : state?.error ? <li role="none" className="file-tree-state file-error" style={indent(level)}>
            <p role="alert">{state.error}</p><button className="file-retry" type="button" onClick={actions.refresh}>重试</button>
          </li>
          : entries.length === 0 ? <li role="none" className="file-tree-state" style={indent(level)}>{query ? "没有匹配的文件" : "此目录为空"}</li>
          : entries.map((entry) => renderEntry(entry, path, level))}
        {state?.directory?.truncated && <li role="none" className="file-notice">此目录仅显示前 2000 项。</li>}
      </ul>
    );
  }

  function renderEntry(entry: WorkspaceEntry, parent: string, level: number) {
    const directory = entry.kind === "directory";
    const isExpanded = directory && expanded(entry.path);
    return (
      <li role="none" key={entry.path}>
        <button type="button" role="treeitem" className={`file-entry ${entry.path === view.selectedPath ? "selected" : ""}`}
          style={indent(level)} data-path={entry.path} data-parent={parent} data-kind={entry.kind}
          title={entry.access_error || entry.path} disabled={!entry.accessible}
          aria-label={`${directory ? "目录" : "阅读文件"} ${entry.name}`} aria-level={level}
          aria-expanded={directory ? isExpanded : undefined} aria-selected={!directory ? entry.path === view.selectedPath : undefined}
          aria-owns={isExpanded ? groupId(entry.path) : undefined} tabIndex={tabStop === entry.path ? 0 : -1}
          onFocus={() => setFocusedPath(entry.path)}
          onClick={() => directory ? actions.toggleDirectory(entry.path) : actions.openFile(entry.path)}>
          {directory ? (isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : <span className="file-tree-spacer" />}
          {directory ? (isExpanded ? <FolderOpen size={14} /> : <Folder size={14} />) : <FileText size={14} />}
          <span className="file-entry-name">{entry.name}</span>
        </button>
        {directory && entry.accessible && renderChildren(entry.path, level + 1)}
      </li>
    );
  }

  return (
    <div className="workspace-files">
      <div className="file-directory-toolbar">
        <span className="file-directory-label">目录</span>
        <button className="icon-button small" type="button" title="刷新文件" aria-label="刷新文件"
          disabled={!workspace} onClick={actions.refresh}><RefreshCw size={14} /></button>
      </div>
      <label className="file-filter">
        <Search size={14} aria-hidden="true" />
        <input type="search" aria-label="筛选已展开目录中的文件" placeholder="筛选文件…"
          title="筛选已展开目录中的文件" value={filter} onChange={(event) => setFilter(event.target.value)} disabled={!workspace} />
      </label>
      <div className="file-directory-list">
        {!workspace ? <p className="file-state">选择工作目录后，可在这里浏览项目文件。</p> : (
          <ul role="tree" aria-label="项目文件目录" onKeyDown={handleTreeKey}>
            <li role="none">
              <button type="button" role="treeitem" className="file-entry file-tree-root" title={workspace}
                aria-label={`目录 ${formatDirectoryName(workspace)}`} aria-level={1} aria-expanded={expanded(".")}
                aria-owns={expanded(".") ? groupId(".") : undefined} data-path="." data-kind="directory"
                tabIndex={tabStop === "." ? 0 : -1} onFocus={() => setFocusedPath(".")} onClick={() => actions.toggleDirectory(".")}>
                {expanded(".") ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <FolderOpen size={14} /><span className="file-entry-name">{formatDirectoryName(workspace)}</span>
              </button>
              {renderChildren(".", 2)}
            </li>
          </ul>
        )}
      </div>
    </div>
  );
}

function indent(level: number): CSSProperties {
  return { paddingLeft: 6 + (level - 1) * 14 };
}
function groupId(path: string): string {
  return `workspace-tree-group-${encodeURIComponent(path)}`;
}
