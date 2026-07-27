import json
from dataclasses import replace
from typing import Any

from automata_api.agent.execution.approval import ApprovalBroker
from automata_api.agent.execution.model import ToolExecutionContext
from automata_api.agent.execution.permissions import (
    DEFAULT_PERMISSION_PRESET,
    CompiledPermissionProfile,
    PermissionPreset,
    compile_permission_profile,
    permissions_for_preset,
)
from automata_api.agent.execution.policy import ToolPolicyEngine
from automata_api.agent.execution.process import process_execution_scope
from automata_api.agent.execution.tool_output import tool_output_execution_scope
from automata_api.agent.tools._core import ToolResult, parse_tool_arguments
from automata_api.agent.tools.router import ToolRouter
from automata_api.observability import observe_span


class ToolExecutionOrchestrator:
    def __init__(
        self,
        *,
        approval_broker: ApprovalBroker,
        policy: ToolPolicyEngine | None = None,
        permission_preset: PermissionPreset = DEFAULT_PERMISSION_PRESET,
        permission_profile: CompiledPermissionProfile | None = None,
    ) -> None:
        self.approval_broker = approval_broker
        self.policy = policy or ToolPolicyEngine()
        self.permissions = permissions_for_preset(permission_preset)
        self.permission_profile = permission_profile

    async def execute(
        self,
        *,
        router: ToolRouter,
        tool_name: str,
        raw_arguments: str | dict[str, Any] | None,
        context: ToolExecutionContext,
    ) -> ToolResult:
        context.cancellation.raise_if_cancelled()
        arguments, parse_error = parse_tool_arguments(raw_arguments)
        if parse_error:
            return failed_result(tool_name, arguments, "invalid_tool_arguments", parse_error)

        descriptor = router.execution_descriptor(tool_name, mode=context.mode)
        if descriptor is None:
            async with observe_span(
                "tool.execute",
                attributes={"tool": tool_name, "authorized": False},
            ):
                return await router.dispatch(
                    tool_name, arguments, mode=context.mode
                )

        async with observe_span(
            "tool.policy.evaluate",
            attributes={
                "tool": tool_name,
                **tool_operation_attributes(tool_name, arguments),
            },
        ) as policy_span:
            decision = self.policy.evaluate(
                descriptor=descriptor,
                arguments=arguments,
                mode=context.mode,
            )
            policy_span.set_attributes(
                action=decision.action,
                risk=decision.risk,
                permission_preset=self.permissions.preset,
                approval_policy=self.permissions.approval_policy,
            )
        if decision.action == "deny":
            return failed_result(tool_name, arguments, decision.reason, decision.reason)
        if (
            decision.action == "prompt"
            and self.permissions.approval_policy == "never"
        ):
            decision = replace(
                decision,
                action="allow",
                reason="approval_policy_never",
                approval_scope=None,
                allow_for_run=False,
            )
        if decision.action == "prompt":
            async with observe_span(
                "tool.approval.wait",
                attributes={
                    "tool": tool_name,
                    "risk": decision.risk,
                },
            ) as approval_span:
                approval = await self.approval_broker.request(
                    tool=tool_name,
                    tool_identity=descriptor.identity
                    or f"{descriptor.source}:{descriptor.name}",
                    arguments=arguments,
                    decision=decision,
                    context=context,
                )
                approval_span.set_attributes(decision=approval)
            if approval == "deny":
                return failed_result(
                    tool_name,
                    arguments,
                    "tool_approval_denied",
                    "User denied this tool call.",
                )

        profile = self.permission_profile or compile_permission_profile(
            self.permissions.preset,
            workspace=context.workspace,
        )
        result = await self._dispatch_authorized(
            router=router,
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            source=descriptor.source,
            profile=profile,
            attempt=1,
        )
        if (
            result.error_code != "sandbox_denied"
            or profile.sandbox_enforcement == "disabled"
        ):
            return result
        if profile.deny_read_paths:
            await emit_context_event(
                context,
                {
                    "type": "sandbox_retry_blocked",
                    "tool_call_id": context.tool_call_id,
                    "profile_hash": profile.profile_hash,
                    "reason": "deny_read_profile",
                },
            )
            return result

        await emit_context_event(
            context,
            {
                "type": "sandbox_retry_requested",
                "tool_call_id": context.tool_call_id,
                "profile_hash": profile.profile_hash,
            },
        )
        retry_decision = replace(
            decision,
            action="prompt",
            reason="sandbox_denied_requires_unsandboxed_retry",
            approval_scope=None,
            allow_for_run=False,
        )
        approval = await self.approval_broker.request(
            tool=tool_name,
            tool_identity=descriptor.identity
            or f"{descriptor.source}:{descriptor.name}",
            arguments=arguments,
            decision=retry_decision,
            context=context,
        )
        if approval == "deny":
            await emit_context_event(
                context,
                {
                    "type": "sandbox_retry_resolved",
                    "tool_call_id": context.tool_call_id,
                    "decision": "deny",
                },
            )
            return result

        retry_profile = compile_permission_profile(
            "full_access",
            workspace=context.workspace,
        )
        await emit_context_event(
            context,
            {
                "type": "sandbox_retry_started",
                "tool_call_id": context.tool_call_id,
                "attempt": 2,
                "profile_hash": retry_profile.profile_hash,
            },
        )
        return await self._dispatch_authorized(
            router=router,
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            source=descriptor.source,
            profile=retry_profile,
            attempt=2,
        )

    async def _dispatch_authorized(
        self,
        *,
        router: ToolRouter,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        source: str,
        profile: CompiledPermissionProfile,
        attempt: int,
    ) -> ToolResult:
        context.cancellation.raise_if_cancelled()
        with process_execution_scope(
            context.run_id,
            context.tool_call_id,
            session_id=context.session_id,
            workspace=context.workspace,
            permission_profile=profile,
            emit_event=context.emit_event,
            sandbox_attempt=attempt,
        ):
            with tool_output_execution_scope(
                tool_call_id=context.tool_call_id,
                tool=tool_name,
                emit=context.emit_event,
            ):
                async with observe_span(
                    "tool.execute",
                    attributes={
                        "tool": tool_name,
                        "source": source,
                        "sandbox_attempt": attempt,
                        **tool_operation_attributes(
                            tool_name,
                            arguments,
                        ),
                    },
                ) as execution_span:
                    result = await router.dispatch_authorized(
                        tool_name,
                        arguments,
                        mode=context.mode,
                    )
                    execution_span.set_attributes(
                        **tool_result_attributes(tool_name, result)
                    )
                    return result


