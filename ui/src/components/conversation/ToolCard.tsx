import { useState } from "react";
import { Activity, CheckCircle2, ChevronDown, ChevronRight, Terminal, X } from "lucide-react";
import type { ChatMessage, ToolRunMetadata, ToolRunStatus } from "../../types/chat";
import { formatToolRunStatus } from "../../utils/format";

type ToolCardProps = {
  metadata: ToolRunMetadata | null;
};

type ToolRunGroupProps = {
  messages: ChatMessage[];
};

type ToolRunSummary = {
  id: string;
  tool: string;
  status: ToolRunStatus;
  argumentsText: string;
  argumentsDisplayText: string;
  resultText: string;
  resultDisplayText: string;
  liveOutputDisplayText: string;
  summaryText: string;
  result: ToolRunMetadata["result"];
};

const ARGUMENT_TEXT_KEYS = new Set(["content", "patch"]);
const RESULT_TEXT_KEYS = new Set(["stdout", "stderr", "content", "output", "diff", "patch"]);
const ARGUMENT_FIELD_ORDER = ["command", "cmd", "pattern", "path", "cwd", "workdir", "target", "file", "mode"];
const RESULT_FIELD_ORDER = [
  "ok",
  "matched",
  "exit_code",
  "timed_out",
  "command",
  "cmd",
  "cwd",
  "workdir",
  "path",
  "absolute_path",
  "tool",
  "mode",
  "syntax",
  "dry_run",
  "encoding",
  "size_bytes",
  "bytes_written",
  "stdout_truncated",
  "stderr_truncated",
  "error",
  "summary",
  "files",
  "attempts",
];

export function ToolCard({ metadata }: ToolCardProps) {
  return <ToolRunGroupContent messages={[toolMessageFromMetadata(metadata)]} />;
}

export function ToolRunGroup({ messages }: ToolRunGroupProps) {
  return (
    <article className="message tool tool-run-group">
      <ToolRunGroupContent messages={messages} />
    </article>
  );
}

