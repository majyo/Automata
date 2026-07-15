import { invoke } from "@tauri-apps/api/core";
import type { ApiRuntimeConfig } from "../types/api";

export const DEFAULT_API_CONFIG: ApiRuntimeConfig = {
  httpBaseUrl: "http://127.0.0.1:8765",
  wsChatUrl: "ws://127.0.0.1:8765/ws/chat",
  defaultWorkingDirectory: "",
  apiToken: "",
};

export async function loadApiConfig(): Promise<ApiRuntimeConfig> {
  try {
    const config = await invoke<ApiRuntimeConfig>("api_config");
    if (config.httpBaseUrl.trim() && config.wsChatUrl.trim() && config.apiToken.trim()) {
      return {
        ...config,
        defaultWorkingDirectory: config.defaultWorkingDirectory?.trim() ?? "",
      };
    }
  } catch {
    return DEFAULT_API_CONFIG;
  }

  return DEFAULT_API_CONFIG;
}
