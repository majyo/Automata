from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from .model import (
    SkillDependencies,
    SkillError,
    SkillInterface,
    SkillLoadOutcome,
    SkillMetadata,
    SkillPolicy,
    SkillRoot,
    SkillToolDependency,
)

SKILL_FILENAME = "SKILL.md"
MAX_SCAN_DEPTH = 6
MAX_SKILL_DIRS_PER_ROOT = 2_000
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1_024
MAX_DEFAULT_PROMPT_LEN = 1_024
MAX_BODY_CHARS = 65_536
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SkillParseError(ValueError):
    pass


def load_skills_from_roots(
    roots: tuple[SkillRoot, ...],
    *,
    body_budget_chars: int = MAX_BODY_CHARS,
) -> SkillLoadOutcome:
    skills: list[SkillMetadata] = []
    errors: list[SkillError] = []
    seen_paths: set[Path] = set()

    for root in roots:
        for path in discover_skill_files(root.path):
            resolved_path = canonicalize(path)
            if resolved_path in seen_paths:
                continue
            seen_paths.add(resolved_path)
            try:
                metadata_warnings: list[SkillError] = []
                skill = parse_skill_file(
                    resolved_path,
                    scope=root.scope,
                    body_budget_chars=body_budget_chars,
                    metadata_warnings=metadata_warnings,
                )
                skills.append(with_skill_identity(skill, root))
                errors.extend(metadata_warnings)
            except SkillParseError as error:
                errors.append(SkillError(path=resolved_path, message=str(error)))

    skills.sort(key=lambda skill: (scope_rank(skill.scope), skill.name, str(skill.path)))
    return SkillLoadOutcome(skills=tuple(skills), errors=tuple(errors))


def discover_skill_files(root: Path) -> tuple[Path, ...]:
    root = canonicalize(root)
    if not root.is_dir():
        return ()

    discovered: list[Path] = []
    visited: set[Path] = {root}
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue and len(visited) <= MAX_SKILL_DIRS_PER_ROOT:
        directory, depth = queue.popleft()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_file() and entry.name == SKILL_FILENAME:
                discovered.append(entry)
                continue
            if depth >= MAX_SCAN_DEPTH:
                continue
            if entry.is_dir():
                resolved = canonicalize(entry)
                if resolved in visited:
                    continue
                visited.add(resolved)
                queue.append((resolved, depth + 1))

    return tuple(discovered)


def parse_skill_file(
    path: Path,
    *,
    scope: str,
    body_budget_chars: int = MAX_BODY_CHARS,
    metadata_warnings: list[SkillError] | None = None,
) -> SkillMetadata:
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SkillParseError(f"failed to read file: {error}") from error
    if len(contents) > body_budget_chars:
        raise SkillParseError(
            f"skill body exceeds maximum length of {body_budget_chars} characters"
        )

    frontmatter = extract_frontmatter(contents)
    if frontmatter is None:
        raise SkillParseError("missing YAML frontmatter delimited by ---")
    try:
        parsed = parse_simple_yaml(frontmatter)
    except SkillParseError:
        raise
    except Exception as error:
        raise SkillParseError(f"invalid YAML: {error}") from error
    if not isinstance(parsed, dict):
        raise SkillParseError("frontmatter must be a YAML object")

    name = sanitize_single_line(str(parsed.get("name") or default_skill_name(path)))
    description = sanitize_single_line(str(parsed.get("description") or ""))
    validate_required("name", name, MAX_NAME_LEN)
    validate_required("description", description, MAX_DESCRIPTION_LEN)
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    short_description = optional_str(
        metadata.get("short-description") if isinstance(metadata, dict) else None,
        MAX_DESCRIPTION_LEN,
    )
    extra = load_openai_metadata(path, warnings=metadata_warnings)

    return SkillMetadata(
        name=name,
        description=description,
        short_description=short_description,
        path=canonicalize(path),
        scope=scope,  # type: ignore[arg-type]
        interface=extra.get("interface"),
        dependencies=extra.get("dependencies"),
        policy=extra.get("policy") or SkillPolicy(),
    )


