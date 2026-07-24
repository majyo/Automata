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
  const locked = !isNewSessionDraft || isStreaming;

  return (
    <section className={`workspace-picker ${isNewSessionDraft ? "" : "locked"}`} aria-label="Working directory">
      <span className="field-label">Working directory</span>
      <div className="text-field">
        <input
          value={displayedWorkingDirectory}
          onChange={(event) => onWorkingDirectoryChange(event.currentTarget.value)}
          disabled={locked}
          title={displayedWorkingDirectory}
          placeholder={defaultWorkingDirectory || WORKING_DIRECTORY_PLACEHOLDER}
        />
        <button
          className="icon-button"
          type="button"
          onClick={onChooseDirectory}
          disabled={locked}
          aria-label="Choose working directory"
          title="Choose working directory"
        >
          <FolderOpen size={16} />
        </button>
      </div>
      <span className="field-helper">
        {locked ? "The working directory is fixed for this session." : "Pick a folder or type a path for the new session."}
      </span>
    </section>
  );
}
