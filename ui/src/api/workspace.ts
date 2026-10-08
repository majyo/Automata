import { requestJson } from "./client";
import type { ApiRuntimeConfig } from "../types/api";
import type { WorkspaceDirectory, WorkspaceText } from "../types/workspace";

export function fetchWorkspaceDirectory(
  config: ApiRuntimeConfig,
  workspace: string,
  path: string,
  signal: AbortSignal,
): Promise<WorkspaceDirectory> {
  const query = new URLSearchParams({ workspace, path });
  return requestJson(config, `/workspace/directory?${query}`, { signal });
}

export function fetchWorkspaceText(
  config: ApiRuntimeConfig,
  workspace: string,
  path: string,
  signal: AbortSignal,
): Promise<WorkspaceText> {
  const query = new URLSearchParams({ workspace, path });
  return requestJson(config, `/workspace/text?${query}`, { signal });
}
