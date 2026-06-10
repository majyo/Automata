import { Send } from "lucide-react";
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
      <button className="composer-submit" type="submit" aria-label="Send prompt" disabled={!canSend}>
        <Send size={18} />
      </button>
    </div>
  );
}
