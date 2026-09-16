export function ConnectionStatus({ status }: { status: string }) {
  const value = status.toLowerCase();
  const offline = /(error|fail|closed|offline|disconnect(?!ing))/.test(value);
  const pending = /(connecting|loading|waiting|pending|starting)/.test(value);
  const connected =
    !offline && !pending && /(connected|ready|online|ok)/.test(value);
  const label = offline
    ? "连接不可用"
    : pending
      ? "正在连接"
      : connected
        ? "已连接"
        : status;
  const tone = offline
    ? "error"
    : pending
      ? "primary"
      : connected
        ? "success"
        : "neutral";
  return (
    <span
      className={`connection-status tone-${tone}`}
      title={status}
      role="status"
    >
      <span className="status-dot" />
      <span>{label}</span>
    </span>
  );
}
