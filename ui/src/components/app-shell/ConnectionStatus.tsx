/**
 * Status text the socket layer reports in English, shown in Chinese. The
 * hooks keep English because other code matches on it; only the label that
 * reaches the screen is translated here.
 */
const statusLabels: Record<string, string> = {
  streaming: "正在生成",
  queued: "已排队",
  steering: "正在插话",
  cancelling: "正在停止",
  cancelled: "已取消",
  interrupted: "已中断",
  "plan ready": "计划已就绪",
  "input withdrawn": "消息已撤回",
  "calling tool": "正在调用工具",
  "tool complete": "工具已完成",
  "no active run to steer": "没有可插话的任务",
  "could not create session": "无法创建会话",
  "invalid backend event": "收到无效的后端事件",
};

export function ConnectionStatus({ status }: { status: string }) {
  const value = status.toLowerCase();
  const translated = translateStatus(status, value);
  const offline = /(error|fail|closed|offline|disconnect(?!ing))/.test(value);
  const pending = /(connecting|loading|waiting|pending|starting)/.test(value);
  const connected =
    !offline && !pending && /(connected|ready|online|ok)/.test(value);
  const label = translated
    ? translated
    : offline
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

function translateStatus(status: string, value: string): string | undefined {
  const known = statusLabels[value];
  if (known) {
    return known;
  }
  const toolComplete = /^tool complete: (.+)$/i.exec(status);
  if (toolComplete) {
    return `工具已完成：${toolComplete[1]}`;
  }
  const tool = /^tool: (.+)$/i.exec(status);
  if (tool) {
    return `工具：${tool[1]}`;
  }
  return undefined;
}
