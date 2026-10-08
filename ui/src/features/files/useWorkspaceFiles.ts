import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWorkspaceDirectory, fetchWorkspaceText } from "../../api/workspace";
import type { ApiRuntimeConfig } from "../../types/api";
import type { WorkspaceDirectory, WorkspaceText } from "../../types/workspace";
import type { WorkspaceDirectoryView, WorkspaceFilesActions, WorkspaceFilesView } from "./model";

type LoadState<T> = { key: string; value: T | null; loading: boolean; error: string };
type Navigation = {
  scope: string;
  expandedDirectories: string[];
  openFiles: string[];
  selectedPath: string | null;
};
const initialNavigation = (scope: string): Navigation => ({
  scope, expandedDirectories: ["."], openFiles: [], selectedPath: null,
});

export function useWorkspaceFiles({ config, workspace, sessionKey, enabled }: {
  config: ApiRuntimeConfig;
  workspace: string;
  sessionKey: string;
  enabled: boolean;
}): { view: WorkspaceFilesView; actions: WorkspaceFilesActions } {
  const scope = JSON.stringify([sessionKey, workspace]);
  const [navigation, setNavigation] = useState(() => initialNavigation(scope));
  // Hide the old workspace immediately, before effects and requests run.
  const current = navigation.scope === scope ? navigation : initialNavigation(scope);
  const { expandedDirectories, openFiles, selectedPath } = current;
  const [revision, setRevision] = useState(0);
  const [directories, setDirectories] = useState<Record<string, LoadState<WorkspaceDirectory>>>({});
  const [previews, setPreviews] = useState<Record<string, LoadState<WorkspaceText>>>({});
  const directoryRequests = useRef(new Map<string, AbortController>());
  const fileRequests = useRef(new Map<string, AbortController>());
  const canLoad = enabled && Boolean(workspace.trim());
  const cacheKey = useCallback((path: string) => JSON.stringify([scope, revision, path]), [scope, revision]);
  const abortRequests = useCallback(() => {
    for (const requests of [directoryRequests.current, fileRequests.current]) {
      for (const controller of requests.values()) controller.abort();
      requests.clear();
    }
  }, []);

  useEffect(() => setNavigation(initialNavigation(scope)), [scope]);
  useEffect(() => {
    setDirectories({});
    setPreviews({});
    return abortRequests;
  }, [scope, config, abortRequests]);
  useEffect(() => abortRequests, [canLoad, abortRequests]);

  useEffect(() => {
    if (!canLoad) return;
    for (const path of expandedDirectories) {
      const key = cacheKey(path);
      const cached = directories[path];
      if (directoryRequests.current.has(key) || (cached?.key === key && !cached.loading)) continue;
      const controller = new AbortController();
      directoryRequests.current.set(key, controller);
      setDirectories((previous) => ({ ...previous, [path]: { key, value: null, loading: true, error: "" } }));
      void fetchWorkspaceDirectory(config, workspace, path, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) {
            setDirectories((previous) => ({ ...previous, [path]: { key, value, loading: false, error: "" } }));
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setDirectories((previous) => ({ ...previous, [path]: { key, value: null, loading: false, error: errorMessage(error) } }));
          }
        })
        .finally(() => {
          if (directoryRequests.current.get(key) === controller) directoryRequests.current.delete(key);
        });
    }
  }, [canLoad, config, workspace, expandedDirectories, directories, cacheKey]);

  useEffect(() => {
    if (!canLoad || !selectedPath) return;
    const path = selectedPath;
    const key = cacheKey(path);
    const cached = previews[path];
    if (fileRequests.current.has(key) || (cached?.key === key && !cached.loading)) return;
    const controller = new AbortController();
    fileRequests.current.set(key, controller);
    setPreviews((previous) => ({ ...previous, [path]: { key, value: null, loading: true, error: "" } }));
    // Requests belong to open files, so changing tabs does not discard their result.
    void fetchWorkspaceText(config, workspace, path, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setPreviews((previous) => ({ ...previous, [path]: { key, value, loading: false, error: "" } }));
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPreviews((previous) => ({ ...previous, [path]: { key, value: null, loading: false, error: errorMessage(error) } }));
        }
      })
      .finally(() => {
        if (fileRequests.current.get(key) === controller) fileRequests.current.delete(key);
      });
  }, [canLoad, config, workspace, selectedPath, previews, cacheKey]);

  const updateNavigation = (update: (previous: Navigation) => Navigation) => {
    setNavigation((previous) => update(previous.scope === scope ? previous : initialNavigation(scope)));
  };
  const openFile = (path: string) => updateNavigation((previous) => ({
    ...previous,
    openFiles: previous.openFiles.includes(path) ? previous.openFiles : [...previous.openFiles, path],
    selectedPath: path,
    expandedDirectories: [...new Set([...previous.expandedDirectories, ...directoryAncestors(parentDirectory(path))])],
  }));
  const discardPreview = (path: string) => {
    const key = cacheKey(path);
    fileRequests.current.get(key)?.abort();
    fileRequests.current.delete(key);
    setPreviews((previous) => {
      const next = { ...previous };
      delete next[path];
      return next;
    });
  };
  const refresh = useCallback(() => {
    abortRequests();
    setRevision((value) => value + 1);
  }, [abortRequests]);
  const directoryViews: Record<string, WorkspaceDirectoryView> = {};
  for (const path of expandedDirectories) {
    const loaded = directories[path];
    const valid = canLoad && loaded?.key === cacheKey(path);
    directoryViews[path] = {
      directory: valid ? loaded.value : null,
      loading: canLoad && (!valid || loaded.loading),
      error: valid ? loaded.error : "",
    };
  }
  const preview = selectedPath ? previews[selectedPath] : undefined;
  const validPreview = Boolean(canLoad && selectedPath && preview?.key === cacheKey(selectedPath));

  return {
    view: {
      expandedDirectories, directories: directoryViews, openFiles, selectedPath,
      preview: validPreview ? preview!.value : null,
      previewLoading: Boolean(canLoad && selectedPath && (!validPreview || preview!.loading)),
      previewError: validPreview ? preview!.error : "",
    },
    actions: {
      toggleDirectory: (path) => updateNavigation((previous) => ({
        ...previous,
        expandedDirectories: previous.expandedDirectories.includes(path)
          ? previous.expandedDirectories.filter((directory) => directory !== path)
          : [...previous.expandedDirectories, path],
      })),
      revealDirectory: (path) => updateNavigation((previous) => ({
        ...previous,
        expandedDirectories: [...new Set([...previous.expandedDirectories, ...directoryAncestors(path)])],
      })),
      openFile,
      selectFile: (path) => path ? openFile(path) : updateNavigation((previous) => ({ ...previous, selectedPath: null })),
      closeFile: (path) => {
        discardPreview(path);
        updateNavigation((previous) => {
          const index = previous.openFiles.indexOf(path);
          const remaining = previous.openFiles.filter((file) => file !== path);
          return {
            ...previous, openFiles: remaining,
            selectedPath: previous.selectedPath === path ? remaining[Math.max(0, index - 1)] ?? null : previous.selectedPath,
          };
        });
      },
      reloadFile: discardPreview,
      refresh,
    },
  };
}

function directoryAncestors(path: string): string[] {
  const parts = path === "." ? [] : path.split("/");
  return [".", ...parts.map((_, index) => parts.slice(0, index + 1).join("/"))];
}
function parentDirectory(path: string): string {
  return path.split("/").slice(0, -1).join("/") || ".";
}
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "读取失败，请重试";
}
