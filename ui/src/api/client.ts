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
    let message = `Request failed: ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (typeof body.detail?.message === "string") {
        message = body.detail.message;
      }
    } catch {
      // A non-JSON error still has a useful status code.
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}
