import type { WorkspaceDirectory, WorkspaceText } from "../../types/workspace";

export type WorkspaceFilesView = {
  expandedDirectories: string[];
  directories: Record<string, WorkspaceDirectoryView>;
  openFiles: string[];
  selectedPath: string | null;
  preview: WorkspaceText | null;
  previewLoading: boolean;
  previewError: string;
};

export type WorkspaceDirectoryView = {
  directory: WorkspaceDirectory | null;
  loading: boolean;
  error: string;
};

export type WorkspaceFilesActions = {
  toggleDirectory(path: string): void;
  revealDirectory(path: string): void;
  openFile(path: string): void;
  selectFile(path: string | null): void;
  closeFile(path: string): void;
  reloadFile(path: string): void;
  refresh(): void;
};