def load_openai_metadata(
    path: Path,
    *,
    warnings: list[SkillError] | None = None,
) -> dict[str, Any]:
    metadata_path = path.parent / "agents" / "openai.yaml"
    if not metadata_path.is_file():
        return {}
    try:
        parsed = parse_simple_yaml(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SkillParseError) as error:
        if warnings is not None:
            warnings.append(
                SkillError(
                    path=metadata_path,
                    message=f"Invalid agents/openai.yaml: {error}",
                    severity="warning",
                )
            )
        return {}
    if not isinstance(parsed, dict):
        if warnings is not None:
            warnings.append(
                SkillError(
                    path=metadata_path,
                    message="Invalid agents/openai.yaml: root must be a YAML object",
                    severity="warning",
                )
            )
        return {}
    return {
        "interface": resolve_interface(parsed.get("interface"), path.parent),
        "dependencies": resolve_dependencies(parsed.get("dependencies")),
        "policy": resolve_policy(parsed.get("policy")),
    }


def with_skill_identity(skill: SkillMetadata, root: SkillRoot) -> SkillMetadata:
    root_path = canonicalize(root.path)
    try:
        relative_dir = skill.path.parent.relative_to(root_path).as_posix() or "."
    except ValueError:
        relative_dir = skill.path.parent.name
    root_id = root.root_id or f"{root.scope}-default"
    identity = "\0".join((root.scope, root_id, relative_dir, skill.name))
    skill_id = f"skill_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    fingerprint = skill_fingerprint(skill.path)
    return replace(
        skill,
        skill_id=skill_id,
        root_id=root_id,
        relative_dir=relative_dir,
        fingerprint=fingerprint,
    )


