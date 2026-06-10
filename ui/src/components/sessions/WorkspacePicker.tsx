import { FolderOpen } from "lucide-react";
import { WORKING_DIRECTORY_PLACEHOLDER } from "../../utils/format";

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
  return (
    <section className={`workspace-picker ${isNewSessionDraft ? "" : "locked"}`} aria-label="Working directory">
      <div className="workspace-picker-header">
        <span>Working directory</span>
        <button
          className="icon-button small"
          type="button"
          onClick={onChooseDirectory}
          disabled={!isNewSessionDraft || isStreaming}
          aria-label="Choose working directory"
        >
          <FolderOpen size={16} />
        </button>
      </div>
      <input
        value={displayedWorkingDirectory}
        onChange={(event) => onWorkingDirectoryChange(event.currentTarget.value)}
        disabled={!isNewSessionDraft || isStreaming}
        title={displayedWorkingDirectory}
        placeholder={defaultWorkingDirectory || WORKING_DIRECTORY_PLACEHOLDER}
      />
    </section>
  );
}