function ToolRunGroupContent({ messages }: ToolRunGroupProps) {
  const [isListExpanded, setIsListExpanded] = useState(true);
  const summaries = messages.map((message) => summarizeToolRun(message.id, message.metadata ?? null));
  const status = groupStatus(summaries);
  const countLabel = `${summaries.length} tool call${summaries.length === 1 ? "" : "s"}`;

  return (
    <div className={`tool-run-group-shell ${status}`}>
      <button
        className="tool-run-group-header"
        type="button"
        aria-expanded={isListExpanded}
        onClick={() => setIsListExpanded((expanded) => !expanded)}
      >
        <span className="tool-run-group-title">
          <Terminal size={14} />
          <span>{groupHeaderLabel(summaries, status, countLabel)}</span>
        </span>
        {isListExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>

      {isListExpanded && (
        <div className="tool-run-lines">
          {summaries.map((summary) => (
            <ToolRunLine key={summary.id} summary={summary} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolRunLine({ summary }: { summary: ToolRunSummary }) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className={`tool-run-line ${summary.status}`}>
      <button
        className="tool-run-line-toggle"
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <span className="tool-run-line-icon" aria-hidden="true">
          {summary.status === "running" ? (
            <Activity size={13} />
          ) : summary.status === "failed" ? (
            <X size={13} />
          ) : (
            <CheckCircle2 size={13} />
          )}
        </span>
        <span className="tool-run-line-state">{lineState(summary.status)}</span>
        <span className="tool-run-line-text">{summary.summaryText}</span>
        {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>

      {isExpanded && (
        <div className="tool-run-line-detail">
          <div className="tool-run-detail-heading">
            <strong>{summary.tool}</strong>
            <span className={`tool-run-detail-status ${summary.status}`}>
              {formatToolRunStatus(summary.status)}
            </span>
          </div>
          <div className="tool-run-detail-grid">
            <ToolRunDetailBlock label="Arguments" value={summary.argumentsDisplayText || "{}"} />
            <ToolRunDetailBlock
              label="Result"
              value={
                summary.result
                  ? summary.resultDisplayText || "(empty)"
                  : summary.liveOutputDisplayText || "Running..."
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ToolRunDetailBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="tool-run-detail-block">
      <span>{label}</span>
      <pre>{value}</pre>
    </div>
  );
}

function summarizeToolRun(id: string, metadata: ToolRunMetadata | null): ToolRunSummary {
  const tool = metadata?.tool?.trim() || "unknown_tool";
  const argumentsText = metadata?.arguments ?? "{}";
  const result = metadata?.result ?? null;
  const status: ToolRunStatus = result ? (result.success === false ? "failed" : "completed") : "running";
  const resultText = result?.content ?? "";
  const commandText = extractReadableToolText(tool, argumentsText, resultText);
  const argumentsDisplayText = formatToolPayload(argumentsText, {
    fieldOrder: ARGUMENT_FIELD_ORDER,
    textKeys: ARGUMENT_TEXT_KEYS,
  });
  const resultDisplayText = formatToolPayload(resultText, {
    fieldOrder: RESULT_FIELD_ORDER,
    textKeys: RESULT_TEXT_KEYS,
  });
  const liveOutputDisplayText = formatLiveOutput(metadata);

  return {
    id,
    tool,
    status,
    argumentsText,
    argumentsDisplayText,
    resultText,
    resultDisplayText,
    liveOutputDisplayText,
    result,
    summaryText: commandText ? `${tool} ${commandText}` : tool,
  };
}

function formatLiveOutput(metadata: ToolRunMetadata | null): string {
  const output = metadata?.live_output;
  if (!output) {
    return "";
  }
  const sections = [
    output.stdout ? `stdout:\n${output.stdout}` : "",
    output.stderr ? `stderr:\n${output.stderr}` : "",
    output.truncated ? "(additional live output was truncated)" : "",
  ].filter(Boolean);
  return sections.join("\n\n");
}

function toolMessageFromMetadata(metadata: ToolRunMetadata | null): ChatMessage {
  return {
    id: metadata?.tool_call_id ?? "tool-run",
    role: "tool",
    text: "",
    kind: "tool_run",
    metadata,
  };
}

function groupStatus(summaries: ToolRunSummary[]): ToolRunStatus {
  if (summaries.some((summary) => summary.status === "running")) {
    return "running";
  }
  if (summaries.some((summary) => summary.status === "failed")) {
    return "failed";
  }
  return "completed";
}

function lineState(status: ToolRunStatus): string {
  if (status === "running") {
    return "Running";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "Ran";
}

function groupState(status: ToolRunStatus): string {
  return lineState(status);
}

function groupHeaderLabel(summaries: ToolRunSummary[], status: ToolRunStatus, countLabel: string): string {
  const running = summaries.filter((summary) => summary.status === "running").length;
  const failed = summaries.filter((summary) => summary.status === "failed").length;
  const completed = summaries.length - running - failed;
  const parts: string[] = [];
  if (running > 0) {
    parts.push(`${running} running`);
  }
  if (completed > 0) {
    parts.push(`${completed} succeeded`);
  }
  if (failed > 0) {
    parts.push(`${failed} failed`);
  }
  return `${groupState(status)} ${countLabel} · ${parts.join(" · ")}`;
}

function extractReadableToolText(tool: string, argumentsText: string, resultText: string): string {
  const args = asRecord(parseJson(argumentsText));
  const result = asRecord(parseJson(resultText));
  const resultCommand = getString(result, "command");
  const command = getString(args, "command") || resultCommand;

  if (command) {
    return truncateInline(command, 150);
  }

  if (tool === "rg" || tool === "grep") {
    const pattern = getString(args, "pattern");
    const path = getString(args, "path") || getString(args, "cwd");
    return truncateInline([pattern ? quoteForDisplay(pattern) : "", path].filter(Boolean).join(" "), 150);
  }

  const path =
    getString(args, "path") ||
    getString(args, "file") ||
    getString(args, "target") ||
    getString(args, "cwd") ||
    getString(args, "workdir");

  if (path) {
    return truncateInline(path, 150);
  }

  if (argumentsText.trim() && argumentsText.trim() !== "{}") {
    return truncateInline(argumentsText, 150);
  }

  return "";
}

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function formatToolPayload(
  rawText: string,
  options: { fieldOrder: string[]; textKeys: Set<string> },
): string {
  const trimmed = rawText.trim();
  if (!trimmed) {
    return "";
  }

  const parsed = parseJsonValue(rawText);
  if (!parsed.ok) {
    return rawText;
  }

  if (typeof parsed.value === "string") {
    return parsed.value;
  }

  const record = asRecord(parsed.value);
  if (record) {
    return formatRecordPayload(record, options);
  }

  return formatDisplayValue(parsed.value);
}

function formatRecordPayload(
  record: Record<string, unknown>,
  options: { fieldOrder: string[]; textKeys: Set<string> },
): string {
  const metadataLines: string[] = [];
  const textSections: string[] = [];
  const detailSections: string[] = [];

  for (const key of orderedKeys(record, options.fieldOrder)) {
    const value = record[key];
    if (!shouldDisplayValue(value)) {
      continue;
    }

    if (options.textKeys.has(key) && typeof value === "string") {
      textSections.push(formatSection(key, value));
      continue;
    }

    if (isInlineValue(value)) {
      metadataLines.push(`${key}: ${String(value)}`);
      continue;
    }

    if (typeof value === "string") {
      textSections.push(formatSection(key, value));
      continue;
    }

    detailSections.push(formatSection(key, formatDisplayValue(value)));
  }

  const sections = [
    metadataLines.length ? metadataLines.join("\n") : "",
    ...textSections,
    ...detailSections,
  ].filter(Boolean);

  return sections.join("\n\n") || formatDisplayValue(record);
}

function orderedKeys(record: Record<string, unknown>, preferredOrder: string[]): string[] {
  const keys = Object.keys(record);
  const preferred = preferredOrder.filter((key) => key in record);
  const remaining = keys.filter((key) => !preferredOrder.includes(key)).sort();
  return [...preferred, ...remaining];
}

function shouldDisplayValue(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false;
  }

  if (typeof value === "string") {
    return value.length > 0;
  }

  if (Array.isArray(value)) {
    return value.length > 0;
  }

  if (typeof value === "object") {
    return Object.keys(value).length > 0;
  }

  return true;
}

function isInlineValue(value: unknown): value is string | number | boolean {
  return (
    typeof value === "number" ||
    typeof value === "boolean" ||
    (typeof value === "string" && !value.includes("\n"))
  );
}

function formatSection(label: string, content: string): string {
  return `${label}:\n${content}`;
}

function formatDisplayValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (value === null) {
    return "null";
  }

  return JSON.stringify(value, null, 2);
}

function parseJsonValue(value: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(value) };
  } catch {
    return { ok: false };
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function getString(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function quoteForDisplay(value: string): string {
  return /\s/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
}

function truncateInline(value: string, maxLength: number): string {
  const inline = value.replace(/\s+/g, " ").trim();
  return inline.length > maxLength ? `${inline.slice(0, maxLength - 1)}...` : inline;
}
