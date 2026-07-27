import type { ApiRuntimeConfig } from "../types/api";
import { requestJson } from "./client";

export type SandboxSetupResult = {
  ready: boolean;
  backend: string;
  profile_hash: string;
  profile_version: number;
  elevated: boolean;
};

export async function setupSandbox(
  config: ApiRuntimeConfig,
  workspace: string,
): Promise<SandboxSetupResult> {
  return requestJson<SandboxSetupResult>(config, "/sandbox/setup", {
    method: "POST",
    body: JSON.stringify({ workspace }),
  });
}
