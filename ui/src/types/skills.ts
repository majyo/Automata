export type SkillScope = "repo" | "user" | "packaged" | "extra" | "plugin";

export type SkillInterface = {
  display_name?: string | null;
  short_description?: string | null;
  icon_small?: string | null;
  icon_large?: string | null;
  brand_color?: string | null;
  default_prompt?: string | null;
};

export type SkillToolDependency = {
  type: string;
  value?: string | null;
  description?: string | null;
  query?: string | null;
  server?: string | null;
  tool?: string | null;
  read_only?: boolean | null;
};

export type SkillDependencyDiagnostic = {
  dependency_type: string;
  status: "available" | "deferred" | "not_granted" | "not_found" | "unknown";
  message: string;
  value?: string | null;
  query?: string | null;
  server?: string | null;
  tool?: string | null;
};

export type SkillRecord = {
  skill_id: string;
  name: string;
  description: string;
  short_description?: string | null;
  path: string;
  scope: SkillScope;
  enabled: boolean;
  root_id: string;
  relative_dir: string;
  fingerprint: string;
  interface?: SkillInterface | null;
  dependencies?: { tools: SkillToolDependency[] } | null;
  diagnostics: SkillDependencyDiagnostic[];
};

export type SkillError = {
  path: string;
  message: string;
  severity: "warning" | "error";
};

export type SkillsListResponse = {
  workspace: string;
  skills: SkillRecord[];
  errors: SkillError[];
};

export type SkillSelection = {
  name: string;
  path: string;
};

export type SkillRuntimeNotice =
  | { type: "loaded"; message: string }
  | { type: "warning"; message: string }
  | { type: "injected"; message: string };
