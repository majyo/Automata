export type PermissionPreset = "default" | "full_access";

export type SessionSummary = {
  id: string;
  title: string;
  working_directory: string;
  backend: string;
  permission_preset: PermissionPreset;
  created_at: string;
  updated_at: string;
  message_count: number;
};
