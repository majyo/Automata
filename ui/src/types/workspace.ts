export type WorkspaceEntry = {
  name: string;
  path: string;
  kind: "directory" | "file";
  size: number | null;
  modified_at: string | null;
  accessible: boolean;
  access_error: string | null;
};

export type WorkspaceDirectory = {
  workspace: string;
  path: string;
  entries: WorkspaceEntry[];
  truncated: boolean;
};

export type WorkspaceText = {
  workspace: string;
  path: string;
  content: string;
  encoding: string;
  size: number;
  modified_at: string;
  truncated: boolean;
};