def skill_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for candidate in (path, path.parent / "agents" / "openai.yaml"):
        try:
            contents = candidate.read_bytes()
        except OSError:
            continue
        digest.update(candidate.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(contents)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def resolve_interface(value: Any, skill_dir: Path) -> SkillInterface | None:
    if not isinstance(value, dict):
        return None
    interface = SkillInterface(
        display_name=optional_str(value.get("display_name"), MAX_NAME_LEN),
        short_description=optional_str(value.get("short_description"), MAX_DESCRIPTION_LEN),
        icon_small=resolve_asset_path(skill_dir, value.get("icon_small")),
        icon_large=resolve_asset_path(skill_dir, value.get("icon_large")),
        brand_color=resolve_color(value.get("brand_color")),
        default_prompt=optional_str(value.get("default_prompt"), MAX_DEFAULT_PROMPT_LEN),
    )
    if not any(
        (
            interface.display_name,
            interface.short_description,
            interface.icon_small,
            interface.icon_large,
            interface.brand_color,
            interface.default_prompt,
        )
    ):
        return None
    return interface


def resolve_dependencies(value: Any) -> SkillDependencies | None:
    if not isinstance(value, dict):
        return None
    raw_tools = value.get("tools")
    if not isinstance(raw_tools, list):
        return None
    tools: list[SkillToolDependency] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        dependency_type = optional_str(item.get("type"), MAX_NAME_LEN)
        if not dependency_type:
            continue
        tools.append(
            SkillToolDependency(
                type=dependency_type,
                value=optional_str(item.get("value"), MAX_DESCRIPTION_LEN),
                description=optional_str(item.get("description"), MAX_DESCRIPTION_LEN),
                query=optional_str(item.get("query"), MAX_DESCRIPTION_LEN),
                server=optional_str(item.get("server"), MAX_NAME_LEN),
                tool=optional_str(item.get("tool"), MAX_DESCRIPTION_LEN),
                read_only=item.get("read_only")
                if isinstance(item.get("read_only"), bool)
                else None,
            )
        )
    return SkillDependencies(tuple(tools)) if tools else None


def resolve_policy(value: Any) -> SkillPolicy | None:
    if not isinstance(value, dict):
        return None
    implicit = value.get("allow_implicit_invocation")
    modes = value.get("modes")
    parsed_modes: list[str] = []
    if isinstance(modes, list):
        parsed_modes = [mode for mode in modes if mode in {"act", "plan"}]
    return SkillPolicy(
        allow_implicit_invocation=implicit if isinstance(implicit, bool) else True,
        modes=tuple(parsed_modes) if parsed_modes else ("act", "plan"),  # type: ignore[arg-type]
    )


def resolve_asset_path(skill_dir: Path, value: Any) -> Path | None:
    text = optional_str(value, MAX_DESCRIPTION_LEN)
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return None
    normalized = Path()
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        normalized /= part
    if not normalized.parts or normalized.parts[0] != "assets":
        return None
    return canonicalize(skill_dir / normalized)


def resolve_color(value: Any) -> str | None:
    text = optional_str(value, 7)
    return text if text and _COLOR_RE.fullmatch(text) else None


def extract_frontmatter(contents: str) -> str | None:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    frontmatter: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(frontmatter)
        frontmatter.append(line)
    return None


def parse_simple_yaml(contents: str) -> Any:
    lines = yaml_lines(contents)
    if not lines:
        return {}
    value, index = parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise SkillParseError("unexpected trailing YAML content")
    return value


def yaml_lines(contents: str) -> list[tuple[int, str]]:
    parsed: list[tuple[int, str]] = []
    for raw_line in contents.splitlines():
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise SkillParseError("tabs are not supported in YAML indentation")
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            continue
        parsed.append((len(raw_line) - len(raw_line.lstrip(" ")), stripped))
    return parsed


def parse_block(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    if lines[index][1].startswith("- "):
        return parse_list(lines, index, indent)
    return parse_dict(lines, index, indent)


def parse_dict(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise SkillParseError("unexpected indentation")
        if text.startswith("- "):
            break
        key, raw_value = split_key_value(text)
        index += 1
        if raw_value == "":
            if index < len(lines) and lines[index][0] > line_indent:
                value, index = parse_block(lines, index, lines[index][0])
            else:
                value = {}
        else:
            value = parse_scalar(raw_value)
        result[key] = value
    return result, index


def parse_list(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or not text.startswith("- "):
            break
        item_text = text[2:].strip()
        index += 1
        if item_text == "":
            if index < len(lines) and lines[index][0] > line_indent:
                item, index = parse_block(lines, index, lines[index][0])
            else:
                item = None
        elif ":" in item_text:
            key, raw_value = split_key_value(item_text)
            item = {}
            if raw_value == "":
                if index < len(lines) and lines[index][0] > line_indent:
                    value, index = parse_block(lines, index, lines[index][0])
                else:
                    value = {}
            else:
                value = parse_scalar(raw_value)
            item[key] = value
            if index < len(lines) and lines[index][0] > line_indent:
                extra, index = parse_dict(lines, index, lines[index][0])
                item.update(extra)
        else:
            item = parse_scalar(item_text)
        result.append(item)
    return result, index


def split_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise SkillParseError("expected key: value")
    key, raw_value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise SkillParseError("empty key")
    return key, raw_value.strip()


def parse_scalar(value: str) -> Any:
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in split_inline_list(inner)]
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    return value


def split_inline_list(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char == ",":
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    items.append("".join(current))
    return items


def validate_required(field: str, value: str, max_len: int) -> None:
    if not value:
        raise SkillParseError(f"missing field `{field}`")
    if len(value) > max_len:
        raise SkillParseError(f"`{field}` exceeds maximum length of {max_len} characters")


def optional_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = sanitize_single_line(str(value))
    if not text or len(text) > max_len:
        return None
    return text


def sanitize_single_line(value: str) -> str:
    return " ".join(value.split())


def default_skill_name(path: Path) -> str:
    return sanitize_single_line(path.parent.name) or "skill"


def canonicalize(path: Path) -> Path:
    return path.expanduser().resolve()


def scope_rank(scope: str) -> int:
    return {
        "repo": 0,
        "user": 1,
        "packaged": 2,
        "extra": 3,
        "plugin": 4,
    }.get(scope, 99)
