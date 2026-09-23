import { Check, ChevronDown, RefreshCw, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { SkillRecord, SkillRuntimeNotice } from "../../../types/skills";

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
  const pickerRef = useRef<HTMLDivElement>(null);
  const selectedCount = selectedIds.size;

  useEffect(() => {
    if (!open) return;
    function closeOnOutsideClick(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !pickerRef.current?.contains(event.target)
      )
        setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        pickerRef.current
          ?.querySelector<HTMLButtonElement>(".skill-picker-trigger")
          ?.focus();
      }
    }
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="skill-picker" ref={pickerRef}>
      <button
        className={`skill-picker-trigger ${selectedCount ? "active" : ""}`}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Wrench size={13} />
        <span>{selectedCount ? `${selectedCount} 个技能` : "技能"}</span>
        <ChevronDown size={12} />
      </button>

      {open ? (
        <div className="skill-picker-popover">
          <div className="skill-picker-heading">
            <div>
              <strong>技能</strong>
              <span>仅对本条消息生效</span>
            </div>
            <button
              type="button"
              aria-label="重新加载技能"
              title="重新加载技能"
              disabled={disabled || isLoading}
              onClick={onRefresh}
            >
              <RefreshCw size={13} className={isLoading ? "spin" : ""} />
            </button>
          </div>

          <div className="skill-picker-list">
            {skills.length === 0 ? (
              <p className="skill-picker-empty">
                {isLoading
                  ? "正在加载技能…"
                  : "当前工作区没有可用的技能。"}
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
                      <span
                        className={`skill-picker-check ${selected ? "selected" : ""}`}
                      >
                        {selected ? <Check size={11} /> : null}
                      </span>
                      <span className="skill-picker-copy">
                        <strong>
                          {skill.interface?.display_name || skill.name}
                        </strong>
                        <span>
                          {skill.short_description || skill.description}
                        </span>
                        <small>{`${skill.scope} · ${skill.relative_dir}`}</small>
                      </span>
                    </button>
                    <button
                      className="skill-picker-enable"
                      type="button"
                      disabled={disabled}
                      onClick={() => void onToggleEnabled(skill)}
                    >
                      {skill.enabled ? "停用" : "启用"}
                    </button>
                    {skill.diagnostics.some(
                      (item) =>
                        !["available", "deferred"].includes(item.status),
                    ) ? (
                      <p className="skill-picker-diagnostic">
                        {skill.diagnostics
                          .filter(
                            (item) =>
                              !["available", "deferred"].includes(item.status),
                          )
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
