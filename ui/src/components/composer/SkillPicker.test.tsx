import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SkillPicker } from "./SkillPicker";
import type { SkillRecord } from "../../types/skills";

function skill(skillId: string, relativeDir: string): SkillRecord {
  return {
    skill_id: skillId,
    name: "review",
    description: "Review the current change",
    path: `D:/repo/${relativeDir}/SKILL.md`,
    scope: "repo",
    enabled: true,
    root_id: "repo:.",
    relative_dir: relativeDir,
    fingerprint: `sha256:${skillId}`,
    diagnostics: [],
  };
}

describe("SkillPicker", () => {
  it("disambiguates duplicate names by path and selects the exact skill id", () => {
    const onToggleSelected = vi.fn();
    render(
      <SkillPicker
        skills={[skill("skill_a", "tools/review"), skill("skill_b", "ui/review")]}
        selectedIds={new Set()}
        errors={[]}
        notices={[]}
        isLoading={false}
        disabled={false}
        onToggleSelected={onToggleSelected}
        onToggleEnabled={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /skills/i }));
    expect(screen.getByText("repo · tools/review")).toBeInTheDocument();
    expect(screen.getByText("repo · ui/review")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Review the current change")[1]);
    expect(onToggleSelected).toHaveBeenCalledWith("skill_b");
  });
});
