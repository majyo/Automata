import { Play } from "lucide-react";

type TopbarProps = {
  displayedWorkingDirectory: string;
  title: string;
  onRunBridgeCheck(): void;
};

export function Topbar({ displayedWorkingDirectory, title, onRunBridgeCheck }: TopbarProps) {
  return (
    <header className="topbar">
      <div>
        <span className="eyebrow workspace-eyebrow" title={displayedWorkingDirectory}>
          {displayedWorkingDirectory}
        </span>
        <h1>{title}</h1>
      </div>
      <button className="run-button" onClick={onRunBridgeCheck}>
        <Play size={17} />
        Run bridge check
      </button>
    </header>
  );
}
