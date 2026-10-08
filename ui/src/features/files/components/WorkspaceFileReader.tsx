import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, FolderOpen, RefreshCw, WrapText } from "lucide-react";
import type { WorkspaceFilesActions, WorkspaceFilesView } from "../model";
import { formatDirectoryName } from "../../../utils/format";

export function WorkspaceFileReader({ workspace, view, actions, onRevealDirectory, active = true }: {
  workspace: string;
  view: WorkspaceFilesView;
  actions: WorkspaceFilesActions;
  onRevealDirectory(path: string): void;
  active?: boolean;
}) {
  const { selectedPath, preview, previewLoading, previewError } = view;
  const [wrapping, setWrapping] = useState<Record<string, boolean>>({});
  const scrollPositions = useRef(new Map<string, { top: number; left: number }>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const wrap = selectedPath ? wrapping[selectedPath] ?? false : false;
  const parts = selectedPath?.split("/") ?? [];
  const lines = useMemo(() => preview?.content.split("\n") ?? [], [preview]);

  useEffect(() => {
    for (const path of scrollPositions.current.keys()) {
      if (!view.openFiles.includes(path)) scrollPositions.current.delete(path);
    }
    setWrapping((previous) => {
      const next = Object.fromEntries(Object.entries(previous).filter(([path]) => view.openFiles.includes(path)));
      return Object.keys(next).length === Object.keys(previous).length ? previous : next;
    });
  }, [view.openFiles]);
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!active || !element || !selectedPath) return;
    const position = scrollPositions.current.get(selectedPath);
    element.scrollTop = position?.top ?? 0;
    element.scrollLeft = position?.left ?? 0;
  }, [selectedPath, preview, active]);

  if (!selectedPath) return (
    <section className="workspace-file-reader" aria-label="文本阅读">
      <div className="file-reader-toolbar">
        <nav className="file-breadcrumbs" aria-label="文件路径">
          <button type="button" title={workspace} onClick={() => onRevealDirectory(".")} disabled={!workspace}>
            <FolderOpen size={14} aria-hidden="true" /><span>{workspace ? formatDirectoryName(workspace) : "工作目录"}</span>
          </button>
        </nav>
      </div>
      <div className="file-reader-empty">
        <FolderOpen size={28} aria-hidden="true" />
        <h3>打开文件</h3>
        <p>{workspace ? "从项目目录中选择文件，在这里阅读。" : "选择工作目录后，可浏览项目文件。"}</p>
        <button className="button button-outlined" type="button" onClick={() => onRevealDirectory(".")} disabled={!workspace}>浏览项目文件</button>
      </div>
    </section>
  );
  return (
    <section className="workspace-file-reader" aria-label="文本阅读">
      <div className="file-reader-toolbar">
        <nav className="file-breadcrumbs" aria-label="文件路径">
          <button type="button" title={workspace} onClick={() => onRevealDirectory(".")}>
            <FolderOpen size={14} aria-hidden="true" /><span>{formatDirectoryName(workspace)}</span>
          </button>
          {parts.map((part, index) => (
            <span className="file-breadcrumb-part" key={index}>
              <ChevronRight size={12} aria-hidden="true" />
              {index === parts.length - 1 ? <strong>{part}</strong> : (
                <button type="button" onClick={() => onRevealDirectory(parts.slice(0, index + 1).join("/"))}>{part}</button>
              )}
            </span>
          ))}
        </nav>
        <div className="file-reader-actions">
          <span className="file-readonly">只读</span>
          <button className={`icon-button small ${wrap ? "active" : ""}`} type="button" title="自动换行"
            aria-label="自动换行" aria-pressed={wrap}
            onClick={() => setWrapping((previous) => ({ ...previous, [selectedPath]: !wrap }))}>
            <WrapText size={16} />
          </button>
          <button className="icon-button small" type="button" title="重新读取文件" aria-label="重新读取文件"
            disabled={previewLoading} onClick={() => actions.reloadFile(selectedPath)}><RefreshCw size={14} /></button>
        </div>
      </div>
      <div className="file-reader-body">
        {preview?.truncated && <p className="file-notice">文件较大，仅预览开头部分（最多 256 KiB / 5000 行）。</p>}
        {previewLoading ? (
          <p className="file-state" role="status">正在读取文件…</p>
        ) : previewError ? (
          <div className="file-state file-error" role="alert">
            <p>{previewError}</p>
            <button className="file-retry" type="button" onClick={() => actions.reloadFile(selectedPath)}>重试</button>
          </div>
        ) : preview?.content === "" ? (
          <p className="file-state">此文件为空</p>
        ) : preview ? (
          <div className={`file-text-scroll ${wrap ? "wrap" : ""}`} role="region" aria-label="文件内容" tabIndex={0}
            ref={scrollRef} key={selectedPath} onScroll={(event) => {
              if (!active) return;
              scrollPositions.current.set(selectedPath, { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft });
            }}>
            <div className="file-text-lines">
              {lines.map((line, index) => (
                <div className="file-text-line" key={index}>
                  <span className="file-line-number" aria-hidden="true">{index + 1}</span>
                  <span className="file-line-content">{line}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      <footer className="file-reader-status">
        <span>{preview ? `${lines.length} 行` : "文本文件"}</span>
        <span>{preview ? `${preview.encoding} · ${formatSize(preview.size)}` : selectedPath}</span>
      </footer>
    </section>
  );
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}
