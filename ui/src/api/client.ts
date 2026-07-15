import type { ApiRuntimeConfig } from "../types/api";

export async function requestJson<T>(config: ApiRuntimeConfig, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${config.httpBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.apiToken}`,
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}
