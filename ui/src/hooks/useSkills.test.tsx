import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSkills } from "./useSkills";
import { fetchSkills } from "../api/skills";
import type { ApiRuntimeConfig } from "../types/api";
import type { SkillRecord } from "../types/skills";

vi.mock("../api/skills", () => ({
  fetchSkills: vi.fn(),
  setSkillEnabled: vi.fn(),
}));

const config: ApiRuntimeConfig = {
  httpBaseUrl: "http://localhost",
  wsChatUrl: "ws://localhost",
  defaultWorkingDirectory: "D:/repo",
  apiToken: "test",
};

const record: SkillRecord = {
  skill_id: "skill_review",
  name: "review",
  description: "Review changes",
  path: "D:/repo/.automata/skills/review/SKILL.md",
  scope: "repo",
  enabled: true,
  root_id: "repo:.",
  relative_dir: "review",
  fingerprint: "sha256:test",
  diagnostics: [],
};

describe("useSkills", () => {
  beforeEach(() => {
    vi.mocked(fetchSkills).mockResolvedValue({
      workspace: "D:/repo",
      skills: [record],
      errors: [],
    });
  });

  it("clears turn selection when the session changes", async () => {
    const apiConfigRef = { current: config };
    const { result, rerender } = renderHook(
      ({ sessionKey }) =>
        useSkills({
          apiConfigRef,
          workspace: "D:/repo",
          sessionKey,
          enabled: true,
        }),
      { initialProps: { sessionKey: "session-a" } },
    );

    await waitFor(() => expect(result.current.skills).toHaveLength(1));
    act(() => result.current.toggleSelected("skill_review"));
    expect(result.current.selectedSkills).toHaveLength(1);

    rerender({ sessionKey: "session-b" });
    await waitFor(() => expect(result.current.selectedSkills).toHaveLength(0));
  });

  it("records durable skills warnings and injected events", async () => {
    const apiConfigRef = { current: config };
    const { result } = renderHook(() =>
      useSkills({
        apiConfigRef,
        workspace: "D:/repo",
        sessionKey: "session-a",
        enabled: true,
      }),
    );
    await waitFor(() => expect(result.current.skills).toHaveLength(1));

    act(() => {
      result.current.handleRuntimeEvent({
        type: "skills_warning",
        session_id: "session-a",
        run_id: "run-a",
        seq: 3,
        schema_version: 1,
        message: "metadata warning",
      });
      result.current.handleRuntimeEvent({
        type: "skill_injected",
        session_id: "session-a",
        run_id: "run-a",
        seq: 4,
        schema_version: 1,
        name: "review",
        path: record.path,
      });
    });

    expect(result.current.notices.map((item) => item.message)).toEqual([
      "metadata warning",
      "Injected review",
    ]);
  });
});
