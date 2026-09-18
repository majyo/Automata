import { FolderOpen } from "lucide-react";

type WorkspacePickerProps = {
  displayedWorkingDirectory: string;
  defaultWorkingDirectory: string;
  isNewSessionDraft: boolean;
  isStreaming: boolean;
  onChooseDirectory(): void;
  onWorkingDirectoryChange(workingDirectory: string): void;
};

export function WorkspacePicker({
  displayedWorkingDirectory,
  defaultWorkingDirectory,
  isNewSessionDraft,
  isStreaming,
  onChooseDirectory,
  onWorkingDirectoryChange,
}: WorkspacePickerProps) {
  const locked = !isNewSessionDraft || isStreaming;

  return (
    <section
      className={`workspace-picker ${isNewSessionDraft ? "" : "locked"}`}
      aria-label="工作目录"
    >
      <label className="field-label" htmlFor="working-directory">
        工作目录
      </label>
      <div className="text-field">
        <FolderOpen size={16} />
        <input
          id="working-directory"
          value={displayedWorkingDirectory}
          onChange={(event) =>
            onWorkingDirectoryChange(event.currentTarget.value)
          }
          disabled={locked}
          title={displayedWorkingDirectory}
          placeholder={
            defaultWorkingDirectory || "输入项目路径（留空使用默认目录）"
          }
        />
        <button
          className="icon-button"
          type="button"
          onClick={onChooseDirectory}
          disabled={locked}
          aria-label="选择文件夹"
          title="选择文件夹"
        >
          <span>浏览</span>
        </button>
      </div>
      <span className="field-helper">
        {locked
          ? "本次会话使用固定的工作目录。"
          : "选择本地文件夹，或直接输入完整路径。"}
      </span>
    </section>
  );
}
