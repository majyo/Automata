import { requestJson } from "./client";
import type { ApiRuntimeConfig } from "../types/api";
import type { SkillRecord, SkillsListResponse } from "../types/skills";

export async function fetchSkills(
  config: ApiRuntimeConfig,
  workspace: string,
  forceReload = false,
): Promise<SkillsListResponse> {
  const query = new URLSearchParams({
    workspace,
    force_reload: String(forceReload),
  });
  return requestJson<SkillsListResponse>(config, `/skills?${query.toString()}`);
}

export async function setSkillEnabled(
  config: ApiRuntimeConfig,
  workspace: string,
  skillId: string,
  enabled: boolean,
): Promise<SkillRecord> {
  return requestJson<SkillRecord>(
    config,
    `/skills/${encodeURIComponent(skillId)}/enabled`,
    {
      method: "PUT",
      body: JSON.stringify({ workspace, enabled }),
    },
  );
}
