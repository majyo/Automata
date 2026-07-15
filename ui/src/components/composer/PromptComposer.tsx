import { Send, Square } from "lucide-react";
import { SendModeToggle } from "./SendModeToggle";
import type { SendMode } from "../../types/chat";

type PromptComposerProps = {
  prompt: string;
  sendMode: SendMode;
  isStreaming: boolean;
  canSend: boolean;
  autoFocus?: boolean;
  draft?: boolean;
  onPromptChange(prompt: string): void;
  onSendModeChange(sendMode: SendMode): void;
  onCancel(): void;
};

export function PromptComposer({
  prompt,
  sendMode,
  isStreaming,
  canSend,
  autoFocus,
  draft,
  onPromptChange,
  onSendModeChange,
  onCancel,
}: PromptComposerProps) {
  return (
    <div className={`composer ${draft ? "draft" : ""}`}>
      <SendModeToggle sendMode={sendMode} disabled={isStreaming} onChange={onSendModeChange} />
      <input
        autoFocus={autoFocus}
        value={prompt}
        onChange={(event) => onPromptChange(event.currentTarget.value)}
        placeholder="Ask the local coding agent..."
      />
      <button
        className={`composer-submit ${isStreaming ? "stop" : ""}`}
        type={isStreaming ? "button" : "submit"}
        aria-label={isStreaming ? "Stop run" : "Send prompt"}
        disabled={isStreaming ? false : !canSend}
        onClick={isStreaming ? onCancel : undefined}
      >
        {isStreaming ? <Square size={16} fill="currentColor" /> : <Send size={18} />}
      </button>
    </div>
  );
}