def failed_result(
    tool: str,
    arguments: dict[str, Any],
    error: str,
    message: str,
) -> ToolResult:
    return ToolResult(
        name=tool,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "ok": False,
                "tool": tool,
                "error": error,
                "message": message,
            },
            ensure_ascii=True,
        ),
        success=False,
    )


async def emit_context_event(
    context: ToolExecutionContext,
    payload: dict[str, Any],
) -> None:
    if context.emit_event is not None:
        await context.emit_event(payload)


def tool_operation_attributes(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name != "rg":
        return {}
    mode = arguments.get("mode", "search")
    return {
        "operation_mode": (
            mode if mode in {"search", "files"} else "invalid"
        )
    }


def tool_result_attributes(
    tool_name: str,
    result: ToolResult,
) -> dict[str, Any]:
    if tool_name != "rg":
        return {}
    try:
        payload = json.loads(result.content)
    except (json.JSONDecodeError, TypeError):
        return {}
    if payload.get("mode") != "files":
        return {}

    attributes: dict[str, Any] = {}
    engine = payload.get("engine")
    if isinstance(engine, str):
        attributes["engine"] = engine
    count = payload.get("count")
    if isinstance(count, int) and not isinstance(count, bool):
        attributes["file_count"] = count
    for name in ("truncated", "degraded"):
        value = payload.get(name)
        if isinstance(value, bool):
            attributes[name] = value
    return attributes
