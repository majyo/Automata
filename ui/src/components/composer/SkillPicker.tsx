import { Check, ChevronDown, RefreshCw, Wrench } from "lucide-react";
import { useState } from "react";
import type { SkillRecord, SkillRuntimeNotice } from "../../types/skills";

type SkillPickerProps = {
  skills: SkillRecord[];
  selectedIds: Set<string>;
  errors: string[];
  notices: SkillRuntimeNotice[];
  isLoading: boolean;
  disabled: boolean;
  onToggleSelected(skillId: string): void;
  onToggleEnabled(skill: SkillRecord): Promise<void>;
  onRefresh(): void;
};

export function SkillPicker({
  skills,
  selectedIds,
  errors,
  notices,
  isLoading,
  disabled,
  onToggleSelected,
  onToggleEnabled,
  onRefresh,
}: SkillPickerProps) {
  const [open, setOpen] = useState(false);
  const selectedCount = selectedIds.size;

  return (
    <div className="skill-picker">
      <button
        className={`skill-picker-trigger ${selectedCount ? "active" : ""}`}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Wrench size={13} />
        <span>{selectedCount ? `${selectedCount} skills` : "Skills"}</span>
        <ChevronDown size={12} />
      </button>

      {open ? (
        <div className="skill-picker-popover">
          <div className="skill-picker-heading">
            <div>
              <strong>Skills</strong>
              <span>Applied to this prompt only</span>
            </div>
            <button
              type="button"
              aria-label="Reload skills"
              title="Reload skills"
              disabled={disabled || isLoading}
              onClick={onRefresh}
            >
              <RefreshCw size={13} className={isLoading ? "spin" : ""} />
            </button>
          </div>

          <div className="skill-picker-list">
            {skills.length === 0 ? (
              <p className="skill-picker-empty">
                {isLoading ? "Loading skills..." : "No skills found for this workspace."}
              </p>
            ) : (
              skills.map((skill) => {
                const selected = selectedIds.has(skill.skill_id);
                return (
                  <div
                    className={`skill-picker-item ${skill.enabled ? "" : "disabled"}`}
                    key={skill.skill_id}
                  >
                    <button
                      className="skill-picker-select"
                      type="button"
                      disabled={disabled || !skill.enabled}
                      onClick={() => onToggleSelected(skill.skill_id)}
                    >
                      <span className={`skill-picker-check ${selected ? "selected" : ""}`}>
                        {selected ? <Check size={11} /> : null}
                      </span>
                      <span className="skill-picker-copy">
                        <strong>{skill.interface?.display_name || skill.name}</strong>
                        <span>{skill.short_description || skill.description}</span>
                        <small>{`${skill.scope} · ${skill.relative_dir}`}</small>
                      </span>
                    </button>
                    <button
                      className="skill-picker-enable"
                      type="button"
                      disabled={disabled}
                      onClick={() => void onToggleEnabled(skill)}
                    >
                      {skill.enabled ? "Disable" : "Enable"}
                    </button>
                    {skill.diagnostics.some(
                      (item) => !["available", "deferred"].includes(item.status),
                    ) ? (
                      <p className="skill-picker-diagnostic">
                        {skill.diagnostics
                          .filter((item) => !["available", "deferred"].includes(item.status))
                          .map((item) => item.message)
                          .join(" · ")}
                      </p>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>

          {[...errors, ...notices.map((notice) => notice.message)].length ? (
            <div className="skill-picker-notices">
              {[...errors, ...notices.map((notice) => notice.message)]
                .slice(-5)
                .map((message, index) => (
                  <p key={`${message}-${index}`}>{message}</p>
                ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
