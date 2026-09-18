import { describe, expect, it } from "vitest";

/**
 * Frontend layering rules.
 *
 * The plan treats `features/*` as independently ownable modules: a feature
 * may use `platform`, `contracts`, `state/chatTypes`, `types` and `shared`,
 * but must not reach into another feature's internals. Cross-feature
 * orchestration belongs to `app`, and the `hooks/` and `state/` composition
 * layers are allowed to wire features together.
 *
 * The sources are read through Vite's raw glob import, so this check adds no
 * Node type dependency and runs with the normal `npm test` command. A new
 * cross-feature import fails the suite instead of surfacing in a later
 * refactor.
 */

const MODULES = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

type Module = { path: string; source: string };

const modules: Module[] = Object.entries(MODULES).map(([key, source]) => ({
  path: key.replace(/^\.\.\//, ""),
  source,
}));

function featureOf(relativePath: string): string | null {
  const parts = relativePath.split("/");
  return parts[0] === "features" ? (parts[1] ?? null) : null;
}

function importsOf(source: string): string[] {
  const specifiers: string[] = [];
  const pattern = /from\s+"([^"]+)"/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    specifiers.push(match[1]);
  }
  return specifiers;
}

/** Resolve a relative import against the importing module's directory. */
function resolveRelative(fromPath: string, specifier: string): string {
  const base = fromPath.split("/").slice(0, -1);
  for (const part of specifier.split("/")) {
    if (part === "." || part === "") continue;
    if (part === "..") base.pop();
    else base.push(part);
  }
  return base.join("/");
}

/** Drop a trailing `.ts`/`.tsx` so extensionless imports match file paths. */
function withoutExtension(modulePath: string): string {
  return modulePath.replace(/\.tsx?$/, "");
}

describe("frontend layering", () => {
  it("finds feature modules to check", () => {
    const features = new Set(
      modules.map((module) => featureOf(module.path)).filter(Boolean),
    );

    expect(features.size).toBeGreaterThan(0);
  });

  it("a feature never imports another feature's files", () => {
    const offenders: string[] = [];

    for (const module of modules) {
      const owner = featureOf(module.path);
      // Only modules that live inside a feature are constrained here; the
      // composition layers above them exist to wire features together.
      if (owner === null) continue;
      for (const specifier of importsOf(module.source)) {
        if (!specifier.startsWith(".")) continue;
        const resolved = withoutExtension(
          resolveRelative(module.path, specifier),
        );
        const target = featureOf(resolved);
        if (target !== null && target !== owner) {
          offenders.push(`${module.path} imports ${specifier}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it("no feature imports the app layer", () => {
    const offenders: string[] = [];

    for (const module of modules) {
      if (featureOf(module.path) === null) continue;
      for (const specifier of importsOf(module.source)) {
        if (!specifier.startsWith(".")) continue;
        const resolved = withoutExtension(
          resolveRelative(module.path, specifier),
        );
        if (resolved.startsWith("app/") || resolved === "App") {
          offenders.push(`${module.path} imports ${specifier}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
