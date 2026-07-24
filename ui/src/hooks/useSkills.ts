import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchSkills, setSkillEnabled } from "../api/skills";
import type { ApiRuntimeConfig } from "../types/api";
import type { SkillRecord, SkillRuntimeNotice, SkillSelection } from "../types/skills";
import type { SkillSocketPayload } from "../types/socket";

type UseSkillsOptions = {
  apiConfigRef: React.MutableRefObject<ApiRuntimeConfig>;
  workspace: string;
  sessionKey: string;
  enabled: boolean;
};

export function useSkills({
  apiConfigRef,
  workspace,
  sessionKey,
  enabled,
}: UseSkillsOptions) {
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [notices, setNotices] = useState<SkillRuntimeNotice[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(
    async (forceReload = false) => {
      if (!enabled || !workspace.trim()) {
        setSkills([]);
        return;
      }
      setIsLoading(true);
      try {
        const response = await fetchSkills(
          apiConfigRef.current,
          workspace,
          forceReload,
        );
        setSkills(response.skills);
        setErrors(response.errors.map((item) => item.message));
        setSelectedIds((current) => {
          const available = new Set(
            response.skills
              .filter((skill) => skill.enabled)
              .map((skill) => skill.skill_id),
          );
          return new Set([...current].filter((skillId) => available.has(skillId)));
        });
      } catch (error) {
        setSkills([]);
        setErrors([
          error instanceof Error ? error.message : "Could not load skills",
        ]);
      } finally {
        setIsLoading(false);
      }
    },
    [apiConfigRef, enabled, workspace],
  );

  useEffect(() => {
    setSelectedIds(new Set());
    setNotices([]);
    void refresh();
  }, [refresh, sessionKey]);

  const toggleSelected = useCallback((skillId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(skillId)) {
        next.delete(skillId);
      } else {
        next.add(skillId);
      }
      return next;
    });
  }, []);

  const toggleEnabled = useCallback(
    async (skill: SkillRecord) => {
      try {
        const updated = await setSkillEnabled(
          apiConfigRef.current,
          workspace,
          skill.skill_id,
          !skill.enabled,
        );
        setSkills((current) =>
          current.map((item) =>
            item.skill_id === updated.skill_id ? updated : item,
          ),
        );
        if (!updated.enabled) {
          setSelectedIds((current) => {
            const next = new Set(current);
            next.delete(updated.skill_id);
            return next;
          });
        }
      } catch (error) {
        setErrors((current) => [
          ...current.slice(-4),
          error instanceof Error
            ? error.message
            : "Could not update skill settings",
        ]);
      }
    },
    [apiConfigRef, workspace],
  );

  const handleRuntimeEvent = useCallback((payload: SkillSocketPayload) => {
    const notice: SkillRuntimeNotice =
      payload.type === "skills_loaded"
        ? {
            type: "loaded",
            message: `${payload.enabled_count} of ${payload.count} skills available`,
          }
        : payload.type === "skills_warning"
          ? { type: "warning", message: payload.message }
          : {
              type: "injected",
              message: `Injected ${payload.name}`,
            };
    setNotices((current) => [...current.slice(-4), notice]);
  }, []);

  const selectedSkills = useMemo<SkillSelection[]>(
    () =>
      skills
        .filter((skill) => skill.enabled && selectedIds.has(skill.skill_id))
        .map((skill) => ({ name: skill.name, path: skill.path })),
    [selectedIds, skills],
  );

  return {
    skills,
    errors,
    notices,
    selectedIds,
    selectedSkills,
    isLoading,
    refresh,
    toggleSelected,
    toggleEnabled,
    handleRuntimeEvent,
    clearSelection: () => setSelectedIds(new Set()),
  };
}
