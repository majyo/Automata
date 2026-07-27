import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from automata_api.agent.execution.model import (
    ApprovalDecision,
    ApprovalRequest,
    CancellationToken,
    RunCancelledError,
    ToolExecutionContext,
    ToolPolicyDecision,
)

ApprovalEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class ApprovalResolutionError(ValueError):
    pass


class ApprovalBroker:
    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        emit: ApprovalEmitter,
        cancellation: CancellationToken,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self._emit = emit
        self._cancellation = cancellation
        self._timeout_seconds = timeout_seconds
        self._pending: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._requests: dict[str, ApprovalRequest] = {}
        self._run_grants: set[str] = set()

    async def request(
        self,
        *,
        tool: str,
        tool_identity: str,
        arguments: dict[str, Any],
        decision: ToolPolicyDecision,
        context: ToolExecutionContext,
    ) -> ApprovalDecision:
        self._cancellation.raise_if_cancelled()
        if decision.approval_scope and decision.approval_scope in self._run_grants:
            return "allow_for_run"

        approval_id = uuid.uuid4().hex
        options: tuple[ApprovalDecision, ...] = (
            ("allow_once", "allow_for_run", "deny")
            if decision.allow_for_run and decision.approval_scope
            else ("allow_once", "deny")
        )
        request = ApprovalRequest(
            approval_id=approval_id,
            run_id=self.run_id,
            session_id=self.session_id,
            tool_call_id=context.tool_call_id,
            tool=tool,
            tool_identity=tool_identity,
            risk=decision.risk,
            reason=decision.reason,
            summary=approval_summary(tool, arguments, decision.risk),
            preview=approval_preview(tool, arguments, context.workspace),
            arguments_hash=canonical_arguments_hash(arguments),
            scope=decision.approval_scope,
            options=options,
            created_at=datetime.now(UTC).isoformat(),
        )
        future: asyncio.Future[ApprovalDecision] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[approval_id] = future
        self._requests[approval_id] = request
        await self._emit(
            {
                "type": "tool_approval_required",
                "session_id": request.session_id,
                "run_id": request.run_id,
                "approval_id": request.approval_id,
                "tool_call_id": request.tool_call_id,
                "tool": request.tool,
                "risk": request.risk,
                "reason": request.reason,
                "summary": request.summary,
                "preview": request.preview,
                "options": list(request.options),
            }
        )

        cancel_task = asyncio.create_task(self._cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {future, cancel_task},
                timeout=self._timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                raise RunCancelledError(self._cancellation.reason)
            if future not in done:
                if not future.done():
                    future.cancel()
                await self._emit(
                    {
                        "type": "tool_approval_resolved",
                        "session_id": self.session_id,
                        "run_id": self.run_id,
                        "approval_id": approval_id,
                        "decision": "deny",
                        "reason": "approval_expired",
                    }
                )
                return "deny"
            resolved = future.result()
            if request.arguments_hash != canonical_arguments_hash(arguments):
                await self._emit(
                    {
                        "type": "tool_approval_resolved",
                        "session_id": self.session_id,
                        "run_id": self.run_id,
                        "approval_id": approval_id,
                        "decision": "deny",
                        "reason": "approval_arguments_changed",
                    }
                )
                return "deny"
            if (
                resolved == "allow_for_run"
                and request.scope
                and "allow_for_run" in request.options
            ):
                self._run_grants.add(request.scope)
            await self._emit(
                {
                    "type": "tool_approval_resolved",
                    "session_id": self.session_id,
                    "run_id": self.run_id,
                    "approval_id": approval_id,
                    "decision": resolved,
                }
            )
            return resolved
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            self._pending.pop(approval_id, None)
            self._requests.pop(approval_id, None)

    def resolve(
        self,
        *,
        run_id: str,
        approval_id: str,
        decision: str,
    ) -> None:
        if run_id != self.run_id:
            raise ApprovalResolutionError("approval_run_mismatch")
        if decision not in {"allow_once", "allow_for_run", "deny"}:
            raise ApprovalResolutionError("approval_decision_invalid")
        future = self._pending.get(approval_id)
        request = self._requests.get(approval_id)
        if future is None or request is None:
            raise ApprovalResolutionError("approval_not_found")
        if future.done():
            raise ApprovalResolutionError("approval_already_resolved")
        if decision not in request.options:
            raise ApprovalResolutionError("approval_decision_not_available")
        future.set_result(decision)

    def cancel_all(self) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.cancel()


def approval_summary(tool: str, arguments: dict[str, Any], risk: str) -> str:
    if tool == "exec_command":
        return f"Run a {arguments.get('shell', 'bash')} command"
    if tool == "write_stdin":
        return "Write to a running process stdin"
    if tool in {"run_bash", "run_powershell"}:
        return f"Run a {tool.removeprefix('run_')} command"
    if tool == "write_file":
        return f"Write workspace file {arguments.get('path', '')}"
    if tool == "apply_patch":
        return "Delete workspace files" if risk == "destructive" else "Modify workspace files"
    if tool.startswith("mcp__"):
        return f"Call external MCP tool {tool}"
    return f"Run tool {tool}"


def canonical_arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_preview(
    tool: str, arguments: dict[str, Any], workspace: str
) -> dict[str, Any]:
    if tool == "exec_command":
        return {
            "shell": arguments.get("shell", "bash"),
            "cwd": arguments.get("workdir", workspace),
            "command": clipped(arguments.get("cmd")),
        }
    if tool == "write_stdin":
        chars = arguments.get("chars")
        return {
            "session_id": arguments.get("session_id", ""),
            "chars": clipped(chars),
            "chars_count": len(chars) if isinstance(chars, str) else 0,
        }
    if tool in {"run_bash", "run_powershell"}:
        return {
            "cwd": arguments.get("cwd", workspace),
            "command": clipped(arguments.get("command")),
        }
    if tool == "write_file":
        content = arguments.get("content")
        return {
            "path": arguments.get("path", ""),
            "mode": arguments.get("mode", "overwrite"),
            "content_chars": len(content) if isinstance(content, str) else 0,
        }
    if tool == "apply_patch":
        patch = arguments.get("patch")
        return {
            "patch": clipped(patch, 4000),
            "destructive": isinstance(patch, str)
            and ("*** Delete File:" in patch or "+++ /dev/null" in patch),
        }
    return {key: redact_value(key, value) for key, value in arguments.items()}


def clipped(value: Any, limit: int = 2000) -> str:
    if not isinstance(value, str):
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... [truncated]"


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(item in lowered for item in ("token", "secret", "password", "authorization")):
        return "[redacted]"
    if isinstance(value, str):
        return clipped(value)
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(key, item) for item in value[:32]]
    return value
