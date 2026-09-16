import { ArrowUpRight, FolderOpen, Play, X } from "lucide-react";
import type { PersistedRunStatus } from "../../types/chat";
import type { PermissionPreset, SessionSummary } from "../../types/session";
import { formatDirectoryName } from "../../utils/format";
import { ConnectionStatus } from "./ConnectionStatus";

type InspectorSheetProps = {
  bridgeStatus: string;
  socketStatus: string;
  activeSession: SessionSummary | null;
  workingDirectory: string;
  messageCount: number;
  permissionPreset: PermissionPreset;
  runStatus?: PersistedRunStatus;
  open: boolean;
  modal: boolean;
  onRunBridgeCheck(): void;
  onClose(): void;
};

const runLabels: Record<PersistedRunStatus, string> = {
  queued: "排队中",
  running: "执行中",
  waiting_approval: "等待批准",
  cancelling: "正在停止",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export function InspectorSheet({
  bridgeStatus,
  socketStatus,
  activeSession,
  workingDirectory,
  messageCount,
  permissionPreset,
  runStatus,
  open,
  modal,
  onRunBridgeCheck,
  onClose,
}: InspectorSheetProps) {
  return (
    <aside
      className={`inspector-sheet ${open ? "open" : ""}`}
      aria-label="工作区概览"
      aria-hidden={!open}
      inert={!open}
      role={modal ? "dialog" : undefined}
      aria-modal={modal || undefined}
    >
      <div className="inspector-inner">
        <div className="inspector-header">
          <h2>工作区概览</h2>
          <button
            className="icon-button small"
            type="button"
            onClick={onClose}
            aria-label="关闭工作区概览"
          >
            <X size={17} />
          </button>
        </div>
        <div className="inspector-body">
          <div className="inspector-workspace">
            <span className="eyebrow">当前项目</span>
            <h3>
              {workingDirectory
                ? formatDirectoryName(workingDirectory)
                : "选择工作目录"}
            </h3>
            <p>
              {workingDirectory
                ? "会话将在此目录下读取文件与执行任务。"
                : "新建会话时选择要处理的项目文件夹。"}
            </p>
          </div>
          <div className="inspector-section-title">
            <span>会话信息</span>
            <ArrowUpRight size={14} />
          </div>
          <dl className="session-metadata">
            <div>
              <dt>消息记录</dt>
              <dd>
                {String(messageCount).padStart(2, "0")} <small>条</small>
              </dd>
            </div>
            <div>
              <dt>运行状态</dt>
              <dd>
                {runStatus
                  ? runLabels[runStatus]
                  : activeSession
                    ? "待命"
                    : "未开始"}
              </dd>
            </div>
            <div>
              <dt>工具权限</dt>
              <dd>
                {permissionPreset === "full_access" ? "完全访问" : "默认沙箱"}
              </dd>
            </div>
            <div>
              <dt>最近更新</dt>
              <dd>
                {activeSession
                  ? formatUpdatedAt(activeSession.updated_at)
                  : "—"}
              </dd>
            </div>
          </dl>
          <div className="directory-note">
            <FolderOpen size={15} />
            <span>{workingDirectory || "尚未指定目录"}</span>
          </div>
          <details className="connection-details">
            <summary>连接与诊断</summary>
            <div className="diagnostic-content">
              <ConnectionStatus status={socketStatus} />
              <p>{bridgeStatus}</p>
              <button
                className="button button-outlined"
                type="button"
                onClick={onRunBridgeCheck}
              >
                <Play size={13} />
                检查桌面连接
              </button>
            </div>
          </details>
          <div className="inspector-note">
            <span className="tiny-square" />
            <span>
              {permissionPreset === "full_access"
                ? "工具可直接访问本机文件与网络。"
                : "工具按当前沙箱与审批设置执行。"}
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
